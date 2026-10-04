"""SuperTonic3 TTS 엔진 — 이 프로젝트 안에서 바로 돈다(다른 폴더·프로그램 불필요).

영상공방 VoiceWright 엔진(voicewright/engine.py)을 가져와 동기식으로 줄였다:
  · 한국어 긴 문장 단어 누락 방지 — 문장/쉼표 단위로 잘라(최대 60자) 조각별 합성 후 이어 붙임
  · 끝 무음만 진폭으로 잘라냄(마지막 단어 잘림 방지)
  · 합성 직전 발음 변환(발음사전 pronunciation_map.yaml + 영문 약어 + 연도·숫자)
모델·목소리: assets/supertonic/onnx/*.onnx · voice_styles/{F1..F5,M1..M5}.json
  — `run.bat tts-setup`(setup.bat 이 부름)이 HuggingFace Supertone/supertonic-3 에서 내려받는다.
"""
from __future__ import annotations

import re
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .. import config

HF = "https://huggingface.co/Supertone/supertonic-3/resolve/main"
ONNX_FILES = ["duration_predictor.onnx", "text_encoder.onnx", "vector_estimator.onnx", "vocoder.onnx",
              "tts.json", "unicode_indexer.json"]
VOICES = ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"]
PMAP = Path(__file__).parent / "pronunciation_map.yaml"

_SENT_END_RE = re.compile(r"(?<=[.!?。！？…])\s+")
_COMMA_RE = re.compile(r"(?<=[,，、])\s+")
_TTS_MAX_CHARS = 60
_INTER_PIECE_SILENCE_SEC = 0.18
TOTAL_STEP = 8


def assets_dir() -> Path:
    return config.ROOT / "assets" / "supertonic"


def missing() -> List[str]:
    d = assets_dir()
    out = [f"onnx/{f}" for f in ONNX_FILES if not (d / "onnx" / f).exists()]
    out += [f"voice_styles/{v}.json" for v in VOICES if not (d / "voice_styles" / f"{v}.json").exists()]
    return out


def download(log=print, force: bool = False) -> None:
    """모델(약 380MB)·목소리 10종을 받는다. 있는 파일은 건너뛴다."""
    d = assets_dir()
    items = [(f"onnx/{f}", f"{HF}/onnx/{f}") for f in ONNX_FILES] + \
            [(f"voice_styles/{v}.json", f"{HF}/voice_styles/{v}.json") for v in VOICES]
    for rel, url in items:
        p = d / rel
        if p.exists() and p.stat().st_size > 0 and not force:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        log(f"  받는 중: {rel}")
        tmp = p.with_suffix(p.suffix + ".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(p)


def _split_for_tts(text: str, max_chars: int = _TTS_MAX_CHARS) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    out: List[str] = []
    for sent in _SENT_END_RE.split(text):
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) <= max_chars:
            out.append(sent)
            continue
        cur = ""
        for p in (x.strip() for x in _COMMA_RE.split(sent) if x.strip()):
            if not cur:
                cur = p
            elif len(cur) + 1 + len(p) <= max_chars:
                cur = cur + " " + p
            else:
                out.append(cur)
                cur = p
        if cur:
            out.append(cur)
    return out


def _providers() -> List[str]:
    try:
        import onnxruntime as ort
        avail = set(ort.get_available_providers())
    except Exception:
        avail = set()
    chosen = [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider") if p in avail]
    return chosen + ["CPUExecutionProvider"]


class Engine:
    _instance: Optional["Engine"] = None

    def __init__(self) -> None:
        from .helper import load_text_to_speech_with_providers
        from .pronunciation import load_pronunciation_map
        miss = missing()
        if miss:
            raise RuntimeError(f"SuperTonic3 모델 파일이 없습니다({len(miss)}개) — setup.bat 또는 run.bat tts-setup")
        self._tts = load_text_to_speech_with_providers(str(assets_dir() / "onnx"), _providers())
        self.sample_rate = int(self._tts.sample_rate)
        self._styles: Dict[str, object] = {}
        self._pmap = load_pronunciation_map(PMAP)

    @classmethod
    def get(cls) -> "Engine":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _style(self, code: str):
        from .helper import load_voice_style
        code = code.upper()
        if code not in VOICES:
            raise ValueError(f"목소리 코드는 {', '.join(VOICES)} 중 하나: {code}")
        if code not in self._styles:
            self._styles[code] = load_voice_style([str(assets_dir() / "voice_styles" / f"{code}.json")])
        return self._styles[code]

    def _trim(self, wav: np.ndarray, dur: np.ndarray) -> np.ndarray:
        full = wav[0] if wav.ndim == 2 else wav
        if full.size == 0:
            return full
        nz = np.where(np.abs(full) > 0.01)[0]
        if len(nz) == 0:
            n = int(self.sample_rate * float(dur[0]))
            return full[: max(0, min(n, full.shape[-1]))]
        return full[: min(int(nz[-1]) + int(self.sample_rate * 0.15), full.shape[-1])]

    def synth(self, text: str, *, voice: str, speed: float = 1.0, lang: str = "ko") -> np.ndarray:
        text = self._pmap.apply(text, spell_unknown_acronyms=True, convert_years=True)
        style = self._style(voice)
        pieces = _split_for_tts(text)
        if not pieces:
            return np.zeros(0, dtype=np.float32)
        gap = np.zeros(int(self.sample_rate * _INTER_PIECE_SILENCE_SEC), dtype=np.float32)
        parts: List[np.ndarray] = []
        for i, piece in enumerate(pieces):
            wav, dur = self._tts(piece, lang, style, TOTAL_STEP, speed)
            parts.append(self._trim(wav, dur))
            if i < len(pieces) - 1:
                parts.append(gap)
        return np.concatenate(parts).astype(np.float32)


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    """16bit mono WAV (표준 라이브러리 wave 로 — 추가 패키지 없이)."""
    import wave
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
