"""s2-tts — 대본 문장을 SuperTonic3 로 읽히고, 한 줄로 이어 붙이며 **문장별 시작·끝 시각**을 적는다.

모션은 이 timing.json 에 맞춰 움직인다("음악이 아니라 목소리에 맞추는" 방식).
  audio/<id>/line_NN.wav     문장별 음성
  audio/<id>/narration.wav   lead 무음 + 문장들(사이 gap) + tail 무음
  audio/<id>/timing.json     {duration, cues:[{i,text,keywords,start,end}], hash}
캐시: say 문장·목소리·속도가 같으면 건너뛴다.
"""
from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path
from typing import Any, Dict, List

from . import config, tts
from .job import Job
from .log import detail, log


def _hash(says: List[str], voice: str, speed: float, c: Dict[str, Any]) -> str:
    key = json.dumps([says, voice, speed, c["gap"], c["lead"], c["tail"]], ensure_ascii=False)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def concat(files: List[Path], out: Path, gap: float, lead: float, tail: float) -> List[tuple]:
    """16bit mono WAV 들을 무음과 함께 이어 붙인다. [(start, end)] 반환(초)."""
    spans = []
    with wave.open(str(files[0]), "rb") as w0:
        rate, width, ch = w0.getframerate(), w0.getsampwidth(), w0.getnchannels()
    silence = lambda sec: b"\x00" * (int(rate * sec) * width * ch)   # noqa: E731
    with wave.open(str(out), "wb") as wo:
        wo.setnchannels(ch)
        wo.setsampwidth(width)
        wo.setframerate(rate)
        wo.writeframes(silence(lead))
        t = lead
        for i, f in enumerate(files):
            with wave.open(str(f), "rb") as wi:
                if wi.getframerate() != rate:
                    raise RuntimeError(f"샘플레이트가 다릅니다: {f}")
                n = wi.getnframes()
                wo.writeframes(wi.readframes(n))
            dur = n / rate
            spans.append((round(t, 3), round(t + dur, 3)))
            t += dur
            if i < len(files) - 1:
                wo.writeframes(silence(gap))
                t += gap
        wo.writeframes(silence(tail))
    return spans


def run(job: Job, only=None, force: bool = False, **_) -> None:
    c = config.load()["tts"]
    voice = job.setting("tts", "voice")
    speed = float(job.setting("tts", "speed"))
    ids = job.pick(only)
    if not ids:
        raise SystemExit("대본이 없습니다 — 먼저 s1-script")
    for sid in ids:
        sh = job.short(sid)
        lines = sh.get("lines") or []
        says = [(ln.get("say") or ln.get("text") or "").strip() for ln in lines]
        d = job.sub("audio", sid)
        h = _hash(says, voice, speed, c)
        old = config.read_json(d / "timing.json", {}) or {}
        if old.get("hash") == h and (d / "narration.wav").exists() and not force:
            detail(f"s2: {sid} 음성 그대로 — 건너뜀")
            continue
        log(f"  {sid} 목소리 입히는 중 ({voice}, 문장 {len(says)}개) …")
        res = tts.synth_lines(says, d, voice=voice, speed=speed)
        files = [Path(r["file"]) for r in sorted(res, key=lambda r: r["index"])]
        spans = concat(files, d / "narration.wav", float(c["gap"]), float(c["lead"]), float(c["tail"]))
        with wave.open(str(d / "narration.wav"), "rb") as w:
            total = w.getnframes() / w.getframerate()
        cues = [{"i": i, "text": ln.get("text", ""), "keywords": ln.get("keywords") or [],
                 "start": s, "end": e} for i, (ln, (s, e)) in enumerate(zip(lines, spans))]
        config.write_json(d / "timing.json", {"duration": round(total, 3), "voice": voice, "speed": speed,
                                              "cues": cues, "hash": h})
        log(f"  ✓ {sid} 음성 {total:.1f}초")
        if total > 58:
            log(f"  △ {sid} 가 {total:.0f}초예요. 쇼츠는 60초를 넘으면 안 돼요 — 대본을 줄이거나 속도를 올려 주세요.")
