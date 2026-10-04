"""s3-images — 대본의 그림 목록을 **Claude 가 SVG 일러스트로 직접 그린다** → PNG 로 바꿔 둔다.

Claude CLI(OAuth 로그인) 하나로 끝난다 — ChatGPT/Codex 는 쓰지 않는다.
  sticker     1024² 투명 PNG — 모션에서 흰 테두리 스티커로 오려 붙인다
  background  1024x1536 전체 배경
  diagram     1024x1536 도해(글자 없음)
쇼츠 하나 = Claude 호출 하나(그림 6~8장을 한 세트로, 같은 팔레트·선으로). 결과:
  images/<id>/NN.svg (원본, 사람이 고쳐도 됨) · NN.png (렌더) · baked.json (캐시)
  · 있는 파일은 건너뛴다 = 이어하기. 처음엔 `--limit 1` 로 한 편만 그려 보고 스타일을 확인한 뒤 나머지.
  · 사람이 패널에서 넣은 그림(「그림 넣기」)은 건드리지 않는다.
"""
from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from . import config, styles
from .job import Job
from .llm import claude_cli
from .log import detail, log

PROMPT = config.ROOT / "aside_shorts" / "prompts" / "svg_images.md"
NO_TEXT = "Absolutely no text, letters, numbers, captions, logos or watermarks anywhere in the image."
SIZE = {"sticker": (1024, 1024), "background": (1024, 1536), "diagram": (1024, 1536)}


def file_for(n: int) -> str:
    return f"{n:02d}.png"


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def full_prompt(item: Dict[str, Any], style: Dict[str, Any]) -> str:
    """사람이 다른 도구로 직접 그릴 때 복사해 가는 설명(패널 「설명 복사」)."""
    role = item.get("role", "sticker")
    look = (style.get("image") or {}).get(role, "")
    if role == "sticker":
        frame = ("A single isolated subject, the whole object fully visible and centered with generous empty "
                 "padding around it, cut-out style on a fully transparent background, no ground shadow, no scenery.")
    elif role == "background":
        frame = ("Vertical 9:16 full-bleed scene for a phone screen. Keep the upper third calm and simple "
                 "(text will be overlaid there). No people looking at the camera.")
    else:
        frame = "Vertical explanatory diagram illustration, clean composition, centered, on plain paper."
    return f"{item['prompt'].strip()}\n{frame}\nStyle: {look}\n{NO_TEXT}".strip()


def plan(job: Job, sid: str) -> List[Dict[str, Any]]:
    """그 쇼츠의 그림 목록 + 파일 이름 + 크기 + 설명."""
    sh = job.short(sid) or {}
    style = styles.load(job.setting("shorts", "style"))
    out = []
    for n, im in enumerate(sh.get("images") or [], 1):
        role = im.get("role", "sticker") if im.get("role") in SIZE else "sticker"
        w, h = SIZE[role]
        out.append({**im, "sid": sid, "file": file_for(n), "role": role, "size": f"{w}x{h}",
                    "_full": full_prompt(im, style)})
    return out


# ── Claude SVG ───────────────────────────────────────────────────────────────
def _brief(job: Job, sid: str, items: List[Dict[str, Any]]) -> str:
    sh = job.short(sid) or {}
    style = styles.load(job.setting("shorts", "style"))
    lines = [f"# 쇼츠 {sid} — {sh.get('perspective', '')} ({sh.get('unit', '')})",
             "대사: " + " / ".join(ln.get("text", "") for ln in sh.get("lines") or []), "",
             "## 스타일", style.get("svg", ""), "", "## 그릴 그림"]
    for it in items:
        lines.append(f"- key={it['key']}  role={it['role']}  viewBox 0 0 {it['size'].replace('x', ' ')}")
        lines.append(f"  내용: {it.get('subject', '')} — {it['prompt']}")
    lines += ["", "위 그림 전부를 <svg-image key=…> 블록으로 출력하라."]
    return "\n".join(lines)


def _parse(text: str) -> Dict[str, str]:
    out = {}
    for m in re.finditer(r'<svg-image\s+key="([^"]+)"\s*>\s*(<svg[\s\S]*?</svg>)\s*</svg-image>', text):
        out[m.group(1)] = m.group(2)
    return out


def _clean(svg: str) -> str:
    svg = re.sub(r"<script[\s\S]*?</script>", "", svg, flags=re.I)
    svg = re.sub(r"<foreignObject[\s\S]*?</foreignObject>", "", svg, flags=re.I)
    if "xmlns=" not in svg[:200]:
        svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    return svg


def rasterize(jobs: List[tuple]) -> None:
    """[(svg_text, png_path, w, h, transparent)] → PNG (헤드리스 Chromium)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for svg, png, w, h, transparent in jobs:
            pg = br.new_page(viewport={"width": w, "height": h})
            bg = "transparent" if transparent else "#ffffff"
            svg2 = re.sub(r"<svg\b", f'<svg width="{w}" height="{h}"', svg, count=1) \
                if not re.search(r"<svg[^>]*\swidth=", svg[:400]) else svg
            pg.set_content(f'<html><body style="margin:0;background:{bg}">{svg2}</body></html>')
            pg.screenshot(path=str(png), omit_background=transparent,
                          clip={"x": 0, "y": 0, "width": w, "height": h})
            pg.close()
        br.close()


def _draw_short(job: Job, sid: str, items: List[Dict[str, Any]], model: str) -> int:
    d = job.sub("images", sid)
    d.mkdir(parents=True, exist_ok=True)
    log(f"  {sid} 그림 {len(items)}장을 Claude 가 그리는 중 (2~6분) …")
    raw = claude_cli.text(_brief(job, sid, items), PROMPT.read_text(encoding="utf-8"), model=model)
    got = _parse(raw)
    todo, done = [], 0
    for it in items:
        svg = got.get(it["key"])
        if not svg:
            log(f"  ✗ {sid} {it['key']} 그림이 빠져서 왔어요 — 다시 누르면 빠진 것만 그려요")
            continue
        svg = _clean(svg)
        (d / it["file"].replace(".png", ".svg")).write_text(svg, encoding="utf-8")
        w, h = SIZE[it["role"]]
        todo.append((svg, d / it["file"], w, h, it["role"] == "sticker"))
    rasterize(todo)
    baked = config.read_json(d / "baked.json", {}) or {}
    for it in items:
        if (d / it["file"]).exists() and it["key"] in got:
            baked[it["file"]] = _hash(it["_full"])
            done += 1
    config.write_json(d / "baked.json", baked)
    log(f"  ✓ {sid} 그림 {done}장 완성")
    return len(items) - done


def run(job: Job, only=None, force: bool = False, limit: int = 0, **_) -> None:
    cfg = config.load()["image"]
    work: List[tuple] = []
    for sid in job.pick(only):
        d = job.sub("images", sid)
        baked = config.read_json(d / "baked.json", {}) or {}
        items = [it for it in plan(job, sid)
                 if force and baked.get(it["file"]) != "manual" or not (d / it["file"]).exists()]
        if items:
            work.append((sid, items))
    if limit:
        work = work[:limit]
    if not work:
        log("그림은 이미 다 있어요.")
        return
    model = config.load()["claude"].get("image_model") or config.load()["claude"]["motion_model"]
    workers = max(1, min(4, int(cfg.get("workers", 2))))
    missing = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_draw_short, job, sid, items, model): sid for sid, items in work}
        for f in as_completed(futs):
            try:
                missing += f.result()
            except claude_cli.ClaudeNotLoggedIn:
                raise
            except Exception as e:      # noqa: BLE001 — 한 편이 실패해도 나머지는 계속
                missing += 1
                log(f"  ✗ {futs[f]} 그림을 못 그렸어요 — 같은 버튼을 다시 누르면 이어서 해요")
                detail(f"  s3 {futs[f]} 실패: {e}")
    if missing:
        raise SystemExit(f"그림 {missing}장이 빠졌어요. 같은 버튼을 다시 누르면 빠진 것만 그려요.")
