"""작업(job) 폴더 하나 = 시리즈 하나(예: 고등학교 통합사회1) = 단원(절) 폴더 여러 개.

jobs/<job>/
  job.json · job.local.json      시리즈 이름·스타일·목소리 (local 은 PC별 덮어쓰기)
  state.json                     업로드·예약 기록
  1-2_통합적 관점의 필요성과 적용/  ← 단원(절) 하나
    01_raw/       원본 docx (보관만)
    02_rewrite/   안전 개작 docx + 검증.json                 s0-rewrite
    03_script/    1-2-01.json …(Claude 원안) · 1-2-01.edit.json(사람 수정 — 언제나 이김) · _s1.json(캐시)
    04_bundle/
      1-2-01/     audio/ (s2) · images/ (s3) · motion/ (s4) · out/1-2-01.mp4·jpg (s5)
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .manuscript import Unit, units_in

_STATE_LOCK = threading.Lock()
BUNDLE_KINDS = ("audio", "images", "motion", "out")


@dataclass
class Job:
    name: str

    @property
    def dir(self) -> Path:
        return config.JOBS / self.name

    def exists(self) -> bool:
        return (self.dir / "job.json").exists()

    @cached_property
    def meta(self) -> Dict[str, Any]:
        base = config.read_json(self.dir / "job.json", {}) or {}
        return config.deep_merge(base, config.read_json(self.dir / "job.local.json", {}) or {})

    def get(self, key: str, default: Any = None) -> Any:
        cur: Any = self.meta
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def setting(self, section: str, key: str) -> Any:
        """job.json 의 같은 이름 칸이 전체 설정을 이긴다 (예: shorts.style, tts.voice)."""
        v = self.get(f"{section}.{key}")
        return v if v is not None else config.load()[section].get(key)

    # ── 단원 ─────────────────────────────────────────────────────────────────
    @property
    def units(self) -> List[Unit]:
        return units_in(self.dir)

    def unit(self, prefix: str) -> Optional[Unit]:
        return next((u for u in self.units if u.prefix == prefix), None)

    def unit_of(self, sid: str) -> Unit:
        u = self.unit(sid.rsplit("-", 1)[0])
        if not u:
            raise SystemExit(f"{sid}: 단원 폴더를 찾지 못했어요")
        return u

    # ── 쇼츠 ─────────────────────────────────────────────────────────────────
    def ids(self) -> List[str]:
        out = []
        for u in self.units:
            if u.script_dir.exists():
                out += [p.stem for p in u.script_dir.glob("*.json")
                        if not p.stem.startswith("_") and not p.stem.endswith(".edit")]
        return sorted(out, key=_id_key)

    def pick(self, only: Optional[List[str]] = None) -> List[str]:
        ids = self.ids()
        if not only:
            return ids
        want = set(only)
        got = [i for i in ids if i in want or any(i.startswith(w + "-") for w in want)]
        if not got:
            raise SystemExit(f"--only 에 맞는 쇼츠가 없습니다: {', '.join(only)} (있는 것: {', '.join(ids) or '없음'})")
        return got

    def script_path(self, sid: str) -> Path:
        return self.unit_of(sid).script_dir / f"{sid}.json"

    def edit_path(self, sid: str) -> Path:
        return self.unit_of(sid).script_dir / f"{sid}.edit.json"

    def raw_short(self, sid: str) -> Optional[Dict[str, Any]]:
        return config.read_json(self.script_path(sid))

    def short(self, sid: str) -> Optional[Dict[str, Any]]:
        """s1 결과 위에 사람의 손편집을 덮은 것 — 다른 모든 곳은 이것만 읽는다."""
        base = self.raw_short(sid)
        if base is None:
            return None
        return config.deep_merge(base, config.read_json(self.edit_path(sid), {}) or {})

    def save_override(self, sid: str, patch: Dict[str, Any]) -> None:
        cur = config.read_json(self.edit_path(sid), {}) or {}
        config.write_json(self.edit_path(sid), config.deep_merge(cur, patch))

    def reset_override(self, sid: str) -> None:
        self.edit_path(sid).unlink(missing_ok=True)

    def overridden(self, sid: str) -> bool:
        return self.edit_path(sid).exists()

    # ── 번들 ─────────────────────────────────────────────────────────────────
    def bundle(self, sid: str) -> Path:
        return self.unit_of(sid).bundle_dir / sid

    def sub(self, kind: str, sid: str) -> Path:
        """쇼츠 한 편의 단계 폴더: audio · images · motion · out"""
        if kind not in BUNDLE_KINDS:
            raise ValueError(kind)
        return self.bundle(sid) / kind

    def video(self, sid: str) -> Path:
        return self.sub("out", sid) / f"{sid}.mp4"

    def url(self, path: Path) -> str:
        """패널 서버 주소 (/jobs/…)"""
        return "/jobs/" + path.resolve().relative_to(config.JOBS.resolve()).as_posix()

    # ── 업로드 상태 ──────────────────────────────────────────────────────────
    def state(self) -> Dict[str, Any]:
        return config.read_json(self.dir / "state.json", {}) or {}

    def update_state(self, sid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        """다시 읽고 고쳐 쓴다 — 서버(예약)와 하위 프로세스(업로드)가 같이 쓴다."""
        with _STATE_LOCK:
            st = self.state()
            cur = dict(st.get(sid) or {})
            for k, v in patch.items():
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = v
            st[sid] = cur
            config.write_json(self.dir / "state.json", st)
            return cur


def _id_key(sid: str):
    return [int(x) if x.isdigit() else x for x in sid.split("-")]


def all_jobs() -> List[Job]:
    if not config.JOBS.exists():
        return []
    return [Job(p.name) for p in sorted(config.JOBS.iterdir())
            if p.is_dir() and (p / "job.json").exists() and p.name != "example"]


def need(name: Optional[str]) -> Job:
    if not name:
        jobs = all_jobs()
        if len(jobs) == 1:
            return jobs[0]
        raise SystemExit("--job 을 지정하세요. 있는 작업: " + (", ".join(j.name for j in jobs) or "없음"))
    job = Job(name)
    if not job.exists():
        raise SystemExit(f"작업이 없습니다: jobs/{name}/job.json — `run.bat new {name} --from <원고폴더>`")
    return job
