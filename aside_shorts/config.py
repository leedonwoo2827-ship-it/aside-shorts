"""설정 — `aside.config.json` 위에 `aside.config.local.json`(PC별, git 제외)을 덮는다.

job 설정은 `jobs/<job>/job.json` 위에 `job.local.json` 을 덮는다(aside-threads 와 같은 규칙).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "jobs"
PROFILES = ROOT / "profiles"
LOGS = ROOT / "logs"
TEMPLATES = ROOT / "templates"
LOCAL = ROOT / "local.json"

DEFAULTS: Dict[str, Any] = {
    # 대본·그림(SVG)·모션 전부 Claude(구독 OAuth 로그인한 claude CLI) — API 키·ChatGPT 불필요
    "claude": {"script_model": "opus", "image_model": "opus", "motion_model": "opus", "effort": "high",
               "timeout_sec": 900, "retries": 1, "see_images": True, "fix_rounds": 2,
               "limit_wait_hours": 72, "limit_poll_min": 30},   # 한도에 걸리면 최대 72시간 기다렸다 이어서
    "shorts": {"target_seconds": 35, "lines_min": 6, "lines_max": 8, "images_min": 6, "images_max": 8,
               "style": "vox-retro"},
    # SuperTonic3 만 쓴다(내장 — assets/supertonic) — 다른 엔진으로 넘어가지 않는다
    "tts": {"voice": "F4", "speed": 1.1, "gap": 0.25, "lead": 0.4, "tail": 0.9,
            "timeout_sec": 900},
    "image": {"workers": 2},
    "render": {"w": 1080, "h": 1920, "fps": 30, "crf": 18, "preset": "medium", "jpeg_quality": 92},
    "youtube": {"chrome": "", "native_schedule": True, "visibility": "public",
                "slots": ["12:00", "19:00"], "base_port": 9361},
    "ui": {"port": 5293, "width": 460},
}


def deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise SystemExit(f"JSON 이 깨졌습니다: {path} — {e}")


def write_json(path: Path, data: Any) -> None:
    """원자적 쓰기 — 서버와 하위 프로세스가 같은 파일을 만진다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load() -> Dict[str, Any]:
    cfg = deep_merge(DEFAULTS, read_json(ROOT / "aside.config.json", {}) or {})
    return deep_merge(cfg, read_json(ROOT / "aside.config.local.json", {}) or {})


def local() -> Dict[str, Any]:
    return read_json(LOCAL, {}) or {}


def save_local(data: Dict[str, Any]) -> None:
    write_json(LOCAL, data)
