"""s1-script — 원고(절 하나)를 Claude 가 관점별 쇼츠 대본 여러 편으로 나눠 쓴다.

결과: shorts/<장>-<절>-<NN>.json 하나에 한 편(후크·문장·그림 목록·유튜브 메타).
캐시: shorts/_s1.json 에 원고 해시를 적어 두고, 원고가 그대로면 건너뛴다(--force 로 다시).
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import config
from .job import Job
from .llm import claude_cli
from .log import detail, log

PROMPT = config.ROOT / "aside_shorts" / "prompts" / "shorts_script.md"

_STR = {"type": "string"}
SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["shorts"],
    "properties": {"shorts": {"type": "array", "minItems": 1, "items": {
        "type": "object",
        "required": ["perspective", "hook", "lines", "images", "youtube"],
        "properties": {
            "perspective": _STR,
            "hook": {"type": "object", "required": ["line1", "line2"],
                     "properties": {"line1": _STR, "line2": _STR}},
            "lines": {"type": "array", "items": {
                "type": "object", "required": ["text", "say", "keywords", "visual"],
                "properties": {"text": _STR, "say": _STR, "visual": _STR,
                               "keywords": {"type": "array", "items": _STR}}}},
            "images": {"type": "array", "items": {
                "type": "object", "required": ["key", "role", "subject", "prompt"],
                "properties": {"key": _STR, "role": {"type": "string", "enum": ["sticker", "background", "diagram"]},
                               "subject": _STR, "prompt": _STR}}},
            "youtube": {"type": "object", "required": ["title", "description", "hashtags"],
                        "properties": {"title": _STR, "description": _STR,
                                       "hashtags": {"type": "array", "items": _STR}}},
        }}}},
}


def system_prompt() -> str:
    c = config.load()["shorts"]
    return (PROMPT.read_text(encoding="utf-8")
            .replace("{LINES_MIN}", str(c["lines_min"])).replace("{LINES_MAX}", str(c["lines_max"]))
            .replace("{IMAGES_MIN}", str(c["images_min"])).replace("{IMAGES_MAX}", str(c["images_max"]))
            .replace("{SECONDS}", str(c["target_seconds"]))
            .replace("{SYLLABLES}", str(int(c["target_seconds"]) * 8)))


def run(job: Job, only=None, force: bool = False, **_) -> None:
    units = job.units
    if not units:
        raise SystemExit("원고가 없습니다 — 「새 작업·원고」에서 원고를 넣어 주세요")
    if only:
        want = {w.rsplit("-", 1)[0] if w.count("-") >= 2 else w for w in only}
        units = [u for u in units if u.prefix in want] or units
    model = config.load()["claude"]["script_model"]
    for u in units:
        if not u.source:
            log(f"  · {u.prefix} 는 아직 안전 개작본(02_rewrite)이 없어요 — 「0 안전 개작」을 먼저 해 주세요.")
            continue
        cache = config.read_json(u.script_dir / "_s1.json", {}) or {}
        have = [i for i in job.ids() if i.startswith(u.prefix + "-")]
        if have and cache.get("hash") == u.hash and not force:
            detail(f"s1: {u.prefix} 개작본 그대로 — 건너뜀 ({len(have)}편)")
            log(f"「{u.section or u.name}」 대본은 이미 있어요 ({len(have)}편).")
            continue
        log(f"「{u.section or u.name}」 개작본으로 쇼츠 대본을 쓰는 중이에요 (1~3분) …")
        head = f"[교과] {job.get('title', '')}\n[단원] {u.chapter} / {u.section}\n\n[원고]\n"
        res = claude_cli.structured(head + u.text, system_prompt(), SCHEMA, model=model)
        shorts: List[Dict[str, Any]] = res.get("shorts") or []
        if not shorts:
            raise SystemExit("대본이 비어서 왔어요. 같은 버튼을 한 번 더 눌러 주세요.")
        for old in have:            # 편 수가 줄었을 수 있다 — 이전 대본 파일은 치운다(사람 수정 .edit.json 은 남긴다)
            (u.script_dir / f"{old}.json").unlink(missing_ok=True)
        for n, sh in enumerate(shorts, 1):
            sid = f"{u.prefix}-{n:02d}"
            sh = {"id": sid, "unit": f"{u.chapter} {u.section}".strip(), **sh}
            for ln in sh.get("lines") or []:      # 자막에 없는 키워드는 강조가 안 된다 — 걸러 둔다
                ln["keywords"] = [k for k in ln.get("keywords") or [] if k and k in ln.get("text", "")]
            for i, im in enumerate(sh.get("images") or [], 1):
                im["key"] = im.get("key") or f"img{i}"
            config.write_json(u.script_dir / f"{sid}.json", sh)
            log(f"  ✓ {sid} {sh['perspective']} — {sh['hook']['line1']} (문장 {len(sh['lines'])} · 그림 {len(sh['images'])})")
        config.write_json(u.script_dir / "_s1.json", {"hash": u.hash, "source": u.source.name})
