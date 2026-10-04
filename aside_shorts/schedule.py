"""예약 — 유튜브처럼 날짜·시각을 박아 둔다.

두 길:
  native  YouTube Studio 예약 공개로 넘긴다(PC 를 꺼도 공개됨). `youtube.native_schedule: true`(기본)
  queue   aside 가 들고 있다가 시각이 되면 올린다(패널/서버가 켜져 있어야 함).
업로드는 즉시 하고 공개만 그 시각에 — native 가 기본이다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from . import config
from .job import Job, all_jobs

FMT = "%Y-%m-%d %H:%M"


def parse_when(text: str) -> datetime:
    text = text.strip().replace("T", " ")
    for f in (FMT, "%Y-%m-%d %H:%M:%S", "%m-%d %H:%M"):
        try:
            d = datetime.strptime(text, f)
            if f == "%m-%d %H:%M":
                d = d.replace(year=datetime.now().year)
            return d
        except ValueError:
            pass
    raise SystemExit(f"시각 형식은 2026-10-04 08:30 꼴입니다: {text}")


def taken(account: Optional[str] = None) -> List[datetime]:
    out = []
    for job in all_jobs():
        for st in job.state().values():
            sc = st.get("scheduled") or {}
            if sc.get("when") and (account is None or sc.get("account") == account):
                out.append(parse_when(sc["when"]))
    return out


def next_slots(n: int, account: Optional[str] = None, start: Optional[datetime] = None) -> List[datetime]:
    """하루 1~2회(설정 youtube.slots) 중 비어 있는 다음 자리 n 개."""
    slots = config.load()["youtube"]["slots"] or ["08:30"]
    busy = {d.strftime(FMT) for d in taken(account)}
    cur = (start or datetime.now()) + timedelta(minutes=15)
    day = cur.replace(hour=0, minute=0, second=0, microsecond=0)
    out: List[datetime] = []
    for _ in range(400):
        for hm in slots:
            h, m = map(int, hm.split(":"))
            d = day.replace(hour=h, minute=m)
            if d >= cur and d.strftime(FMT) not in busy:
                out.append(d)
                if len(out) >= n:
                    return out
        day += timedelta(days=1)
    return out


def due(now: Optional[datetime] = None) -> List[Dict]:
    """queue 모드에서 시각이 된 것들."""
    now = now or datetime.now()
    out = []
    for job in all_jobs():
        for data_id, st in job.state().items():
            sc = st.get("scheduled") or {}
            if sc.get("mode") == "queue" and not st.get("posted") and not st.get("error") \
                    and parse_when(sc["when"]) <= now:
                out.append({"job": job.name, "data_id": data_id, "account": sc.get("account")})
    return out


def enqueue(job: Job, data_id: str, when: datetime, account: str, mode: str = "queue") -> Dict:
    return job.update_state(data_id, {"scheduled": {"when": when.strftime(FMT), "account": account,
                                                    "mode": mode}, "error": None})
