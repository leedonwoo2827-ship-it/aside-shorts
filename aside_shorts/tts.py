"""TTS — SuperTonic3(영상공방 VoiceWright) **전용**. 다른 엔진으로 넘어가지 않는다.

shorts-studio `shortsmaker/tts.py` 의 VoiceWright 브리지만 가져왔다(edge-tts 폴백은 뺐다).
영상공방 폴더의 venv 파이썬으로 `voicewright.engine.Engine.synth` 를 부르고, 결과 WAV(16bit mono)를 받는다.
영상공방 폴더: 설정 tts.bridge_dir → 환경변수 LLM_BRIDGE_DIR → D:\\00work\\260604-od-lmimg-supoer3-mp4
목소리: F1~F5 / M1~M5.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import List

from . import config

DEFAULT_BRIDGE = r"D:\00work\260604-od-lmimg-supoer3-mp4"
VOICES = ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"]
_SENT = "\x1eRESP\x1e"


class TTSUnavailable(RuntimeError):
    pass


def bridge_dir() -> Path:
    return Path(config.load()["tts"].get("bridge_dir") or os.environ.get("LLM_BRIDGE_DIR") or DEFAULT_BRIDGE)


def bridge_python():
    d = bridge_dir()
    for c in (d / "venv" / "Scripts" / "python.exe", d / "venv" / "bin" / "python",
              d / ".venv" / "Scripts" / "python.exe", d / ".venv" / "bin" / "python"):
        if c.is_file():
            return str(c)
    return None


def available() -> bool:
    return (bridge_dir() / "voicewright").is_dir() and bridge_python() is not None


_VW_SCRIPT = (
    "import asyncio, json, sys\n"
    "from voicewright.engine import Engine\n"
    "from voicewright.audio_io import write_wav\n"
    "data = json.loads(sys.stdin.read())\n"
    "async def main():\n"
    "    eng = await Engine.get()\n"
    "    out = []\n"
    "    for i, line in enumerate(data['lines']):\n"
    "        t = (line or '').strip() or '...'\n"
    "        wav = await eng.synth(t, voice_code=data['voice'], speed=data['speed'])\n"
    "        p = data['out_dir'] + '/line_%02d.wav' % i\n"
    "        write_wav(p, wav, eng.sample_rate)\n"
    "        out.append({'index': i, 'file': p, 'duration': len(wav)/float(eng.sample_rate)})\n"
    "    return out\n"
    "res = asyncio.run(main())\n"
    "sys.stdout.write('\\x1eRESP\\x1e'); sys.stdout.write(json.dumps(res))\n"
)


def synth_lines(lines: List[str], out_dir: Path, *, voice: str, speed: float) -> List[dict]:
    """각 줄 → out_dir/line_NN.wav. [{index,file,duration}]."""
    if not available():
        raise TTSUnavailable(f"SuperTonic3(VoiceWright)를 찾지 못했어요: {bridge_dir()} "
                             "— aside.config.local.json 의 tts.bridge_dir 를 영상공방 폴더로 맞춰 주세요")
    if voice.upper() not in VOICES:
        raise TTSUnavailable(f"목소리 코드는 {', '.join(VOICES)} 중 하나예요: {voice}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    d = bridge_dir()
    payload = json.dumps({"lines": list(lines), "voice": voice.upper(), "speed": float(speed),
                          "out_dir": out_dir.resolve().as_posix()})
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(PYTHONPATH=str(d), PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    timeout = float(config.load()["tts"]["timeout_sec"])
    try:
        proc = subprocess.run([bridge_python(), "-c", _VW_SCRIPT], input=payload, cwd=str(d),
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=env, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise TTSUnavailable(f"음성 만들기 시간 초과({timeout:.0f}초)")
    if _SENT in (proc.stdout or ""):
        return json.loads(proc.stdout.split(_SENT, 1)[1].strip())
    raise TTSUnavailable(f"SuperTonic3 실패: {(proc.stderr or proc.stdout or '').strip()[-400:]}")
