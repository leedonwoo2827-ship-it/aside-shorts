"""s5-render — 완성 페이지를 프레임마다 찍어 mp4 로 굽고 음성을 입힌다.

Playwright(Chromium, 헤드리스) 1080×1920 → 프레임 t 마다 `__seek(t)` → JPEG 스크린샷 → ffmpeg 표준입력.
타임라인을 멈춰 두고 시각을 직접 찍으므로 PC 가 느려도 프레임이 빠지지 않는다(결정론).
  out/<id>.mp4   H.264 + AAC, 30fps, yuv420p, faststart
  out/<id>.jpg   썸네일(마지막 문장 무렵 한 장)
캐시: 페이지·장면·음성·그림이 그대로면 건너뛴다.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from pathlib import Path
from typing import List

from . import config
from .job import Job
from .log import detail, log

ARGS = ["--allow-file-access-from-files", "--disable-web-security", "--font-render-hinting=none",
        "--force-color-profile=srgb", "--hide-scrollbars"]


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("ffmpeg 를 찾지 못했어요 — winget install Gyan.FFmpeg 후 다시 열어 주세요")
    return exe


def _open(pw, page_path: Path):
    c = config.load()["render"]
    br = pw.chromium.launch(args=ARGS)
    pg = br.new_page(viewport={"width": int(c["w"]), "height": int(c["h"])}, device_scale_factor=1)
    console: List[str] = []
    pg.on("console", lambda m: console.append(f"console.{m.type}: {m.text}") if m.type in ("error",) else None)
    pg.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
    pg.goto(page_path.resolve().as_uri(), wait_until="load")
    try:
        pg.wait_for_function("window.__ready === true", timeout=60_000)
    except Exception:
        console.append("페이지가 60초 안에 준비되지 않음(__ready) — 그림·글꼴 로딩 또는 스크립트 오류")
    return br, pg, console


def check(page_path: Path, duration: float) -> List[str]:
    """브라우저로 열어 오류·타임라인 길이·화면 밖 요소를 점검한다. 문제 목록(쉬운 말 아님 — Claude 에게 준다)."""
    from playwright.sync_api import sync_playwright
    probs: List[str] = []
    with sync_playwright() as pw:
        br, pg, console = _open(pw, page_path)
        try:
            for t in (0.5, duration * 0.33, duration * 0.66, max(0, duration - 0.2)):
                pg.evaluate("t => window.__seek(t)", t)
            errs = pg.evaluate("window.__errors || []")
            probs += [f"런타임 오류: {e}" for e in errs] + console
            tl = pg.evaluate("window.TL ? TL.duration() : -1")
            if tl < 0:
                probs.append("TL 이 없습니다")
            elif tl > duration + 0.5:
                probs.append(f"타임라인이 음성보다 깁니다 ({tl:.1f}초 > {duration:.1f}초) — 음성 끝에서 잘린다")
            pg.evaluate(f"window.__seek({max(0, duration - 0.2)})")
            n = pg.evaluate("""() => document.querySelectorAll('#stage *').length""")
            if n < 5:
                probs.append("장면 요소가 거의 없습니다")
            vis = pg.evaluate("""() => { let c=0; document.querySelectorAll('#stage img').forEach(i=>{
                const s=getComputedStyle(i); if(s.opacity!=='0' && s.visibility!=='hidden') c++;}); return c; }""")
            if vis == 0:
                probs.append("마지막 순간에 보이는 그림이 하나도 없습니다 — 마지막 컷은 비어 있으면 안 된다")
        finally:
            br.close()
    return probs[:12]


def _inputs(job: Job, sid: str) -> list:
    return [p for p in [job.sub("motion", sid) / "index.html", job.sub("audio", sid) / "narration.wav",
                        *sorted(job.sub("images", sid).glob("*.png"))] if p.exists()]


def _key(job: Job, sid: str) -> str:
    """영상의 재료가 그대로인가 — **파일 내용**으로 본다(날짜 X).
    ★ 2026-10-06 실측: 이어서 시작하면 s4 가 페이지를 다시 써서 날짜가 바뀌고, 다 된 영상을 전부 또 구웠다."""
    h = hashlib.sha1()
    for p in _inputs(job, sid):
        h.update(p.name.encode())
        h.update(hashlib.sha1(p.read_bytes()).digest())
    return "c" + h.hexdigest()[:12]


def render(job: Job, sid: str) -> Path:
    from playwright.sync_api import sync_playwright
    c = config.load()["render"]
    timing = config.read_json(job.sub("audio", sid) / "timing.json") or {}
    page = job.sub("motion", sid) / "index.html"
    wav = job.sub("audio", sid) / "narration.wav"
    if not page.exists() or not wav.exists():
        raise SystemExit(f"{sid}: 모션 페이지나 음성이 없습니다 — 먼저 s2-tts, s4-motion")
    fps = int(c["fps"])
    dur = float(timing["duration"])
    n = int(round(dur * fps))
    out = job.video(sid)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    cmd = [ffmpeg(), "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg",
           "-i", "-", "-i", str(wav), "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", str(c["preset"]), "-crf", str(c["crf"]), "-pix_fmt", "yuv420p",
           "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest",
           "-movflags", "+faststart", str(tmp)]
    detail("  " + " ".join(cmd))
    t0 = time.time()
    with sync_playwright() as pw:
        br, pg, console = _open(pw, page)
        if console:
            detail(f"  {sid} 페이지 경고: {console[:5]}")
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            q = int(c["jpeg_quality"])
            step = max(1, n // 10)
            for f in range(n):
                pg.evaluate("t => window.__seek(t)", f / fps)
                ff.stdin.write(pg.screenshot(type="jpeg", quality=q))
                if f and f % step == 0:
                    log(f"    {sid} 굽는 중 {int(f * 100 / n)}%")
            # 썸네일 — 마지막 문장이 시작되고 1초 뒤
            last = (timing.get("cues") or [{}])[-1].get("start", dur * 0.8)
            pg.evaluate("t => window.__seek(t)", min(dur - 0.1, last + 1.0))
            pg.screenshot(path=str(out.with_suffix(".jpg")), type="jpeg", quality=90)
        finally:
            br.close()
            ff.stdin.close()
            err = ff.stderr.read().decode("utf-8", "replace")
            code = ff.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg 실패({code}): {err[-400:]}")
    tmp.replace(out)
    detail(f"  {sid} 렌더 {n}프레임 {time.time() - t0:.0f}초")
    return out


def run(job: Job, only=None, force: bool = False, **_) -> None:
    for sid in job.pick(only):
        if not (job.sub("motion", sid) / "index.html").exists():
            log(f"  · {sid} 는 아직 모션이 없어서 건너뛰어요.")
            continue
        meta_p = job.sub("out", sid) / "render.json"
        key = _key(job, sid)
        old = (config.read_json(meta_p, {}) or {}).get("key", "")
        vid = job.video(sid)
        if vid.exists() and not force and old and not old.startswith("c"):
            # 예전(날짜 기준) 기록으로 구운 영상 — 재료가 영상보다 나중에 '내용이' 바뀐 게 아니면 그대로 쓴다.
            # 페이지(index.html)는 다시 쓰여도 날짜만 바뀔 뿐이라, 장면 조각(scene.*)·음성·그림 날짜로 판단한다.
            src = [p for p in [job.sub("motion", sid) / "scene.html", job.sub("motion", sid) / "scene.js",
                               job.sub("audio", sid) / "narration.wav", *job.sub("images", sid).glob("*.png")] if p.exists()]
            if all(p.stat().st_mtime <= vid.stat().st_mtime + 2 for p in src):
                config.write_json(meta_p, {"key": key, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "adopted": True})
                old = key
        if vid.exists() and old == key and not force:
            detail(f"s5: {sid} 그대로 — 건너뜀")
            continue
        log(f"  {sid} 영상으로 굽는 중이에요 (1~3분) …")
        out = render(job, sid)
        config.write_json(meta_p, {"key": key, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        job.update_state(sid, {"rendered": time.strftime("%Y-%m-%d %H:%M")})
        log(f"  ✓ {sid} 완성 — out/{out.name}")
