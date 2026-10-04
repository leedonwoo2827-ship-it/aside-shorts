"""aside-shorts 패널 서버 — 127.0.0.1 전용. 화면 오른쪽 좁은 앱창이 이 서버를 본다.

무거운 일(Claude·SuperTonic3·Playwright)은 전부 `python -m aside_shorts …` 하위 프로세스로 돌리고 로그만 읽는다
(lecture-composer 방식). 한 번에 하나만 돈다 — 할당량과 브라우저를 두 일이 다투지 않게.
예약 대기열은 30초마다 확인해서 시각이 된 것을 하나씩 올린다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .. import accounts, config, schedule, styles, tts
from ..job import Job, all_jobs
from ..log import log

STATIC = Path(__file__).parent / "static"
ALLOWED = {"make", "all", "yt-meta", "s0-rewrite", "s1-script", "s2-tts", "s3-images", "s4-motion", "s5-render", "post", "plan", "queue",
           "login", "probe", "doctor", "assets", "tts-setup", "claude-login"}

app = FastAPI(title="aside-shorts")


# ── 하위 프로세스 하나 ────────────────────────────────────────────────────────
def label(args: List[str]) -> str:
    """로그 창에 보일 일 이름 — 명령어 대신 쉬운 말."""
    cmd = args[0] if args else ""
    if cmd == "post":
        return "미리 채워 보기" if "--dry-run" in args else ("예약 넣기" if "--at" in args else "올리기")
    names = {"make": "딸깍 만들기", "all": "밤샘 0→5", "yt-meta": "유튜브 문구 쓰기", "s0-rewrite": "안전 개작", "s1-script": "대본 쓰기", "s2-tts": "목소리 입히기",
             "s3-images": "그림 그리기", "s4-motion": "모션 짜기", "s5-render": "영상 굽기",
             "queue": "예약 시간 확인", "plan": "예약 자동 배치", "login": "로그인 창 열기",
             "probe": "업로드 창 구조 확인", "doctor": "점검", "assets": "글꼴·GSAP 받기", "tts-setup": "SuperTonic3 받기",
             "claude-login": "Claude 로그인"}
    return names.get(cmd, cmd)


class Runner:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.lines: List[str] = []
        self.cmd: List[str] = []
        self.code: Optional[int] = None
        self.lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, args: List[str]) -> None:
        with self.lock:
            if self.busy:
                raise HTTPException(409, "지금 다른 일을 하는 중이에요. 끝나면 다시 눌러 주세요.")
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            self.cmd = args
            self.code = None
            self.lines.append(f"▶ {label(args)}")
            from ..log import detail
            detail(f"$ aside_shorts {' '.join(args)}")
            self.proc = subprocess.Popen(
                [child_python(), "-m", "aside_shorts", *args], cwd=str(config.ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            self.lines.append(line.rstrip("\n"))
            if len(self.lines) > 3000:
                del self.lines[:1000]
        self.code = proc.wait()
        if getattr(self, "stopped", False):
            self.code = -1
            self.stopped = False
        else:
            self.lines.append("✓ 다 됐어요" if self.code == 0 else "✗ 중간에 멈췄어요 — 바로 위 줄을 확인해 주세요")

    def stop(self) -> None:
        if self.busy:
            self.stopped = True
            self.lines.append("■ 멈췄어요. 이미 된 건 남아 있고, 다시 누르면 남은 것만 이어서 해요.")
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                self.proc.terminate()


RUN = Runner()


def _scheduler() -> None:
    while True:
        time.sleep(30)
        try:
            if not RUN.busy and schedule.due():
                RUN.start(["queue"])
        except Exception as e:      # noqa: BLE001 — 대기열 확인이 서버를 죽이면 안 된다
            from ..log import detail
            detail(f"대기열 확인 실패: {e}")


# ── 보기 ─────────────────────────────────────────────────────────────────────
def _job(name: str) -> Job:
    job = Job(name)
    if not job.exists():
        raise HTTPException(404, f"원고 묶음을 찾을 수 없어요: {name}")
    return job


def _status(job: Job, sid: str, st: Dict[str, Any]) -> str:
    if (st.get("scheduled") or {}).get("when") and (st.get("scheduled") or {}).get("mode") == "native":
        return "scheduled"
    if st.get("posted"):
        return "posted"
    if (st.get("scheduled") or {}).get("when"):
        return "scheduled"
    if st.get("error"):
        return "error"
    if job.video(sid).exists():
        return "ready"
    if (job.sub("motion", sid) / "index.html").exists():
        return "motion"
    return "script"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """제작 대시보드(넓은 웹앱)."""
    return (STATIC / "dashboard.html").read_text(encoding="utf-8")


@app.get("/panel", response_class=HTMLResponse)
def panel_page() -> str:
    """업로드용 사이드 패널(오른쪽 도킹) — 왼쪽 Studio Chrome 과 나란히."""
    return (STATIC / "panel.html").read_text(encoding="utf-8")


@app.get("/api/state")
def state() -> Dict[str, Any]:
    loc = config.local()
    return {
        "jobs": [{"name": j.name, "title": j.get("title", j.name)} for j in all_jobs()],
        "accounts": accounts.all_(),
        "current": loc.get("current"),
        "slots": config.load()["youtube"]["slots"],
        "native": bool(config.load()["youtube"].get("native_schedule")),
        "busy": RUN.busy, "cmd": RUN.cmd, "app": "aside-shorts",
        "styles": [{"name": n, "label": styles.load(n).get("label", n)} for n in styles.names()],
        "voices": tts.VOICES,
    }


@app.get("/api/claude")
def claude_status() -> Dict[str, Any]:
    """Claude 구독 로그인 상태(OAuth) — 패널 맨 위에 보인다."""
    from ..llm import claude_cli
    return claude_cli.auth_status()


@app.get("/api/tools")
def tools_status() -> Dict[str, Any]:
    """Claude(대본·모션)·SuperTonic3(목소리) — 설치 여부만 빠르게(로그인 확인은 「점검하기」)."""
    from ..llm import claude_cli
    return {"claude": bool(claude_cli.exe()), "tts": tts.available(), "tts_dir": str(tts.assets_dir())}


@app.get("/api/log")
def get_log(since: int = 0) -> Dict[str, Any]:
    total = len(RUN.lines)
    since = min(max(0, since), total)
    return {"lines": RUN.lines[since:], "next": total, "busy": RUN.busy, "code": RUN.code, "cmd": RUN.cmd,
            "label": label(RUN.cmd)}


class RunBody(BaseModel):
    args: List[str]


@app.post("/api/run")
def run(body: RunBody) -> Dict[str, Any]:
    if not body.args or body.args[0] not in ALLOWED:
        raise HTTPException(400, "허용되지 않은 명령")
    RUN.start(body.args)
    return {"ok": True}


@app.post("/api/stop")
def stop() -> Dict[str, Any]:
    RUN.stop()
    return {"ok": True}


def _title(job: Job, sid: str) -> str:
    sh = job.short(sid) or {}
    return f"{sid} {sh.get('perspective', '')}".strip()


@app.get("/api/jobs/{name}/shorts")
def shorts(name: str) -> Dict[str, Any]:
    job = _job(name)
    st_all = job.state()
    out = []
    for sid in job.ids():
        sh = job.short(sid) or {}
        st = st_all.get(sid) or {}
        n_img = len(list(job.sub("images", sid).glob("[0-9][0-9].png"))) if job.sub("images", sid).exists() else 0
        timing = config.read_json(job.sub("audio", sid) / "timing.json") or {}
        mmeta = config.read_json(job.sub("motion", sid) / "meta.json") or {}
        out.append({
            "duration": timing.get("duration"), "lines": len(sh.get("lines") or []),
            "motion_problems": mmeta.get("problems") or [],
            "overridden": job.overridden(sid), "bundle": job.bundle(sid).relative_to(job.dir).as_posix(),
            "id": sid, "perspective": sh.get("perspective", ""), "hook": sh.get("hook") or {},
            "status": _status(job, sid, st),
            "steps": {"audio": (job.sub("audio", sid) / "narration.wav").exists(),
                      "images": f"{n_img}/{len(sh.get('images') or [])}",
                      "motion": (job.sub("motion", sid) / "index.html").exists(),
                      "video": job.video(sid).exists()},
            "thumb": job.url(job.video(sid).with_suffix(".jpg")) if job.video(sid).with_suffix(".jpg").exists() else "",
            "scheduled": st.get("scheduled"), "posted": st.get("posted"), "error": st.get("error"),
        })
    return {"docs": [_unit_row(job, u, out) for u in job.units],
            "shorts": out, "style": job.setting("shorts", "style"), "voice": job.setting("tts", "voice"),
            "speed": job.setting("tts", "speed"), "title": job.get("title", name), "busy_cmd": RUN.cmd if RUN.busy else []}


def _unit_row(job: Job, u, out) -> Dict[str, Any]:
    rep = config.read_json(u.dir / "02_rewrite" / "검증.json") or {}
    return {"prefix": u.prefix, "chapter": u.chapter, "section": u.section, "dir": u.name,
            "raw": u.raw.name if u.raw else "", "rewrite": u.rewrite.name if u.rewrite else "",
            "chars": len(u.text), "shorts": sum(1 for o in out if o["id"].startswith(u.prefix + "-")),
            "check": {"passed": rep.get("passed"), "problems": rep.get("problems") or [],
                      "sim": rep.get("example_sim_max"), "swaps": rep.get("swaps") or []} if rep else None,
            # 이전 버전이 만든 개작본(검증.json 없음)인지, 가져온 개작본인지
            "file": (u.rewrite or u.raw).name if (u.rewrite or u.raw) else ""}


@app.get("/api/jobs/{name}/units/{prefix}/compare")
def unit_compare(name: str, prefix: str) -> Dict[str, Any]:
    """원본·개작본 문단 나란히 + 검증 결과 — 「새 작업·원고」에서 개작을 눈으로 확인."""
    from ..s0_rewrite import paragraphs
    job = _job(name)
    u = job.unit(prefix)
    if not u:
        raise HTTPException(404)
    raw = [t for t in paragraphs(u.raw) if t.strip()] if u.raw and u.raw.suffix == ".docx" else []
    new = [t for t in paragraphs(u.rewrite) if t.strip()] if u.rewrite and u.rewrite.suffix == ".docx" else []
    return {"raw": raw, "rewrite": new, "check": config.read_json(u.dir / "02_rewrite" / "검증.json") or None,
            "dir": u.name}


# ── 설정 ─────────────────────────────────────────────────────────────────────
EDITABLE = {   # 화면에서 고칠 수 있는 전체 설정 (section → keys)
    "claude": ["script_model", "image_model", "motion_model", "effort", "see_images", "fix_rounds", "limit_wait_hours", "limit_poll_min"],
    "shorts": ["target_seconds", "lines_min", "lines_max", "images_min", "images_max", "style"],
    "tts": ["voice", "speed"],
    "youtube": ["native_schedule", "visibility", "slots"],
}
LOCAL_CFG = config.ROOT / "aside.config.local.json"


@app.get("/api/settings")
def get_settings() -> Dict[str, Any]:
    cfg = config.load()
    return {"values": {sec: {k: cfg[sec].get(k) for k in keys} for sec, keys in EDITABLE.items()},
            "local": config.read_json(LOCAL_CFG, {}) or {}, "styles": styles.names(), "voices": tts.VOICES,
            "tts_dir": str(tts.assets_dir()), "tts_ok": tts.available()}


class Settings(BaseModel):
    values: Dict[str, Dict[str, Any]]


@app.put("/api/settings")
def put_settings(body: Settings) -> Dict[str, Any]:
    """이 PC 에만 적용(aside.config.local.json) — 회사 PC 와 집 PC 가 달라도 된다."""
    cur = config.read_json(LOCAL_CFG, {}) or {}
    base = config.deep_merge(config.DEFAULTS, config.read_json(config.ROOT / "aside.config.json", {}) or {})
    for sec, vals in body.values.items():
        if sec not in EDITABLE:
            raise HTTPException(400, f"고칠 수 없는 칸: {sec}")
        for k, v in vals.items():
            if k not in EDITABLE[sec]:
                raise HTTPException(400, f"고칠 수 없는 칸: {sec}.{k}")
            if sec == "shorts" and k == "style" and v not in styles.names():
                raise HTTPException(400, f"없는 스타일: {v}")
            if sec == "tts" and k == "voice" and str(v).upper() not in tts.VOICES:
                raise HTTPException(400, f"없는 목소리: {v}")
            if v == base.get(sec, {}).get(k):
                cur.get(sec, {}).pop(k, None)       # 기본값과 같으면 덮어쓰기에서 뺀다
            else:
                cur.setdefault(sec, {})[k] = v
    cur = {k: v for k, v in cur.items() if v}
    config.write_json(LOCAL_CFG, cur)
    return {"ok": True, "tts_ok": tts.available(), "tts_dir": str(tts.assets_dir())}


@app.get("/api/jobs/{name}/meta")
def get_meta(name: str) -> Dict[str, Any]:
    return config.read_json(_job(name).dir / "job.json", {}) or {}


@app.put("/api/jobs/{name}/meta")
def put_meta(name: str, body: Dict[str, Any]) -> Dict[str, Any]:
    job = _job(name)
    st = (body.get("shorts") or {}).get("style")
    if st and st not in styles.names():
        raise HTTPException(400, f"없는 스타일: {st}")
    v = (body.get("tts") or {}).get("voice")
    if v and str(v).upper() not in tts.VOICES:
        raise HTTPException(400, f"없는 목소리: {v}")
    config.write_json(job.dir / "job.json", body)
    return {"ok": True}


@app.post("/api/jobs/{name}/upload-docx")
def upload_docx(name: str, body: Dict[str, str]) -> Dict[str, Any]:
    """끌어다 놓은 원고를 단원 폴더의 01_raw(원본, 기본) 또는 02_rewrite(이미 개작본)에 저장.
    body = {filename, data_url, stage: raw|rewrite}"""
    import base64
    import tempfile
    from ..manuscript import place
    job = _job(name)
    fn = Path(body.get("filename", "")).name
    if not fn or Path(fn).suffix.lower() not in (".docx", ".txt", ".md") or fn.startswith("~$"):
        raise HTTPException(400, "원고는 .docx(또는 .txt/.md) 파일이에요.")
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / fn
        f.write_bytes(base64.b64decode(body.get("data_url", "").partition(",")[2]))
        out = place(job.dir, f, "rewrite" if body.get("stage") == "rewrite" else "raw", len(job.units) + 1)
    return {"ok": True, "file": fn, "unit": out.parent.parent.name, "stage": out.parent.name}


@app.delete("/api/jobs/{name}/units/{prefix}/rewrite")
def delete_rewrite(name: str, prefix: str) -> Dict[str, Any]:
    """개작본만 지운다(원본 01_raw 는 그대로) — 「0 안전 개작」을 다시 하고 싶을 때."""
    import shutil
    u = _job(name).unit(prefix)
    if not u or not u.raw:
        raise HTTPException(400, "원본(01_raw)이 없는 단원은 개작본을 지울 수 없어요(다시 만들 재료가 없음).")
    shutil.rmtree(u.dir / "02_rewrite", ignore_errors=True)
    return {"ok": True}


@app.post("/api/panel")
def open_panel_api() -> Dict[str, Any]:
    """업로드용 사이드 패널 띄우기(대시보드 「업로드 패널 열기」)."""
    port = int(config.local().get("ui_port") or config.load()["ui"]["port"])
    threading.Thread(target=_open_panel, args=(port,), daemon=True).start()
    return {"ok": True}


@app.get("/api/jobs/{name}/shorts/{sid}")
def short_detail(name: str, sid: str) -> Dict[str, Any]:
    job = _job(name)
    sh = job.short(sid)
    if not sh:
        raise HTTPException(404)
    from ..s3_images import plan as img_plan
    from ..youtube import meta as yt_meta
    v = job.video(sid)
    stamp = int(v.stat().st_mtime) if v.exists() else 0
    timing = config.read_json(job.sub("audio", sid) / "timing.json") or {}
    meta = config.read_json(job.sub("motion", sid) / "meta.json") or {}
    um = yt_meta(sh)
    return {
        "id": sid, "short": sh, "overridden": job.overridden(sid), "bundle": job.bundle(sid).relative_to(job.dir).as_posix(),
        "duration": timing.get("duration"),
        "video": f"{job.url(v)}?v={stamp}" if v.exists() else "",
        "motion": job.url(job.sub("motion", sid) / "index.html") if (job.sub("motion", sid) / "index.html").exists() else "",
        "motion_problems": meta.get("problems") or [],
        "images": [{"file": it["file"], "role": it["role"], "subject": it.get("subject", ""), "prompt": it["_full"],
                    "size": it["size"], "has": (job.sub("images", sid) / it["file"]).exists(),
                    "url": job.url(job.sub("images", sid) / it["file"]) if (job.sub("images", sid) / it["file"]).exists() else ""}
                   for it in img_plan(job, sid)],
        "upload": um, "yt_check": ((sh.get("youtube") or {}).get("check")),
        "state": job.state().get(sid) or {},
        "next_slot": schedule.next_slots(1, config.local().get("current"))[0].strftime("%Y-%m-%dT%H:%M"),
    }


class Patch(BaseModel):
    patch: Dict[str, Any]
    then: List[str] = []        # 저장 뒤 이어서 돌릴 단계 (예: ["s2-tts","s4-motion","s5-render"])


@app.put("/api/jobs/{name}/shorts/{sid}")
def save_short(name: str, sid: str, body: Patch) -> Dict[str, Any]:
    job = _job(name)
    if not job.raw_short(sid):
        raise HTTPException(400, "대본이 아직 없어요. 먼저 「딸깍 만들기」를 눌러 주세요.")
    job.save_override(sid, body.patch)
    if body.then and not RUN.busy:
        RUN.start(["make", "--job", name, "--only", sid])
    return {"ok": True}


@app.delete("/api/jobs/{name}/shorts/{sid}/override")
def reset_short(name: str, sid: str) -> Dict[str, Any]:
    _job(name).reset_override(sid)
    return {"ok": True}


def _safe(base: Path, rest: str) -> Path:
    base = base.resolve()
    p = (base / rest).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404)
    return p


@app.get("/jobs/{rest:path}")
def job_files(rest: str):
    """작업 폴더 파일 — 모션 페이지(상대 경로로 그림·음성·../templates 를 부른다)·영상·썸네일."""
    return FileResponse(_safe(config.JOBS, rest), headers={"Cache-Control": "no-store"})


@app.get("/templates/{rest:path}")
def template_files(rest: str):
    return FileResponse(_safe(config.TEMPLATES, rest))


class Upload(BaseModel):
    data_url: str


@app.post("/api/jobs/{name}/images/{sid}/{file}")
def put_image(name: str, sid: str, file: str, body: Upload) -> Dict[str, Any]:
    """사람이 만든 그림 넣기(ChatGPT 앱 등). 넣은 뒤 「모션·영상 다시」를 누르면 반영된다."""
    import base64
    import re as _re
    if not _re.fullmatch(r"\d\d\.png", file):
        raise HTTPException(400)
    job = _job(name)
    if not job.raw_short(sid):
        raise HTTPException(404)
    head, _, b64 = body.data_url.partition(",")
    if "image/" not in head:
        raise HTTPException(400, "그림 파일이 아니에요.")
    raw = base64.b64decode(b64)
    d = job.sub("images", sid)
    d.mkdir(parents=True, exist_ok=True)
    out = d / file
    if "image/png" in head:
        out.write_bytes(raw)
    else:       # jpg·webp → png (브라우저로 다시 찍는다)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page()
            pg.set_content(f'<img id=i src="{body.data_url}" style="display:block">')
            pg.locator("#i").screenshot(path=str(out), omit_background=True)
            b.close()
    baked = config.read_json(d / "baked.json", {}) or {}
    baked[file] = "manual"
    config.write_json(d / "baked.json", baked)
    return {"ok": True}


class Pick(BaseModel):
    kind: str = "folder"      # folder | files
    path: str = ""            # 선택 창 대신 직접 입력한 폴더 경로(선택 창을 못 띄우는 PC)


@app.post("/api/pick")
def pick(body: Pick) -> Dict[str, Any]:
    """Windows 기본 선택 창을 띄워 경로를 받는다(브라우저는 실제 경로를 못 준다)."""
    import json as _json
    if body.kind not in ("folder", "files"):
        raise HTTPException(400)
    if body.path:
        d = Path(body.path.strip().strip('"')).expanduser()
        if not d.is_dir():
            raise HTTPException(400, f"폴더를 찾을 수 없어요: {d}")
        from ..manuscript import classify_sources
        return {"folder": str(d), "files": [str(i["file"]) for i in classify_sources(d)]}
    r = subprocess.run([child_python(), "-m", "aside_shorts.pick", body.kind], cwd=str(config.ROOT),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                       capture_output=True, text=True, encoding="utf-8", timeout=600,
                       env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    if r.returncode != 0:
        raise HTTPException(501, "선택 창을 띄우지 못했습니다(리눅스는 python3-tk 필요). 폴더 경로를 직접 입력하세요.")
    return _json.loads(r.stdout.strip().splitlines()[-1])


class NewJob(BaseModel):
    name: str
    src: str = ""
    files: List[str] = []
    title: str = ""
    style: str = ""
    voice: str = ""
    rewritten: bool = False


@app.post("/api/jobs")
def new_job(body: NewJob) -> Dict[str, Any]:
    from ..cli import main
    if Job(body.name).exists():
        raise HTTPException(400, f"「{body.name}」 이름이 이미 있어요. 다른 이름을 써 주세요.")
    args = ["new", body.name] + (["--files", *body.files] if body.files else ["--from", body.src])
    for k in ("title", "style", "voice"):
        if getattr(body, k):
            args += [f"--{k}", getattr(body, k)]
    if body.rewritten:
        args.append("--rewritten")
    try:
        main(args)
    except SystemExit as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/jobs/{name}")
def delete_job(name: str) -> Dict[str, Any]:
    """작업 폴더를 통째로 지운다(복사본·문구·그림·카드·게시 기록). 원본 원고 폴더는 건드리지 않는다."""
    import shutil
    job = _job(name)
    if name == "example":
        raise HTTPException(400, "example 은 지우지 않습니다")
    if RUN.busy:
        raise HTTPException(409, "지금 다른 일을 하는 중이에요. 끝나면 다시 눌러 주세요.")
    pending = [k for k, v in job.state().items()
               if (v.get("scheduled") or {}).get("mode") == "queue" and not v.get("posted")]
    if pending:
        raise HTTPException(400, f"예약이 {len(pending)}건 걸려 있어요. 예약 탭에서 먼저 취소해 주세요.")
    shutil.rmtree(job.dir)
    return {"ok": True}


# ── 계정 ─────────────────────────────────────────────────────────────────────
class NewAcc(BaseModel):
    name: str
    label: str = ""


@app.post("/api/accounts")
def add_account(body: NewAcc) -> Dict[str, Any]:
    try:
        return accounts.add(body.name, body.label)
    except SystemExit as e:
        raise HTTPException(400, str(e))


@app.post("/api/accounts/{name}/use")
def use_account(name: str) -> Dict[str, Any]:
    accounts.set_current(name)
    return {"ok": True}


@app.post("/api/accounts/{name}/login")
def login(name: str) -> Dict[str, Any]:
    from .. import youtube as threads
    acc = accounts.get(name)
    if not threads.ensure_chrome(acc, threads.HOME):     # 이미 떠 있으면 앞으로 가져와 다시 붙인다
        try:
            threads.place(int(acc["port"]), "left")
        except Exception:
            pass
    return {"ok": True}


@app.get("/api/accounts/{name}/status")
def account_status(name: str) -> Dict[str, Any]:
    from .. import youtube as threads
    if RUN.busy and "post" in RUN.cmd[:1]:
        return {"chrome": True, "logged_in": None, "note": "게시 중"}
    try:
        return threads.status(accounts.get(name))
    except Exception as e:      # noqa: BLE001
        return {"chrome": False, "logged_in": None, "note": str(e)[:120]}


# ── 예약 ─────────────────────────────────────────────────────────────────────
class Sched(BaseModel):
    job: str
    data_id: str
    when: str
    account: str
    native: bool = False


@app.post("/api/schedule")
def add_schedule(body: Sched) -> Dict[str, Any]:
    job = _job(body.job)
    when = schedule.parse_when(body.when)
    if when < datetime.now():
        raise HTTPException(400, "이미 지난 시간이에요. 앞으로의 시간을 골라 주세요.")
    if body.native:
        RUN.start(["post", "--job", body.job, "--only", body.data_id, "--account", body.account,
                   "--at", when.strftime(schedule.FMT)])
        return {"ok": True, "mode": "native"}
    schedule.enqueue(job, body.data_id, when, body.account)
    return {"ok": True, "mode": "queue"}


@app.delete("/api/schedule/{name}/{data_id}")
def cancel_schedule(name: str, data_id: str) -> Dict[str, Any]:
    job = _job(name)
    st = job.state().get(data_id) or {}
    if (st.get("scheduled") or {}).get("mode") == "native":
        raise HTTPException(400, "YouTube 에 이미 예약 공개로 올라간 것입니다 — Studio 에서 공개 시간을 바꾸거나 취소하세요")
    job.update_state(data_id, {"scheduled": None, "error": None})
    return {"ok": True}


@app.get("/api/schedule")
def list_schedule() -> List[Dict[str, Any]]:
    out = []
    for job in all_jobs():
        for data_id, st in job.state().items():
            sc = st.get("scheduled") or {}
            if sc.get("when") or st.get("posted"):
                out.append({"job": job.name, "data_id": data_id, "title": _title(job, data_id),
                            "when": sc.get("when") or (st.get("posted") or {}).get("at", "")[:16].replace("T", " "),
                            "mode": sc.get("mode"), "account": sc.get("account") or (st.get("posted") or {}).get("account"),
                            "posted": bool(st.get("posted")), "url": (st.get("posted") or {}).get("url"),
                            "error": st.get("error")})
    return sorted(out, key=lambda r: r["when"])


class Plan(BaseModel):
    job: str
    account: str
    apply: bool = False


@app.post("/api/plan")
def plan(body: Plan) -> List[Dict[str, Any]]:
    """남은 쇼츠(영상 있음 · 미업로드 · 미예약)를 다음 빈 슬롯에. apply 면 실제로 건다(native 면 업로드까지)."""
    from ..cli import _ready
    job = _job(body.job)
    left = _ready(job)
    slots = schedule.next_slots(len(left), body.account)
    out = [{"data_id": sid, "title": _title(job, sid), "when": when.strftime(schedule.FMT)}
           for sid, when in zip(left, slots)]
    if body.apply and out:
        if config.load()["youtube"].get("native_schedule"):
            RUN.start(["plan", "--job", body.job, "--account", body.account, "--apply"])
        else:
            for sid, when in zip(left, slots):
                schedule.enqueue(job, sid, when, body.account)
    return out


# ── 띄우기 ───────────────────────────────────────────────────────────────────
def _open_panel(port: int) -> None:
    """오른쪽 좁은 앱창. 디버그 포트를 달아 두고, 뜬 뒤 화면 오른쪽 끝에 정확히 붙인다(threads.place)."""
    from ..youtube import chrome_path, pick_panel_port, place, port_open, screen, wait_port
    width = int(config.load()["ui"]["width"])
    url = f"http://127.0.0.1:{port}/panel"
    pport = pick_panel_port(port)
    if port_open(pport):          # 이미 떠 있는 패널 — 새로 띄우지 않고 앞으로 가져와 붙인다
        try:
            place(pport, "right")
        except Exception:
            pass
        return
    sw, sh = screen()
    try:
        subprocess.Popen([chrome_path(), f"--app={url}", f"--user-data-dir={config.PROFILES / '_panel'}",
                          f"--remote-debugging-port={pport}", "--no-first-run", "--no-default-browser-check",
                          f"--window-position={max(0, sw - width)},0", f"--window-size={width},{sh - 40}"])
    except SystemExit:
        webbrowser.open(url)
        return
    if wait_port(pport, 40):
        time.sleep(0.8)
        try:
            place(pport, "right")
        except Exception as e:      # noqa: BLE001
            log(f"패널 배치 실패(무시): {e}")


def _open_dashboard(port: int) -> None:
    """제작 대시보드 — 화면 가운데 넓은 앱창(Chrome --app). Chrome 이 없으면 기본 브라우저."""
    from ..youtube import chrome_path, free_port, screen
    url = f"http://127.0.0.1:{port}/"
    sw, sh = screen()
    w, h = min(1500, sw - 80), min(980, sh - 80)
    try:
        dport = free_port(int(config.load()["youtube"]["base_port"]) - 80)
        data = config.local()
        data["dash_port"] = dport
        config.save_local(data)
        subprocess.Popen([chrome_path(), f"--app={url}", f"--user-data-dir={config.PROFILES / '_dash'}",
                          f"--remote-debugging-port={dport}", "--no-first-run", "--no-default-browser-check",
                          f"--window-position={max(0, (sw - w) // 2)},{max(0, (sh - h) // 3)}",
                          f"--window-size={w},{h}"])
    except SystemExit:
        webbrowser.open(url)


class Arrange(BaseModel):
    account: str = ""


@app.post("/api/arrange")
def arrange_windows(body: Arrange) -> Dict[str, Any]:
    from .. import youtube as threads
    acc = None
    if body.account:
        try:
            acc = accounts.get(body.account)
        except SystemExit:
            acc = None
    if RUN.busy and RUN.cmd[:1] == ["post"]:
        raise HTTPException(409, "게시 중에는 창을 옮기지 않습니다")
    return {"done": threads.arrange(acc)}


@app.post("/api/quit")
def quit_app() -> Dict[str, Any]:
    """「프로그램 끄기」 — 패널 창과 서버를 함께 끈다. 걸어 둔 aside 예약은 다음에 켤 때 이어서 올라간다."""
    RUN.stop()

    def _bye():
        time.sleep(0.6)
        from ..youtube import panel_port, port_open
        for pp in (panel_port(), config.local().get("dash_port")):
            try:
                if pp and port_open(int(pp)):
                    urllib.request.urlopen(urllib.request.Request(
                        f"http://127.0.0.1:{pp}/json/close/" + _panel_target(int(pp)), method="PUT"), timeout=2)
            except Exception:
                pass
        os._exit(0)

    threading.Thread(target=_bye, daemon=True).start()
    return {"ok": True}


def _panel_target(pp: int) -> str:
    import json as _json
    tabs = _json.load(urllib.request.urlopen(f"http://127.0.0.1:{pp}/json/list", timeout=2))
    return next(t["id"] for t in tabs if t.get("type") == "page")


def _quiet_stdio() -> None:
    """검은 창 없이(pythonw) 뜨면 표준출력이 없다 — 로그를 파일로 돌린다."""
    if sys.stdout is None or sys.stderr is None:
        config.LOGS.mkdir(parents=True, exist_ok=True)
        f = open(config.LOGS / "server.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def _is_aside(port: int) -> bool:
    import json as _json
    try:
        return _json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1)).get("app") == "aside-shorts"
    except Exception:
        return False


def child_python() -> str:
    """하위 작업은 python.exe 로 — pythonw 는 출력이 없어 로그 창이 빈다(창은 CREATE_NO_WINDOW 로 숨김)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return sys.executable


def serve(window: bool = True) -> None:
    import uvicorn
    from ..youtube import port_open
    _quiet_stdio()
    want = int(os.environ.get("ASIDE_PORT") or config.load()["ui"]["port"])
    # ★ 이런 도구를 여러 개 쓰는 PC 는 포트가 자주 겹친다. 5291 부터 위로 보면서
    #   · aside-shorts 가 이미 켜져 있으면 → 패널만 다시 열고 끝(서버 둘이 예약을 두 번 올리면 안 된다)
    #   · 다른 프로그램이 쓰고 있으면 → 다음 번호로
    port = want
    for cand in range(want, want + 20):
        if not port_open(cand):
            port = cand
            break
        if _is_aside(cand):
            log(f"이미 켜져 있어요. 대시보드를 다시 열어요. (주소 http://127.0.0.1:{cand}/)")
            if window:
                _open_dashboard(cand)
            return
    else:
        raise SystemExit(f"{want}~{want + 19} 포트가 모두 쓰이고 있어요. 다른 프로그램을 몇 개 닫고 다시 켜 주세요.")
    if port != want:
        log(f"{want} 번은 다른 프로그램이 쓰고 있어서 {port} 번으로 켰어요.")
    data = config.local()
    data["ui_port"] = port
    config.save_local(data)
    threading.Thread(target=_scheduler, daemon=True).start()
    if window:
        threading.Timer(1.2, _open_dashboard, args=(port,)).start()
    log(f"aside-shorts 가 켜졌어요. 이 검은 창을 닫으면 예약이 멈춰요 (최소화는 괜찮아요). 대시보드: http://127.0.0.1:{port}/")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
