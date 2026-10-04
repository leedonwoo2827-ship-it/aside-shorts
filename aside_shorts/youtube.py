"""YouTube Studio 업로드 — 실제 Chrome(전용 프로필) + CDP 로 붙는다. aside-threads `threads.py` 와 같은 방식.

왜 Playwright 가 띄운 브라우저가 아닌가: Google 은 자동화 브라우저 로그인을 막는다.
그래서 **사람이 그 Chrome 에서 한 번 로그인**하고(채널 선택까지), 이후로는 같은 프로필에 CDP 로 붙어서 조종한다.
그 Chrome 창이 화면 왼쪽에 떠 있고, 오른쪽에 패널이 붙는다 — 업로드가 눈앞에서 진행된다.

업로드 순서: youtube.com/upload → 파일 → 제목·설명 → 아동용 아님 → 다음×3 → 공개/비공개/예약 → 저장.
Studio 화면이 바뀌면 **SEL 표만 고친다.** `run.bat probe` 가 업로드 창 구조와 스크린샷을 logs/probe/ 에 떨군다.
"""
from __future__ import annotations

import ctypes
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import accounts, config
from .log import detail, log

HOME = "https://studio.youtube.com/"
UPLOAD = "https://www.youtube.com/upload"

# ── 셀렉터 — 화면이 바뀌면 여기만 고친다 ───────────────────────────────────
SEL: Dict[str, Any] = {
    "dialog": "ytcp-uploads-dialog",
    "file": 'ytcp-uploads-file-picker input[type="file"], input[type="file"]',
    "title": "#title-textarea #textbox",
    "desc": "#description-textarea #textbox",
    "not_kids": 'tp-yt-paper-radiobutton[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',
    "next": "#next-button",
    "done": "#done-button",
    "vis": {"public": 'tp-yt-paper-radiobutton[name="PUBLIC"]',
            "unlisted": 'tp-yt-paper-radiobutton[name="UNLISTED"]',
            "private": 'tp-yt-paper-radiobutton[name="PRIVATE"]'},
    "schedule_open": "#second-container-expand-button",
    "date_trigger": "#datepicker-trigger",
    "date_input": "ytcp-date-picker tp-yt-paper-input input, ytcp-date-picker input",
    "time_input": "#time-of-day-container input, ytcp-datetime-picker #time-of-day-container tp-yt-paper-input input",
    "video_url": "ytcp-video-info a.ytcp-video-info, .video-url-fadeable a",
    "progress": "ytcp-video-upload-progress .progress-label, ytcp-video-upload-progress",
    "close": "#close-button, ytcp-button#close-button",
    # 태그 — 세부정보 「자세히 보기」 안
    "more": "#toggle-button",
    "tags": "#tags-container input#text-input, ytcp-video-metadata-editor-advanced input[aria-label*='태그'], "
            "input[aria-label*='Tags'], input[aria-label*='태그']",
    # 고정 댓글 — 공개된 시청 페이지
    "c_box": "#simplebox-placeholder, ytd-comment-simplebox-renderer #placeholder-area",
    "c_input": "#contenteditable-root",
    "c_submit": "#submit-button",
    "c_thread": "ytd-comment-thread-renderer",
    "c_menu": "#action-menu-button",
    "uploaded": re.compile(r"(업로드 완료|Upload complete|확인 완료|Checks complete|처리|Processing|SD|HD|저작권|Copyright)", re.I),
}
SESSION_COOKIES = ("SAPISID", "__Secure-3PAPISID", "LOGIN_INFO")


class PostError(RuntimeError):
    pass


class ScheduleUnsupported(PostError):
    """Studio 예약 칸을 다루지 못함 — 호출부가 내장 대기열로 넘긴다."""


# ── Chrome ──────────────────────────────────────────────────────────────────
def chrome_path() -> str:
    """Windows · macOS · Linux 의 Google Chrome. 설정 youtube.chrome 이 있으면 그것이 이긴다.

    Chromium 도 받지만 **Google Chrome 을 권한다** — 로그인 차단이 덜하다(lecture-composer 와 같은 이유)."""
    cfg = config.load()["youtube"]
    cands = [cfg.get("chrome") or ""]
    if sys.platform.startswith("win"):
        cands += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe")]
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    else:
        cands += [shutil.which(n) or "" for n in
                  ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")]
    for c in cands:
        if c and Path(c).exists():
            return c
    raise SystemExit("Chrome 을 찾지 못했습니다 — Google Chrome 을 설치하거나 "
                     "aside.config.local.json 의 youtube.chrome 에 실행 파일 경로를 적으세요")


_SCREEN: Optional[tuple] = None


def screen() -> tuple:
    """화면 크기(가로, 세로) — 왼쪽 YouTube 창과 오른쪽 패널을 나란히 놓는 데 쓴다."""
    global _SCREEN
    if _SCREEN:
        return _SCREEN
    try:
        if sys.platform.startswith("win"):
            u = ctypes.windll.user32
            u.SetProcessDPIAware()
            _SCREEN = (u.GetSystemMetrics(0), u.GetSystemMetrics(1))
        else:
            # macOS·Linux: tkinter 는 메인 스레드를 가리므로 별도 프로세스로 묻는다
            r = subprocess.run([sys.executable.replace("pythonw", "python"), "-c",
                                "import tkinter as t;r=t.Tk();r.withdraw();"
                                "print(r.winfo_screenwidth(), r.winfo_screenheight())"],
                               capture_output=True, text=True, timeout=10)
            w, h = map(int, r.stdout.split())
            _SCREEN = (w, h)
    except Exception:
        _SCREEN = (1440, 900) if sys.platform == "darwin" else (1920, 1080)
    return _SCREEN


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_chrome(acc: Dict[str, Any], url: str = HOME) -> bool:
    """그 계정의 Chrome 을 왼쪽에 띄운다. 이미 떠 있으면 그대로 쓴다. 새로 띄웠으면 True."""
    port = int(acc["port"])
    if port_open(port):
        return False
    sw, sh = screen()
    panel = int(config.load()["ui"]["width"])
    subprocess.Popen([
        chrome_path(), f"--user-data-dir={accounts.profile(acc)}",
        f"--remote-debugging-port={port}", "--no-first-run", "--no-default-browser-check",
        "--window-position=0,0", f"--window-size={max(900, sw - panel)},{sh - 40}", url])
    if not wait_port(port):
        raise SystemExit("Chrome 디버그 포트가 열리지 않습니다 — 같은 프로필 Chrome 이 이미 떠 있으면 닫고 다시")
    try:
        place(port, "left")
    except Exception as e:      # noqa: BLE001 — 배치는 보기 좋으라고 하는 일이다
        detail(f"  (창 배치 실패, 무시: {e})")
    return True


def wait_port(port: int, tries: int = 60) -> bool:
    for _ in range(tries):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


PANEL_PORT_OFFSET = -1      # 패널 Chrome 의 디버그 포트 = base_port - 1


def free_port(start: int, avoid=(), span: int = 60) -> int:
    """start 부터 위로 비어 있는 포트 하나. 이런 도구를 여러 개 쓰는 PC 는 포트가 자주 겹친다."""
    for p in range(start, start + span):
        if p not in avoid and not port_open(p):
            return p
    raise SystemExit(f"{start} 근처에 빈 포트가 없어요 — 다른 프로그램을 몇 개 닫고 다시 해 주세요")


def panel_port() -> int:
    """패널 Chrome 의 디버그 포트. 기억해 둔 값 → 없으면 base_port-1. 실제로 고르는 건 pick_panel_port()."""
    saved = config.local().get("panel_port")
    return int(saved) if saved else int(config.load()["youtube"]["base_port"]) + PANEL_PORT_OFFSET


def is_our_panel(port: int, ui_port: int) -> bool:
    """그 포트의 Chrome 이 aside 패널인가(다른 도구의 Chrome 일 수도 있다)."""
    import json as _json
    try:
        tabs = _json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1))
        return any(str(t.get("url", "")).startswith(f"http://127.0.0.1:{ui_port}/") for t in tabs)
    except Exception:
        return False


def pick_panel_port(ui_port: int) -> int:
    p = panel_port()
    if not port_open(p) or is_our_panel(p, ui_port):
        return p
    used = {int(a["port"]) for a in accounts.all_()}
    p = free_port(int(config.load()["youtube"]["base_port"]) - 40, avoid=used)
    data = config.local()
    data["panel_port"] = p
    config.save_local(data)
    return p


def place(port: int, side: str) -> None:
    """창을 화면 왼쪽(YouTube) 또는 오른쪽(패널)에 **정확히** 붙인다.

    ★ 처음에는 실행 인자(--window-position/size)로만 놓았는데, 그 좌표는 Windows 배율(125%·150%)과
      모니터 구성에 따라 Chrome 이 다르게 읽는다 — 패널이 화면 밖으로 밀리고 가운데가 비었다(2026-10-03).
      그래서 창이 뜬 뒤 **Chrome 에게 직접** 쓸 수 있는 화면 영역(screen.avail*)을 묻고,
      같은 단위로 CDP `Browser.setWindowBounds` 를 건다. 배율·OS 와 무관하게 맞는다."""
    from playwright.sync_api import sync_playwright

    width = int(config.load()["ui"]["width"])
    with sync_playwright() as pw:
        br = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        if True:      # with 블록을 나가면 CDP 연결만 끊긴다 — 사람이 보는 창은 그대로 남는다
            ctx = br.contexts[0]
            page = next((p for p in ctx.pages if not p.url.startswith("devtools")), None) or ctx.new_page()
            ax, ay, aw, ah = page.evaluate(
                "[screen.availLeft || 0, screen.availTop || 0, screen.availWidth, screen.availHeight]")
            cdp = ctx.new_cdp_session(page)
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
            if side == "right":
                b = {"left": ax + aw - width, "top": ay, "width": width, "height": ah}
            else:
                b = {"left": ax, "top": ay, "width": max(700, aw - width), "height": ah}
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": b})
            cdp.detach()


def arrange(acc: Optional[Dict[str, Any]] = None) -> List[str]:
    """떠 있는 창들을 다시 붙인다 — 패널의 「창 정렬」."""
    done = []
    if port_open(panel_port()):
        place(panel_port(), "right")
        done.append("패널")
    if acc and port_open(int(acc["port"])):
        place(int(acc["port"]), "left")
        done.append(acc["name"])
    return done


class Session:
    """with Session(acc) as s: s.page …  — 끝나도 Chrome 은 닫지 않는다(사람이 계속 본다)."""

    def __init__(self, acc: Dict[str, Any]):
        self.acc = acc

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        ensure_chrome(self.acc)
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.acc['port']}")
        self.ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        pages = [p for p in self.ctx.pages if "youtube." in p.url]
        self.page = pages[0] if pages else (self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page())
        self.page.bring_to_front()
        return self

    def __exit__(self, *exc):
        try:
            self._pw.stop()     # CDP 연결만 끊는다 — 창은 남는다
        except Exception:
            pass

    def logged_in(self) -> bool:
        names = {c["name"] for c in self.ctx.cookies(["https://www.youtube.com", "https://studio.youtube.com"])}
        return any(n in names for n in SESSION_COOKIES)

    def shot(self, name: str) -> Path:
        d = config.LOGS / "post"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{time.strftime('%m%d-%H%M%S')}-{name}.png"
        try:
            self.page.screenshot(path=str(p))
        except Exception:
            pass
        return p




# ── 동작 ────────────────────────────────────────────────────────────────────
def open_login(acc: Dict[str, Any]) -> None:
    fresh = ensure_chrome(acc, HOME)
    if not fresh:
        with Session(acc) as s:
            s.page.goto(HOME)
    log("왼쪽 Chrome 창에서 Google 로그인 후, 올릴 채널(예: @dekmanfactory)이 선택됐는지 확인해 주세요. 창은 닫지 않아도 돼요.")


def status(acc: Dict[str, Any]) -> Dict[str, Any]:
    if not port_open(int(acc["port"])):
        return {"chrome": False, "logged_in": None}
    with Session(acc) as s:
        return {"chrome": True, "logged_in": s.logged_in()}


def _clean_tags(xs) -> list:
    out = []
    for t in xs or []:
        t = re.sub(r"[\s#,<>]+", "", str(t))
        if t and t.lower() not in ("shorts", "쇼츠") and t not in out:
            out.append(t)
    return out


def meta(short: Dict[str, Any]) -> Dict[str, Any]:
    """업로드 문구 — @dekmanfactory 형식 하나로 통일. (자동 업로드·패널 복사 모두 이것만 쓴다)

    제목   「후크 #shorts #태그 … #쇼츠」 (100자 안에서 뒤 태그부터 뺀다)
    설명   후크 1줄 / 후크 2줄 / #태그 4~5개 #shorts / (빈 줄) / (2) 공간적 관점 / 개념 설명 문단들
    태그   Studio 태그 칸(쉼표) · 고정 댓글 한 줄
    예전 대본(youtube.title/description/hashtags 만 있음)도 같은 모양으로 조립한다."""
    yt = short.get("youtube") or {}
    head = (yt.get("title") or (short.get("hook") or {}).get("line1") or short.get("id", "")).strip()
    head = re.sub(r"\s*#\S+", "", head).strip()          # 제목 칸에 섞여 온 해시태그는 떼고 아래에서 붙인다
    ttags = _clean_tags(yt.get("title_tags") or yt.get("hashtags"))
    parts = [head, "#shorts", *[f"#{t}" for t in ttags]]
    while len(" ".join(parts + ["#쇼츠"])) > 100 and len(parts) > 2:
        parts.pop()
    title = " ".join(parts + ["#쇼츠"])[:100]

    hook = [x.strip() for x in yt.get("hook_lines") or [] if x and x.strip()]
    if not hook and yt.get("description"):
        hook = [x.strip() for x in str(yt["description"]).splitlines() if x.strip()][:2]
    dtags = _clean_tags(yt.get("desc_tags") or ttags[:4])[:5]
    lines = hook[:2] + [" ".join([f"#{t}" for t in dtags] + ["#shorts"])]
    body = [x.strip() for x in yt.get("summary") or [] if x and x.strip()]
    label = (yt.get("section_label") or (short.get("section") or {}).get("label") or "").strip()
    if label or body:
        lines += ["", *([label] if label else []), *body]
    desc = "\n".join(lines).replace("<", "").replace(">", "")[:4800]

    tags = _clean_tags(yt.get("tags") or ttags)
    while len(",".join(tags)) > 480 and tags:              # Studio 태그 칸 500자 제한
        tags.pop()
    return {"title": title, "description": desc, "tags": tags,
            "pinned_comment": (yt.get("pinned_comment") or "").strip()}


def compose(short: Dict[str, Any]) -> tuple:
    """(제목, 설명) — meta() 의 짧은 꼴(예전 호출부 호환)."""
    m = meta(short)
    return m["title"], m["description"]


def _fill(page, selector: str, text: str) -> None:
    box = page.locator(selector).first
    box.wait_for(state="visible", timeout=60_000)
    box.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.keyboard.insert_text(text)
    time.sleep(0.4)


def _date_text(sample: str, when: datetime) -> str:
    """지금 칸에 적힌 꼴을 흉내 낸다 — 한국어 「2026. 10. 5.」, 영어 「Oct 5, 2026」."""
    if re.search(r"[A-Za-z]{3}", sample or ""):
        return f"{when:%b} {when.day}, {when.year}"
    return f"{when.year}. {when.month}. {when.day}."


def _time_text(sample: str, when: datetime) -> str:
    h12 = when.hour % 12 or 12
    if "오전" in (sample or "") or "오후" in (sample or ""):
        return f"{'오전' if when.hour < 12 else '오후'} {h12}:{when.minute:02d}"
    if re.search(r"AM|PM", sample or "", re.I):
        return f"{h12}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"
    return when.strftime("%H:%M")


def _set_schedule(s: Session, when: datetime) -> None:
    page = s.page
    try:
        page.locator(SEL["schedule_open"]).first.click(timeout=10_000)
        time.sleep(0.8)
        page.locator(SEL["date_trigger"]).first.click(timeout=10_000)
        di = page.locator(SEL["date_input"]).first
        di.wait_for(state="visible", timeout=10_000)
        txt = _date_text(di.input_value(), when)
        di.fill(txt)
        di.press("Enter")
        time.sleep(0.6)
        ti = page.locator(SEL["time_input"]).first
        ti.wait_for(state="visible", timeout=10_000)
        ttxt = _time_text(ti.input_value(), when)
        ti.click()
        ti.fill(ttxt)
        ti.press("Enter")
        time.sleep(0.6)
        detail(f"  예약 입력: {txt} {ttxt}")
        s.shot("schedule-set")
    except Exception as e:
        s.shot("schedule-fail")
        raise ScheduleUnsupported(f"Studio 예약 칸을 다루지 못했습니다: {str(e)[:160]}")


def _wait_uploaded(s: Session, limit: int = 1800) -> None:
    """파일 전송이 끝날 때까지 — 끝나기 전에 창을 떠나면 업로드가 끊긴다."""
    page = s.page
    t0 = time.time()
    last = ""
    while time.time() - t0 < limit:
        try:
            txt = page.locator(SEL["progress"]).first.inner_text(timeout=3_000).strip()
        except Exception:
            txt = ""
        if txt != last:
            detail(f"  진행: {txt[:80]}")
            last = txt
        if txt and not re.search(r"(업로드 중|Uploading|\d+\s*%)", txt) and SEL["uploaded"].search(txt):
            return
        time.sleep(2)
    raise PostError("업로드가 30분 안에 끝나지 않았습니다")


# 화면 글자로도 찾는다 — Studio 는 내부 이름(name=…)이 자주 바뀐다(2026-10-04 실측: 아동용 라디오를 name 으로 못 찾음)
TEXT: Dict[str, Any] = {
    "not_kids": re.compile(r"(아니요, 아동용이 아닙니다|No, it.s not made for kids)"),
    "public": re.compile(r"^(공개|Public)$"),
    "unlisted": re.compile(r"^(일부 공개|Unlisted)$"),
    "private": re.compile(r"^(비공개|Private)$"),
    "next": re.compile(r"^(다음|Next)$"),
    "done": re.compile(r"^(저장|게시|예약|Save|Publish|Schedule)$"),
}


def _click(page, css: str, text=None, *, role: str = "radio", timeout: int = 20_000) -> None:
    """css → (role, 이름) → 글자 순서로 찾아 누른다. 안 보이면 스크롤해서."""
    cands = [page.locator(css).first]
    if text is not None:
        cands += [page.get_by_role(role, name=text).first, page.get_by_text(text).first]
    deadline = time.time() + timeout / 1000
    last: Exception = PostError("못 찾음")
    while time.time() < deadline:
        for c in cands:
            try:
                if c.count() and c.is_visible():
                    c.scroll_into_view_if_needed(timeout=3_000)
                    c.click(timeout=5_000)
                    return
            except Exception as e:      # noqa: BLE001
                last = e
        time.sleep(0.5)
    raise PostError(f"화면에서 버튼을 찾지 못했어요 ({text.pattern if text is not None else css}): {str(last)[:120]}")


TEXT_MORE = re.compile(r"^(자세히 보기|Show more|더보기)$")
TEXT_PIN = re.compile(r"^(고정|Pin)$")


def _fill_tags(s: Session, tags: List[str]) -> bool:
    """세부정보 「자세히 보기」를 펼쳐 태그 칸에 쉼표로 넣는다. 못 하면 경고만 — 업로드는 계속."""
    page = s.page
    try:
        try:
            _click(page, SEL["more"], TEXT_MORE, role="button", timeout=8_000)
            time.sleep(0.8)
        except PostError:
            pass                                    # 이미 펼쳐져 있으면 버튼이 없다
        box = page.locator(SEL["tags"]).first
        box.wait_for(state="visible", timeout=10_000)
        box.scroll_into_view_if_needed(timeout=3_000)
        box.click()
        page.keyboard.insert_text(", ".join(tags) + ",")
        time.sleep(0.5)
        log(f"  · 태그 {len(tags)}개를 넣었어요")
        return True
    except Exception as e:      # noqa: BLE001
        detail(f"  태그 입력 실패: {e}")
        s.shot("tags-fail")
        log("  △ 태그 칸을 못 찾아 건너뛰었어요 (Studio 에서 직접 넣을 수 있어요)")
        return False


def _watch_url(url: str) -> str:
    m = re.search(r"(?:shorts/|v=|youtu\.be/)([A-Za-z0-9_-]{6,})", url or "")
    return f"https://www.youtube.com/watch?v={m.group(1)}" if m else url


def _pin_comment(s: Session, url: str, text: str) -> bool:
    """공개된 영상에 댓글을 달고 고정한다(새 탭). 못 하면 경고만 — 업로드는 이미 끝났다."""
    page = s.ctx.new_page()
    try:
        log("  ⑨ 고정 댓글을 다는 중")
        for attempt in range(4):                    # 방금 올린 영상은 댓글 칸이 늦게 열린다
            page.goto(_watch_url(url), wait_until="domcontentloaded")
            time.sleep(4)
            page.mouse.wheel(0, 700)
            time.sleep(3)
            if page.locator(SEL["c_box"]).count():
                break
            time.sleep(30)
        page.locator(SEL["c_box"]).first.click(timeout=15_000)
        page.locator(SEL["c_input"]).first.wait_for(state="visible", timeout=10_000)
        page.keyboard.insert_text(text)
        page.locator(SEL["c_submit"]).first.click(timeout=10_000)
        time.sleep(4)
        th = page.locator(SEL["c_thread"]).filter(has_text=text[:20]).first
        th.locator(SEL["c_menu"]).first.click(timeout=10_000)
        page.get_by_text(TEXT_PIN).first.click(timeout=8_000)
        time.sleep(1)
        dlg = page.get_by_role("button", name=TEXT_PIN)
        if dlg.count():
            dlg.last.click(timeout=8_000)           # 「이 댓글을 고정할까요?」 확인
        time.sleep(2)
        s.shot("pinned")
        log("  ✓ 고정 댓글을 달았어요")
        return True
    except Exception as e:      # noqa: BLE001
        detail(f"  고정 댓글 실패: {e}")
        try:
            page.screenshot(path=str(config.LOGS / "post" / f"{time.strftime('%m%d-%H%M%S')}-pin-fail.png"))
        except Exception:
            pass
        log("  △ 고정 댓글을 못 달았어요 — 패널의 「고정 댓글 복사」로 직접 달아 주세요")
        return False
    finally:
        try:
            page.close()
        except Exception:
            pass


def upload(acc: Dict[str, Any], short: Dict[str, Any], video: Path, *, when: Optional[datetime] = None,
           visibility: str = "public", dry_run: bool = False) -> Dict[str, Any]:
    """쇼츠 하나 업로드. when 이 있으면 YouTube 예약 공개(그 시각에 공개 — PC 가 꺼져 있어도 된다).

    ★ 파일을 넣은 뒤에는 **다시 시도하지 않는다** — 페이지를 다시 열면 같은 영상이 초안으로 하나 더 생기고
      「사이트를 새로고침하시겠습니까?」가 뜬다(2026-10-04 실측). 멈추면 왼쪽 창에서 사람이 이어서 누르면 된다."""
    m = meta(short)
    title, desc = m["title"], m["description"]
    with Session(acc) as s:
        page = s.page
        page.on("dialog", lambda d: d.dismiss())        # 떠나기 확인창은 「취소」 — 올리던 걸 지키기
        log("  ① 업로드 창을 여는 중")
        page.goto(UPLOAD, wait_until="domcontentloaded")
        if "accounts.google." in page.url or not s.logged_in():
            s.shot("not-logged-in")
            raise PostError("로그인이 안 되어 있습니다 — 패널의 「로그인」으로 이 계정 Chrome 에서 로그인하세요")
        inp = page.locator(SEL["file"]).first
        inp.wait_for(state="attached", timeout=60_000)
        log("  ② 영상 파일을 넣는 중")
        inp.set_input_files(str(video))
        try:
            log("  ③ 제목·설명을 쓰는 중")
            _fill(page, SEL["title"], title)
            _fill(page, SEL["desc"], desc)
            if m["tags"]:
                _fill_tags(s, m["tags"])
            log("  ④ 「아동용 아님」 고르는 중")
            _click(page, SEL["not_kids"], TEXT["not_kids"], timeout=30_000)
            s.shot("details")
            for n in range(3):
                _click(page, SEL["next"], TEXT["next"], role="button", timeout=30_000)
                time.sleep(1.5)
            if when:
                log(f"  ⑤ 예약 시간을 넣는 중 ({when:%m월 %d일 %H:%M})")
                _set_schedule(s, when)
            else:
                log(f"  ⑤ 공개 범위: {visibility}")
                _click(page, SEL["vis"].get(visibility, SEL["vis"]["public"]), TEXT.get(visibility, TEXT["public"]))
        except (ScheduleUnsupported, PostError, Exception) as e:
            s.shot("stopped")
            detail(f"  upload 멈춤: {e}")
            raise PostError(f"{str(e)[:200]} — 영상은 이미 들어갔어요. 왼쪽 Studio 창에서 나머지를 직접 눌러 마무리해 주세요"
                            " (다시 올리기 버튼을 누르면 초안이 하나 더 생겨요).")
        url = None
        try:
            url = page.locator(SEL["video_url"]).first.get_attribute("href", timeout=20_000)
        except Exception:
            pass
        s.shot("ready")
        if dry_run:
            log("✓ 업로드 창을 채워 두었어요. 왼쪽 창에서 확인해 보세요. 「저장/게시」는 누르지 않았어요"
                " (영상은 Studio 에 비공개 초안으로 남아요 — 그대로 이어서 눌러도 되고, 지워도 돼요).")
            return {"dry_run": True, "url": url}
        log("  ⑥ 영상이 다 올라갈 때까지 기다리는 중 …")
        _wait_uploaded(s)
        log("  ⑦ " + ("예약하는" if when else "게시하는") + " 중")
        _click(page, SEL["done"], TEXT["done"], role="button", timeout=30_000)
        time.sleep(3)
        s.shot("posted")
        try:
            page.locator(SEL["close"]).first.click(timeout=10_000)
        except Exception:
            pass
        pinned = None
        if m["pinned_comment"]:
            if when or visibility != "public":
                log("  · 고정 댓글은 공개된 뒤에 달 수 있어요 — 공개되면 패널의 「고정 댓글 복사」로 달아 주세요.")
            elif url:
                pinned = _pin_comment(s, url, m["pinned_comment"])
        log("  ⑧ 끝!")
        detail(f"  uploaded url={url}")
        return {"url": url, "title": title, "at": datetime.now().isoformat(timespec="seconds"),
                "scheduled_for": when.isoformat(timespec="minutes") if when else None,
                "visibility": "scheduled" if when else visibility, "pinned": pinned}


def probe(acc: Dict[str, Any]) -> Path:
    """업로드 창 구조를 떠 둔다 — 셀렉터를 맞출 때 쓴다. 올리지 않는다."""
    out = config.LOGS / "probe" / time.strftime("%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    with Session(acc) as s:
        log(f"  로그인: {s.logged_in()}")
        s.page.goto(UPLOAD, wait_until="domcontentloaded")
        time.sleep(4)
        s.page.screenshot(path=str(out / "1-upload.png"))
        try:
            (out / "page.aria.yml").write_text(s.page.locator("body").aria_snapshot(), encoding="utf-8")
        except Exception as e:
            log(f"  aria 스냅샷 실패: {e}")
        (out / "page.html").write_text(s.page.content(), encoding="utf-8")
    log(f"  → {out}")
    return out
