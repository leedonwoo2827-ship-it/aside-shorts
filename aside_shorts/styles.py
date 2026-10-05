"""모션 스타일 프리셋 + 완성 페이지 조립.

templates/styles/<이름>/
  style.json   이름·설명·그림 스타일 문구(s3 가 지시문 끝에 붙임)
  guide.md     Claude 에게 주는 모션 스타일 설명서(s4)
  frame.css    후크·자막·배경 색 — 쇼츠 틀
templates/base/  shell.html(틀) · base.css(캔버스·세이프존) · runtime.js(TL·자막·__seek)
templates/fonts/ · templates/vendor/gsap.min.js  — `run.bat assets` 로 받는다(오프라인 렌더)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from . import config

BASE = config.TEMPLATES / "base"
STYLES = config.TEMPLATES / "styles"
FONTS = config.TEMPLATES / "fonts"
GSAP = config.TEMPLATES / "vendor" / "gsap.min.js"

# (파일, 글꼴 이름, 굵기)
FONT_FILES = [
    ("Pretendard-Regular.woff2", "Pretendard", 400), ("Pretendard-Medium.woff2", "Pretendard", 500),
    ("Pretendard-Bold.woff2", "Pretendard", 700), ("Pretendard-ExtraBold.woff2", "Pretendard", 800),
    ("Pretendard-Black.woff2", "Pretendard", 900),
    ("BlackHanSans-Regular.ttf", "Black Han Sans", 400),
    ("NanumPenScript-Regular.ttf", "Nanum Pen Script", 400),
]


def names() -> List[str]:
    return sorted(p.name for p in STYLES.iterdir() if (p / "style.json").exists()) if STYLES.exists() else []


def load(name: str) -> Dict[str, Any]:
    d = STYLES / (name or "")
    if not (d / "style.json").exists():
        raise SystemExit(f"스타일이 없습니다: {name} (있는 것: {', '.join(names())})")
    st = json.loads((d / "style.json").read_text(encoding="utf-8"))
    st["dir"] = d
    st["guide"] = (d / "guide.md").read_text(encoding="utf-8") if (d / "guide.md").exists() else ""
    return st


def rel(target: Path, page_dir: Path) -> str:
    """페이지 기준 상대 경로 — file:// 로 렌더할 때도, 패널 서버(/jobs, /templates)로 볼 때도 같이 맞는다."""
    import os
    return os.path.relpath(target, page_dir).replace("\\", "/")


def fonts_css(page_dir: Path) -> str:
    out = []
    for f, fam, w in FONT_FILES:
        p = FONTS / f
        if p.exists():
            fmt = "woff2" if f.endswith("woff2") else "truetype"
            out.append(f'@font-face{{font-family:"{fam}";font-weight:{w};font-display:block;'
                       f'src:url("{rel(p, page_dir)}") format("{fmt}");}}')
    return "\n".join(out)


def short_data(job, sid: str) -> Dict[str, Any]:
    """페이지에 심는 SHORT — 후크·문장 타이밍·그림 경로(페이지 기준 상대 경로)."""
    sh = job.short(sid) or {}
    timing = config.read_json(job.sub("audio", sid) / "timing.json") or {}
    if not timing:
        raise SystemExit(f"{sid}: 음성 타이밍이 없습니다 — 먼저 s2-tts")
    imgs = {}
    for n, im in enumerate(sh.get("images") or [], 1):
        imgs[im.get("key") or f"img{n}"] = {"src": f"../images/{n:02d}.png", "role": im.get("role"),
                                            "subject": im.get("subject", "")}
    return {"id": sid, "duration": timing["duration"], "hook": sh.get("hook") or {},
            "cues": [{k: c[k] for k in ("i", "text", "keywords", "start", "end")} for c in timing["cues"]],
            "images": imgs}


def build_page(job, sid: str, scene_html: str, scene_js: str) -> Path:
    st = load(job.setting("shorts", "style"))
    data = short_data(job, sid)
    page = (BASE / "shell.html").read_text(encoding="utf-8")
    out = job.sub("motion", sid) / "index.html"
    pd = out.parent
    rep = {
        "{{TITLE}}": f"{sid} {data['hook'].get('line1', '')}",
        "{{FONTS_CSS}}": fonts_css(pd),
        "{{BASE_CSS}}": rel(BASE / "base.css", pd),
        "{{STYLE_CSS}}": rel(st["dir"] / "frame.css", pd),
        "{{GSAP}}": rel(GSAP, pd),
        "{{RUNTIME}}": rel(BASE / "runtime.js", pd),
        "{{SHORT_JSON}}": json.dumps(data, ensure_ascii=False).replace("</", "<\\/"),
        "{{SCENE_HTML}}": scene_html,
        "{{SCENE_JS}}": scene_js,
    }
    for k, v in rep.items():
        page = page.replace(k, v)
    # 브라우저에서 그냥 열면 음성과 함께 재생해 볼 수 있게(렌더에는 영향 없음)
    page = page.replace("</body>", f'<audio id="__audio" src="../audio/narration.wav" preload="auto"></audio>\n'
                                   '<script>if(!navigator.webdriver){document.addEventListener("click",function(){window.__play()});}</script>\n</body>')
    out.parent.mkdir(parents=True, exist_ok=True)
    if not (out.exists() and out.read_text(encoding="utf-8") == page):    # 같으면 다시 쓰지 않는다(영상 캐시가 안 깨지게)
        out.write_text(page, encoding="utf-8")
    return out
