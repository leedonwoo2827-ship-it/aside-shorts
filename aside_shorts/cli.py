"""python -m aside_shorts <명령> …   (run.bat <명령> … 과 같다)

만들기
  new <job> --from <원고폴더|.docx> [--files a.docx …] [--rewritten] [--title 통합사회1] [--style vox-retro] [--voice F4]
  make      --job J [--only 1-2-04,…] [--force] [--limit N]   딸깍: 쇼츠마다 s2→s5 (목소리·그림·모션·영상) — 개작·대본은 사람이 확인하고 따로
  yt-meta   --job J [--only id] [--force]                    유튜브 문구(제목·해시태그·설명·태그·고정 댓글)만 다시 쓰기
  all       --job J [--only 1-2,…]                          밤샘: 단원마다 s0 개작 → s1 대본 → 쇼츠마다 s2→s5 (한 편 실패해도 계속)
  s0-rewrite | s1-script | s2-tts | s3-images | s4-motion | s5-render     (단계별, 옵션 같음)
올리기 (YouTube Studio)
  account add <이름> [--label @채널] | account list | account use <이름> | account rm <이름>
  login   [--account A]                 왼쪽 Chrome 에서 Google 로그인·채널 선택
  post    --job J --only id [--account A] [--at "2026-10-05 19:00"] [--visibility public|unlisted|private]
          [--queue] [--dry-run] [--force]
  plan    --job J [--account A] [--apply]       남은 쇼츠를 다음 빈 슬롯에 예약(미리보기/적용)
  queue                                 시각이 된 대기열 업로드(서버가 30초마다 부른다)
  probe   [--account A]                 업로드 창 구조 떠 두기(셀렉터 맞출 때)
기타
  ui        패널 서버 + 오른쪽 도킹 창
  assets    글꼴(Pretendard·Black Han Sans·Nanum Pen Script)·GSAP 받기
  tts-setup SuperTonic3 모델·목소리 받기(HuggingFace, 약 380MB — setup.bat 이 부른다)
  doctor    Claude·SuperTonic3·ffmpeg·Chrome 점검
  claude-login   Claude 구독 로그인(OAuth)
"""
from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
from typing import List, Optional

from . import accounts, config, schedule, styles
from .job import Job, all_jobs, need
from .log import detail, log

STAGES = ["s0-rewrite", "s1-script", "s2-tts", "s3-images", "s4-motion", "s5-render"]
MAKE = STAGES[2:]      # 딸깍 = 목소리→영상. 0 개작·1 대본은 사람이 확인하는 단계라 따로 누른다
# 로그 창에 보이는 단계 이름 — 쉬운 말로
STEP_NAMES = {"s0-rewrite": "0/5 안전 개작", "s1-script": "1/5 대본 쓰기", "s2-tts": "2/5 목소리 입히기",
              "s3-images": "3/5 그림 그리기", "s4-motion": "4/5 모션 짜기", "s5-render": "5/5 영상 굽기"}
ASSETS = {
    "fonts/Pretendard-{w}.woff2": "https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/woff2/Pretendard-{w}.woff2",
    "fonts/BlackHanSans-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/blackhansans/BlackHanSans-Regular.ttf",
    "fonts/NanumPenScript-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/nanumpenscript/NanumPenScript-Regular.ttf",
    "vendor/gsap.min.js": "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js",
}


def _only(v: Optional[str]) -> Optional[List[str]]:
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


def run_stage(name: str, job: Job, **kw) -> None:
    from . import s0_rewrite, s1_script, s2_tts, s3_images, s4_motion, s5_render
    mod = {"s0-rewrite": s0_rewrite, "s1-script": s1_script, "s2-tts": s2_tts, "s3-images": s3_images,
           "s4-motion": s4_motion, "s5-render": s5_render}[name]
    log(f"── {STEP_NAMES.get(name, name)}")
    detail(f"stage {name} [{job.name}]")
    mod.run(job, **kw)


def cmd_new(a) -> None:
    """원고를 작업으로 가져온다 — 단원(절) 폴더마다 01_raw(원본) / 02_rewrite(개작본)에 복사(원본 폴더는 그대로)."""
    from pathlib import Path
    from .manuscript import classify_sources, place
    stage = "rewrite" if a.rewritten else "raw"
    if a.files:
        items = [{"file": Path(f), "stage": stage} for f in a.files]
        missing = [str(i["file"]) for i in items if not i["file"].is_file()]
        if missing:
            raise SystemExit("파일이 없습니다: " + ", ".join(missing))
    elif a.src:
        items = classify_sources(Path(a.src))
        if a.rewritten:
            items = [{**i, "stage": "rewrite"} for i in items]
    else:
        raise SystemExit("--from <원고폴더> 또는 --files <파일…> 중 하나를 주세요")
    if not items:
        raise SystemExit(f"원고(.docx)를 찾지 못했습니다: {a.src}")
    if a.style and a.style not in styles.names():
        raise SystemExit(f"스타일이 없습니다: {a.style} (있는 것: {', '.join(styles.names())})")
    job = Job(a.name)
    job.dir.mkdir(parents=True, exist_ok=True)
    meta = config.read_json(job.dir / "job.json", {}) or {}
    meta.setdefault("title", a.title or a.name)
    if a.title:
        meta["title"] = a.title
    if a.style:
        meta.setdefault("shorts", {})["style"] = a.style
    if a.voice:
        meta.setdefault("tts", {})["voice"] = a.voice.upper()
    config.write_json(job.dir / "job.json", meta)
    for n, it in enumerate(items, 1):
        out = place(job.dir, it["file"], it["stage"], n)
        log(f"  {'원본  ' if it['stage'] == 'raw' else '개작본'} → {out.parent.parent.name}/{out.parent.name}/")
    us = Job(a.name).units
    log(f"단원 {len(us)}개: " + ", ".join(f"{u.prefix}({'원본' if u.raw else ''}{'+' if u.raw and u.rewrite else ''}{'개작' if u.rewrite else ''})" for u in us))
    if any(u.raw and not u.rewrite for u in us):
        log("원본만 있는 단원은 「0 안전 개작」으로 개작본을 먼저 만들어 주세요.")


def _guard(what: str, fn, fails: List[str]) -> bool:
    """한 단원·한 편이 실패해도 나머지는 계속 — 실패는 기록만 한다(밤샘 무인 제작)."""
    from .llm.claude_cli import ClaudeNotLoggedIn
    try:
        fn()
        return True
    except ClaudeNotLoggedIn:
        raise                       # 로그인이 풀리면 나머지도 다 안 된다 — 멈춘다
    except BaseException as e:      # noqa: BLE001 — SystemExit(쉬운 말 실패)도 여기서 받는다
        if isinstance(e, KeyboardInterrupt):
            raise
        import traceback
        detail(f"{what} 실패: {traceback.format_exc()}")
        msg = str(e) if isinstance(e, SystemExit) and not isinstance(e.code, int) else friendly(e) if isinstance(e, Exception) else str(e)
        log(f"  ✗ {what} 실패 — 다음으로 넘어가요 ({str(msg)[:120]})")
        fails.append(what)
        return False


def cmd_make(job: Job, only, force: bool, limit: int, full: bool = False) -> int:
    """딸깍(make) = 쇼츠마다 2 목소리 → 5 영상. 밤샘(all) = 단원마다 0 개작 → 1 대본 다음 쇼츠마다 2 → 5.

    한 편이 실패해도 다음 편으로 넘어간다. Claude 한도에 걸리면 기다렸다 이어 간다(claude_wait).
    이미 된 단계는 건너뛰므로 같은 버튼을 다시 눌러도 남은 것만 한다."""
    fails: List[str] = []
    if full:
        for st in STAGES[:2]:
            for u in job.units:
                if only and not any(w == u.prefix or w.startswith(u.prefix + "-") for w in only):
                    continue
                _guard(f"{STEP_NAMES[st]} {u.prefix}", lambda st=st, u=u: run_stage(st, job, only=[u.prefix], force=force), fails)
    ids = job.pick(only) if (only and job.ids()) else job.ids()
    if not ids:
        log("만들 쇼츠가 없어요 — 먼저 「1 대본」을 해 주세요.")
        return 1 if fails else 0
    log(f"쇼츠 {len(ids)}편을 차례로 만들어요 (한 편씩 목소리 → 그림 → 모션 → 영상)")
    done = 0
    for n, sid in enumerate(ids, 1):
        log(f"━━ [{n}/{len(ids)}] {sid}")
        ok = True
        for st in MAKE:
            if not _guard(f"{sid} {STEP_NAMES[st]}", lambda st=st: run_stage(st, job, only=[sid], force=force, limit=limit), fails):
                ok = False
                break               # 이 편은 다음 단계로 못 간다 — 다음 편으로
        done += ok and job.video(sid).exists()
    log(f"다 됐어요! 영상 {sum(1 for s in job.ids() if job.video(s).exists())}편 (이번에 {done}편 확인)"
        + (f" · 실패 {len(fails)}건: {', '.join(fails)} — 같은 버튼을 다시 누르면 실패한 것만 이어서 해요" if fails else ""))
    return 1 if fails else 0


def cmd_assets(_a=None) -> None:
    for rel, url in ASSETS.items():
        ws = ["Regular", "Medium", "Bold", "ExtraBold", "Black"] if "{w}" in rel else [""]
        for w in ws:
            p = config.TEMPLATES / rel.format(w=w)
            if p.exists() and p.stat().st_size > 1000:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            log(f"  받는 중: {p.name}")
            urllib.request.urlretrieve(url.format(w=w), p)
    log("글꼴·GSAP 준비 완료")


def cmd_doctor(_a=None) -> int:
    bad = 0
    from .llm import claude_cli
    if not claude_cli.exe():
        log("✗ Claude(claude 명령)가 없어요 — npm i -g @anthropic-ai/claude-code 후 claude 로 로그인")
        bad += 1
    else:
        st = claude_cli.auth_status()
        ok, msg = claude_cli.ping() if st["logged_in"] else (False, "로그인 안 됨")
        log(f"✓ Claude 연결 정상 ({st['email']} · {st['plan']})" if ok
            else "✗ Claude 로그인이 필요해요 — 패널의 「Claude 로그인」 또는 터미널에서 claude auth login")
        detail(f"doctor claude: {msg}")
        bad += 0 if ok else 1
    from . import tts
    log("✓ SuperTonic3 확인 (내장 · assets/supertonic)" if tts.available()
        else "✗ SuperTonic3 모델이 없어요 — setup.bat 을 다시 실행하거나 run.bat tts-setup")
    bad += 0 if tts.available() else 1
    log("✓ ffmpeg 확인" if shutil.which("ffmpeg") else "✗ ffmpeg 가 없어요 — winget install Gyan.FFmpeg")
    bad += 0 if shutil.which("ffmpeg") else 1
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch().close()
        log("✓ 렌더용 Chromium 확인")
    except Exception as e:
        log("✗ 렌더용 Chromium 이 없어요 — setup.bat 을 다시 실행해 주세요")
        detail(f"doctor chromium: {e}")
        bad += 1
    miss = [p for p in [styles.GSAP] + [styles.FONTS / f for f, _, _ in styles.FONT_FILES] if not p.exists()]
    log("✓ 글꼴·GSAP 확인" if not miss else f"✗ 글꼴·GSAP 이 {len(miss)}개 없어요 — run.bat assets")
    bad += 1 if miss else 0
    try:
        from .youtube import chrome_path
        detail(f"Chrome: {chrome_path()}")
        log("✓ Chrome 확인")
    except SystemExit as e:
        log(f"✗ {e}")
        bad += 1
    accs = accounts.all_()
    log(f"✓ YouTube 계정 {len(accs)}개" if accs else "△ YouTube 계정이 아직 없어요 — 패널 위쪽 ＋ 로 추가해 주세요")
    return bad


def cmd_claude_login(_a=None) -> int:
    """`claude auth login` 을 대신 돌린다 — 패널의 「Claude 로그인」. 브라우저에서 claude.ai 로그인(OAuth)."""
    import re
    import subprocess
    from .llm import claude_cli
    path = claude_cli.exe()
    if not path:
        log("✗ Claude(claude 명령)가 없어요. npm i -g @anthropic-ai/claude-code 후 다시 해 주세요.")
        return 1
    log("브라우저에 Claude 로그인 화면이 열려요. 구독 계정으로 로그인하고 「승인」을 눌러 주세요.")
    proc = subprocess.Popen([path, "auth", "login", "--claudeai"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace")
    shown = False
    try:
        for line in proc.stdout:
            line = line.strip()
            detail(f"claude login: {line}")
            m = re.search(r"https://\S*(claude\.ai|anthropic\.com)\S*", line)
            if m and not shown:
                log(f"LOGIN_URL {m.group(0)}")      # 패널이 「로그인 페이지 열기」 버튼으로 바꾼다
                shown = True
        proc.wait(timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        log("✗ 시간이 지나 로그인을 멈췄어요. 「Claude 로그인」을 다시 눌러 주세요.")
        return 1
    st = claude_cli.auth_status()
    if st["logged_in"]:
        log(f"✓ Claude 로그인 완료 ({st['email']} · {st['plan']})")
        return 0
    log("✗ 로그인이 끝나지 않았어요. 「Claude 로그인」을 다시 눌러 주세요 (또는 터미널에서 claude auth login).")
    return 1


def cmd_account(a) -> None:
    if a.action == "add":
        acc = accounts.add(a.name, a.label or "")
        log(f"계정 추가: {acc['name']} (포트 {acc['port']}) — 이제 `run.bat login --account {acc['name']}`")
    elif a.action == "rm":
        accounts.remove(a.name)
        log(f"계정 목록에서 뺐습니다: {a.name} (profiles/{a.name}/ 폴더는 남겨 둠)")
    elif a.action == "use":
        accounts.set_current(a.name)
        log(f"기본 계정: {a.name}")
    else:
        cur = config.local().get("current")
        for acc in accounts.all_():
            log(f"{'*' if acc['name'] == cur else ' '} {acc['name']:16} {acc.get('label', '')}  port {acc['port']}")


def cmd_post(a) -> None:
    from . import youtube
    job = need(a.job)
    acc = accounts.get(a.account)
    cfg = config.load()["youtube"]
    targets = job.pick(_only(a.only)) if a.only else []
    if not targets:
        raise SystemExit("--only 로 쇼츠 하나를 고르세요")
    vis = a.visibility or cfg.get("visibility", "public")
    for sid in targets:
        st = job.state().get(sid) or {}
        if st.get("posted") and not a.force:
            log(f"· {sid} 는 이미 올렸어요.")
            continue
        short = job.short(sid)
        video = job.video(sid)
        if not short or not video.exists():
            raise SystemExit(f"{sid}: 대본이나 영상이 없습니다 — 먼저 make")
        when = schedule.parse_when(a.at) if a.at else None
        if when and (a.queue or not cfg.get("native_schedule")):
            schedule.enqueue(job, sid, when, acc["name"], "queue")
            log(f"⏰ {sid} 를 {when:%m월 %d일 %H:%M} 에 올리도록 예약했어요. (이 프로그램이 켜져 있어야 올라가요)")
            continue
        log(f"▶ {sid} " + ("미리 채워 보기" if a.dry_run else "예약 공개로 올리기" if when else f"올리기({vis})"))
        detail(f"post {sid} account={acc['name']} when={when} vis={vis} dry={a.dry_run}")
        try:
            res = youtube.upload(acc, short, video, when=when, visibility=vis, dry_run=a.dry_run)
        except youtube.ScheduleUnsupported as e:
            log("✗ YouTube 예약 칸을 다루지 못했어요. 이 프로그램의 예약(--queue)으로 다시 걸어 주세요.")
            detail(f"native schedule 실패: {e}")
            job.update_state(sid, {"error": str(e)[:300]})
            raise SystemExit(2)
        except youtube.PostError as e:
            job.update_state(sid, {"error": str(e)[:300]})
            detail(f"post 실패 {sid}: {e}  (스크린샷: logs/post/)")
            raise SystemExit(f"✗ 올리지 못했어요: {e}")
        if res.get("dry_run"):
            continue
        rec = {"account": acc["name"], **res}
        if when:
            job.update_state(sid, {"scheduled": {"when": when.strftime(schedule.FMT), "account": acc["name"],
                                                 "mode": "native"}, "posted": rec, "error": None})
            log(f"✓ YouTube 에 예약 공개로 올렸어요 ({when:%m월 %d일 %H:%M} 공개)")
        else:
            job.update_state(sid, {"posted": rec, "error": None})
            log("✓ 다 올렸어요! 왼쪽 Studio 창에서 확인해 보세요.")


def _ready(job: Job) -> List[str]:
    st = job.state()
    return [sid for sid in job.ids() if job.video(sid).exists()
            and not (st.get(sid) or {}).get("posted") and not ((st.get(sid) or {}).get("scheduled") or {}).get("when")]


def cmd_plan(a) -> None:
    job = need(a.job)
    acc = accounts.get(a.account)
    left = _ready(job)
    if a.only:
        want = _only(a.only)
        left = [s for s in left if s in want]
    start = schedule.parse_when(a.start + " 00:00") if a.start else None
    slots = schedule.next_slots(len(left), acc["name"], start=start, pat=a.pattern)
    native = bool(config.load()["youtube"].get("native_schedule"))
    fails: List[str] = []
    for sid, when in zip(left, slots):
        log(f"  {when:%m-%d(%a) %H:%M}  {sid}  {(job.short(sid) or {}).get('perspective', '')}")
        if a.apply:
            if native:          # 한 편이 실패해도 다음 편은 계속 — 끝에 실패 목록
                _guard(f"{sid} 예약 업로드", lambda sid=sid, when=when: cmd_post(argparse.Namespace(
                    job=job.name, only=sid, account=acc["name"], at=when.strftime(schedule.FMT),
                    visibility=None, queue=False, dry_run=False, force=False)), fails)
            else:
                schedule.enqueue(job, sid, when, acc["name"])
    if not a.apply:
        log(f"미리보기입니다 — 적용하려면 --apply ({len(left)}건, 계정 {acc['name']})")
    elif fails:
        log(f"예약 {len(left) - len(fails)}건 완료 · 실패 {len(fails)}건: {', '.join(fails)} — 예약 탭에서 다시 누르면 남은 것만 해요")
        raise SystemExit(1)
    else:
        log(f"예약했습니다 ({len(left)}건, 계정 {acc['name']})")


def cmd_queue(_a=None) -> None:
    items = schedule.due()
    if not items:
        return
    it = items[0]       # 한 번에 하나 — 몰아올리지 않는다
    log("⏰ 예약 시간이 되어 올려요")
    detail(f"queue due: {it['job']}/{it['data_id']} ({it['account']})")
    cmd_post(argparse.Namespace(job=it["job"], only=it["data_id"], account=it["account"], at=None,
                                visibility=None, queue=False, dry_run=False, force=False))


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="aside_shorts", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("new")
    p.add_argument("name")
    p.add_argument("--from", dest="src", help="원고 폴더(260711 구조면 02_ 만) 또는 .docx")
    p.add_argument("--files", nargs="+")
    p.add_argument("--rewritten", action="store_true", help="넣는 원고가 이미 안전 개작본이면(02_rewrite 로)")
    p.add_argument("--title")
    p.add_argument("--style")
    p.add_argument("--voice")

    for name in ["make", "all", "yt-meta", *STAGES]:
        p = sub.add_parser(name)
        p.add_argument("--job")
        p.add_argument("--only")
        p.add_argument("--force", action="store_true")
        p.add_argument("--limit", type=int, default=0, help="s3: 이번에 구울 최대 장수")

    p = sub.add_parser("account")
    p.add_argument("action", choices=["add", "list", "use", "rm"])
    p.add_argument("name", nargs="?")
    p.add_argument("--label")

    for name in ("login", "probe"):
        p = sub.add_parser(name)
        p.add_argument("--account")

    p = sub.add_parser("post")
    p.add_argument("--job")
    p.add_argument("--only")
    p.add_argument("--account")
    p.add_argument("--at")
    p.add_argument("--visibility", choices=["public", "unlisted", "private"])
    p.add_argument("--queue", action="store_true", help="YouTube 예약 대신 이 프로그램이 그 시각에 올리기")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("plan")
    p.add_argument("--job")
    p.add_argument("--account")
    p.add_argument("--only", help="예약할 쇼츠만(쉼표). 비우면 영상 있고 안 올린 것 전부")
    p.add_argument("--pattern", help="2-lunch(기본) · 2-morning · 1-evening · 2-fixed · config")
    p.add_argument("--start", help="시작 날짜 YYYY-MM-DD")
    p.add_argument("--apply", action="store_true")

    sub.add_parser("queue")
    p = sub.add_parser("ui")
    p.add_argument("--no-window", action="store_true")
    sub.add_parser("assets")
    sub.add_parser("tts-setup")
    sub.add_parser("doctor")
    sub.add_parser("claude-login")

    a = ap.parse_args(argv)
    try:
        return _dispatch(a, ap)
    except SystemExit:
        raise
    except Exception as e:      # noqa: BLE001 — 화면에는 쉬운 말, 자세한 건 기록 파일로
        import traceback
        detail(traceback.format_exc())
        raise SystemExit(friendly(e))


def friendly(e: Exception) -> str:
    """기술 오류 → 쉬운 말 한 줄. 원문은 logs/detail.log 에."""
    from .llm.claude_cli import ClaudeError, ClaudeNotLoggedIn
    from .tts import TTSUnavailable
    if isinstance(e, ClaudeNotLoggedIn):
        return "✗ Claude 로그인이 필요해요. 패널의 「Claude 로그인」(또는 claude auth login) 후 같은 버튼을 눌러 주세요."
    if isinstance(e, ClaudeError):
        if any(w in str(e).lower() for w in ("limit", "usage", "quota", "rate")):
            return "✗ Claude 사용 한도에 걸렸어요. 한도가 풀린 뒤 같은 버튼을 누르면 남은 것만 이어서 해요."
        return f"✗ Claude 가 일을 마치지 못했어요 — 같은 버튼을 한 번 더 눌러 주세요. ({str(e)[:120]})"
    if isinstance(e, TTSUnavailable):
        return f"✗ 목소리를 만들지 못했어요: {str(e)[:160]}"
    msg = str(e).lower()
    if any(w in msg for w in ("timed out", "timeout", "connection", "network", "urlopen")):
        return "✗ 인터넷 연결이 불안정해요. 잠시 뒤 같은 버튼을 다시 눌러 주세요."
    return "✗ 문제가 생겨서 멈췄어요. 같은 버튼을 한 번 더 눌러 보시고, 또 그러면 logs/detail.log 를 확인해 주세요."


def _dispatch(a, ap) -> int:
    if a.cmd == "new":
        cmd_new(a)
    elif a.cmd in STAGES:
        from .llm.claude_wait import keep_awake
        keep_awake(True)            # 밤새 도는 동안 PC 가 절전에 들어가지 않게
        run_stage(a.cmd, need(a.job), only=_only(a.only), force=a.force, limit=a.limit)
    elif a.cmd == "yt-meta":
        from . import ytmeta
        ytmeta.run(need(a.job), only=_only(a.only), force=a.force)
    elif a.cmd in ("make", "all"):
        from .llm.claude_wait import keep_awake
        keep_awake(True)
        return cmd_make(need(a.job), _only(a.only), a.force, a.limit, full=a.cmd == "all")
    elif a.cmd == "account":
        if a.action in ("add", "use", "rm") and not a.name:
            raise SystemExit("계정 이름을 주세요")
        cmd_account(a)
    elif a.cmd == "login":
        from . import youtube
        youtube.open_login(accounts.get(a.account))
    elif a.cmd == "probe":
        from . import youtube
        youtube.probe(accounts.get(a.account))
    elif a.cmd == "post":
        cmd_post(a)
    elif a.cmd == "plan":
        cmd_plan(a)
    elif a.cmd == "queue":
        cmd_queue()
    elif a.cmd == "ui":
        from .web import server
        server.serve(window=not a.no_window)
    elif a.cmd == "assets":
        cmd_assets()
    elif a.cmd == "tts-setup":
        from .supertonic import engine as st
        log("SuperTonic3 모델·목소리를 받는 중이에요 (처음 한 번, 약 380MB) …")
        st.download(log=log)
        log("✓ SuperTonic3 준비 완료" if not st.missing() else f"✗ 빠진 파일: {', '.join(st.missing())}")
    elif a.cmd == "claude-login":
        return cmd_claude_login()
    elif a.cmd == "doctor":
        return 1 if cmd_doctor() else 0
    else:
        ap.print_help()
    return 0
