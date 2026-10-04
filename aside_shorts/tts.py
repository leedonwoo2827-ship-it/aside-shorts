"""TTS — SuperTonic3 **전용**, 이 프로젝트에 내장(aside_shorts/supertonic). 다른 엔진으로 넘어가지 않는다.

모델·목소리는 setup.bat(→ `run.bat tts-setup`)이 HuggingFace Supertone/supertonic-3 에서 assets/supertonic/ 로 받는다.
목소리: F1~F5 / M1~M5.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from .supertonic import engine as st

VOICES = st.VOICES


class TTSUnavailable(RuntimeError):
    pass


def assets_dir() -> Path:
    return st.assets_dir()


def available() -> bool:
    if st.missing():
        return False
    try:
        import onnxruntime  # noqa: F401
        import yaml  # noqa: F401
        return True
    except Exception:
        return False


def synth_lines(lines: List[str], out_dir: Path, *, voice: str, speed: float) -> List[dict]:
    """각 줄 → out_dir/line_NN.wav. [{index,file,duration}]."""
    miss = st.missing()
    if miss:
        raise TTSUnavailable(f"SuperTonic3 모델이 없어요({len(miss)}개 빠짐) — setup.bat 을 다시 실행하거나 run.bat tts-setup")
    if voice.upper() not in VOICES:
        raise TTSUnavailable(f"목소리 코드는 {', '.join(VOICES)} 중 하나예요: {voice}")
    try:
        eng = st.Engine.get()
    except Exception as e:      # noqa: BLE001
        raise TTSUnavailable(f"SuperTonic3 를 불러오지 못했어요: {str(e)[:200]}")
    out_dir = Path(out_dir)
    out = []
    for i, line in enumerate(lines):
        wav = eng.synth((line or "").strip() or "...", voice=voice, speed=float(speed))
        p = out_dir / f"line_{i:02d}.wav"
        st.write_wav(p, wav, eng.sample_rate)
        out.append({"index": i, "file": str(p), "duration": len(wav) / float(eng.sample_rate)})
    return out
