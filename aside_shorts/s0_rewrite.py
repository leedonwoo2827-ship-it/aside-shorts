"""s0-rewrite — 교과서 원문(01_raw)을 **원문과 전혀 다른 해설 글**(02_rewrite)로 다시 쓴다.

형식(@dekmanfactory 설명란 스타일): 소제목 "(1) 시간적 관점" + 해설 3~4문단. **소제목 하나 = 쇼츠 한 편**,
그 글은 그대로 그 쇼츠의 유튜브 설명란 본문이 된다. (prompts/rewrite.md)
  개념어는 유지, 사례는 다른 사례로 교체, 문장은 전부 새로 — 원문과 전혀 다른 글.
검증은 코드가 한다(통과 못 하면 문제를 붙여 한 번 더 맡긴다):
  · 원문과 띄어쓰기 빼고 MAX_RUN(15)자 넘게 같은 구간 없음 · 문단 유사도 SIM_MAX(0.5) 이하
  · 핵심 개념어 전부 보존 · 소제목 3~5개, 소제목마다 3~4문단
결과:
  02_rewrite/<원본 이름>.docx   소제목(굵게)+문단 — 사람이 읽고 고쳐도 됨
  02_rewrite/sections.json      대본(s1)·유튜브 설명이 읽는 구조화 본(사람이 docx 를 고치면 docx 가 이긴다 — 아래 load_sections)
  02_rewrite/검증.json           겹침·유사도·보존어·바꾼 사례·통과 여부
원본(01_raw)은 절대 고치지 않는다. 개작본이 이미 있으면 건너뛴다(--force 로 다시).
※ 표현 겹침을 줄이는 자동 장치이지 법적 판단은 아니다 — 최종 확인은 사람이.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .job import Job
from .llm import claude_cli
from .log import detail, log
from .manuscript import REWRITE

PROMPT = config.ROOT / "aside_shorts" / "prompts" / "rewrite.md"
MAX_RUN = 15
SIM_MAX = 0.5
_STR = {"type": "string"}
SCHEMA = {
    "type": "object", "required": ["unit_title", "unit_topic", "keep_terms", "sections", "swaps"],
    "properties": {
        "unit_title": _STR, "unit_topic": _STR,
        "keep_terms": {"type": "array", "items": _STR},
        "sections": {"type": "array", "items": {
            "type": "object", "required": ["label", "title", "paragraphs"],
            "properties": {"label": _STR, "title": _STR, "paragraphs": {"type": "array", "items": _STR}}}},
        "swaps": {"type": "array", "items": {"type": "object", "required": ["from", "to"],
                                             "properties": {"from": _STR, "to": _STR}}},
    },
}
_LABEL = re.compile(r"^\(\s*\d+\s*\)")


def paragraphs(path: Path) -> List[str]:
    """docx 문단 전체(빈 문단 포함)."""
    import docx
    return [p.text for p in docx.Document(str(path)).paragraphs]


def _squash(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def longest_run(a: str, b: str) -> tuple:
    a, b = _squash(a), _squash(b)
    m = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size, a[m.a:m.a + m.size]


def check(orig: List[str], res: Dict[str, Any]) -> tuple:
    """(문제 목록, 문단별 보고) — orig 는 원문 문단들."""
    probs: List[str] = []
    rows = []
    src = "\n".join(orig)
    secs = res.get("sections") or []
    if not 3 <= len(secs) <= 5:
        probs.append(f"소제목은 3~5개여야 합니다(지금 {len(secs)}개)")
    for si, s in enumerate(secs, 1):
        if not _LABEL.match(s.get("label", "")):
            probs.append(f"{si}번째 소제목 label 은 \"({si}) 이름\" 꼴로")
        ps = s.get("paragraphs") or []
        if not 3 <= len(ps) <= 4:
            probs.append(f"{s.get('label')} 문단은 3~4개(지금 {len(ps)}개)")
        for pi, p in enumerate(ps, 1):
            run, frag = longest_run(p, src)
            sim = max((SequenceMatcher(None, p, o).ratio() for o in orig if len(o.strip()) > 20), default=0)
            rows.append({"section": s.get("label"), "p": pi, "run": run, "sim": round(sim, 2), "len": len(p)})
            if run > MAX_RUN:
                probs.append(f"{s.get('label')} {pi}문단이 원문과 {run}자 겹칩니다: 「{frag[:40]}」 — 완전히 다른 표현으로")
            if sim > SIM_MAX:
                probs.append(f"{s.get('label')} {pi}문단이 원문 문단과 비슷합니다(유사도 {sim:.2f}) — 문장 구조부터 새로")
    whole = "\n".join(p for s in secs for p in s.get("paragraphs") or [])
    miss = [t for t in res.get("keep_terms") or [] if t and _squash(t) not in _squash(whole)]
    if miss:
        probs.append("빠진 핵심 개념어: " + ", ".join(miss))
    return probs, rows


def write_docx(out: Path, res: Dict[str, Any]) -> None:
    import docx
    d = docx.Document()
    d.add_heading(res.get("unit_title") or "", level=1)
    for s in res.get("sections") or []:
        h = d.add_paragraph()
        h.add_run(s.get("label", "")).bold = True
        for p in s.get("paragraphs") or []:
            d.add_paragraph(p)
    out.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(out))


def load_sections(unit) -> Optional[Dict[str, Any]]:
    """개작본 구조. 사람이 docx 를 고쳤으면(파일이 더 새것) docx 를 다시 읽어 소제목·문단을 만든다."""
    sj = unit.dir / REWRITE / "sections.json"
    data = config.read_json(sj)
    if not data:
        return None
    rw = unit.rewrite
    if rw and rw.suffix == ".docx" and rw.stat().st_mtime > sj.stat().st_mtime + 2:
        secs, cur = [], None
        for t in (x.strip() for x in paragraphs(rw)):
            if not t or t == (data.get("unit_title") or "").strip():
                continue
            if _LABEL.match(t):
                cur = {"label": t, "title": "", "paragraphs": []}
                secs.append(cur)
            elif cur is not None:
                cur["paragraphs"].append(t)
        old = {s["label"]: s.get("title", "") for s in data.get("sections") or []}
        for s in secs:
            s["title"] = old.get(s["label"], "")
        if secs:
            data = {**data, "sections": secs, "edited_by_hand": True}
    return data


def rewrite_unit(job: Job, unit, model: str) -> bool:
    raw = unit.raw
    orig = [t for t in paragraphs(raw) if t.strip()] if raw.suffix == ".docx" else \
        [t for t in raw.read_text(encoding="utf-8", errors="ignore").splitlines() if t.strip()]
    numbered = "\n".join(f"[{n}] {t}" for n, t in enumerate(orig))
    prompt = f"[교과] {job.get('title', '')}\n[단원] {unit.chapter} / {unit.section}\n\n[원문 문단 {len(orig)}개]\n{numbered}"
    system = PROMPT.read_text(encoding="utf-8")
    log(f"「{unit.section or unit.name}」 원문을 전혀 다른 해설 글로 다시 쓰는 중이에요 (Claude, 2~4분) …")
    res = claude_cli.structured(prompt, system, SCHEMA, model=model)
    probs, rows = check(orig, res)
    for rnd in range(2):
        if not probs:
            break
        log(f"  검증에서 {len(probs)}가지가 걸려 Claude 에게 다시 맡겨요 ({rnd + 1}차) …")
        detail(f"  s0 {unit.prefix} 문제: {probs}")
        fix = prompt + "\n\n[앞선 결과]\n" + "\n".join(
            f"{s.get('label')}\n" + "\n".join(s.get("paragraphs") or []) for s in res.get("sections") or []) + \
            "\n\n[문제 — 모두 고쳐서 전체를 다시]\n- " + "\n- ".join(probs)
        res = claude_cli.structured(fix, system, SCHEMA, model=model)
        probs, rows = check(orig, res)
    d = unit.dir / REWRITE
    if d.exists():
        for old in d.iterdir():
            if old.is_file() and not old.name.startswith("~$"):
                old.unlink()
    write_docx(d / raw.with_suffix(".docx").name, res)
    config.write_json(d / "sections.json", {k: res.get(k) for k in ("unit_title", "unit_topic", "keep_terms", "sections", "swaps")})
    report = {
        "format": "sections", "source": f"01_raw/{raw.name}", "output": f"{REWRITE}/{raw.with_suffix('.docx').name}",
        "passed": not probs, "problems": probs, "sections": len(res.get("sections") or []),
        "chars_orig": sum(map(len, orig)), "chars_new": sum(r["len"] for r in rows),
        "max_run": max((r["run"] for r in rows), default=0), "example_sim_max": max((r["sim"] for r in rows), default=None),
        "limits": {"max_run": MAX_RUN, "sim": SIM_MAX},
        "keep_terms": res.get("keep_terms") or [], "swaps": res.get("swaps") or [], "rows": rows,
        "note": "원문과 겹치는 표현을 줄이는 자동 검증입니다. 저작권에 대한 법적 판단은 아니므로 최종 확인은 사람이 합니다.",
    }
    config.write_json(d / "검증.json", report)
    labels = ", ".join(s.get("label", "") for s in res.get("sections") or [])
    if probs:
        log(f"  △ {unit.prefix} 개작본은 만들었지만 검증 문제가 남았어요: {probs[0][:80]}")
    else:
        log(f"  ✓ {unit.prefix} 개작 완료 — {labels} · 원문과 최대 {report['max_run']}자 겹침 · 유사도 최대 {report['example_sim_max']}")
    return not probs


def run(job: Job, only=None, force: bool = False, **_) -> None:
    units = job.units
    if only:
        want = {w.rsplit("-", 1)[0] if w.count("-") >= 2 else w for w in only}
        units = [u for u in units if u.prefix in want] or units
    if not units:
        raise SystemExit("원고가 없어요 — 「① 원고」에서 원고를 넣어 주세요")
    model = config.load()["claude"]["script_model"]
    for u in units:
        if not u.raw:
            if u.rewrite:
                log(f"「{u.section or u.name}」 은 개작본만 있어요 (원본 없음) — 그대로 써요.")
            else:
                log(f"  ✗ {u.prefix} 원고 파일이 없어요")
            continue
        if u.rewrite and not force:
            log(f"「{u.section or u.name}」 개작본은 이미 있어요.")
            continue
        rewrite_unit(job, u, model)
