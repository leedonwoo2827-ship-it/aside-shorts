"""Claude 사용 한도(5시간 창·주간 한도)에 걸리면 **기다렸다가 이어서** 한다 — 밤새·며칠 무인 제작용.

lecture-composer `longform/llm_wait.py` 와 같은 방식:
    is_limit(text)   응답·오류가 한도/과부하 메시지인가
    reset_at(text)   메시지에서 재설정 시각(epoch) 읽기 — 「limit reached|1759…」, 「resets 3pm」, 「reset at 15:30」
    wait(text)       재설정 시각까지(모르면 claude.limit_poll_min(기본 30)분마다) 짧은 확인 질문으로 풀렸는지 본다.
                     최대 claude.limit_wait_hours(기본 72시간). 풀리면 True.
기다리는 동안 PC 절전을 막는다(keep_awake) — 절전에 들어가면 다음 날 아침에 멈춰 있다.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
import time
from typing import Optional

from .. import config
from ..log import detail, log

LIMIT_RE = re.compile(r"usage limit|limit reached|hour limit|weekly limit|rate limit|rate_limit|overloaded|"
                      r"too many requests|hit your limit|resets? (at )?\d|\b429\b|\b529\b", re.I)


def is_limit(text: str) -> bool:
    return bool(LIMIT_RE.search(text or ""))


def reset_at(text: str) -> Optional[float]:
    t = text or ""
    m = re.search(r"\|(\d{10})\b", t)                        # 「Claude AI usage limit reached|1759380000」
    if m:
        return float(m.group(1))
    m = re.search(r"reset[s]?\s*(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t, re.I)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        now = dt.datetime.now()
        r = now.replace(hour=h % 24, minute=mi, second=0, microsecond=0)
        if r <= now:
            r += dt.timedelta(days=1)
        return r.timestamp()
    return None


def keep_awake(on: bool = True) -> None:
    """Windows 절전 막기(작업 중에만). 다른 OS 는 무시."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


def _probe() -> str:
    """가장 싼 모델로 한 마디 — 풀렸으면 빈 문자열, 아니면 오류 글."""
    from .claude_cli import ClaudeError, _run
    try:
        _run("ok 라고만 답해", "짧게 답한다", model="haiku", timeout=180)
        return ""
    except ClaudeError as e:
        return str(e) or "error"


def wait(first_text: str = "") -> bool:
    """한도가 풀릴 때까지 기다린다. 풀리면 True, 최대 시간을 넘기면 False."""
    cfg = config.load()["claude"]
    max_h = float(cfg.get("limit_wait_hours", 72))
    poll = max(5.0, float(cfg.get("limit_poll_min", 30))) * 60
    if max_h <= 0:
        return False
    deadline = time.time() + max_h * 3600
    text = first_text
    keep_awake(True)
    while time.time() < deadline:
        ts = reset_at(text)
        if ts and time.time() < ts < deadline:
            left = ts - time.time() + 90
            nap = min(left, 3600)           # 리셋이 멀어도(주간 한도) 1시간마다 한 번은 확인 — 일찍 풀리면 바로 이어 간다
            log(f"⏸ Claude 사용 한도 — {dt.datetime.fromtimestamp(ts + 90):%m월 %d일 %H:%M} 재설정 예정"
                f" (약 {left / 60:.0f}분). {nap / 60:.0f}분 뒤 확인해요. 창은 켜 두세요.")
            time.sleep(nap)
        else:
            log(f"⏸ Claude 사용 한도(또는 과부하) — {poll / 60:.0f}분 뒤 다시 확인해요. 창은 켜 두세요.")
            time.sleep(poll)
        text = _probe()
        if not text:
            log("▶ Claude 한도가 풀렸어요 — 이어서 해요")
            return True
        detail(f"  한도 확인: {text[:200]}")
    log(f"✗ Claude 한도가 {max_h:.0f}시간 안에 풀리지 않았어요")
    return False
