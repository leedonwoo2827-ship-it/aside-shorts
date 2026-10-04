"""s0-rewrite — 교과서 원문(01_raw)을 **저작권 안전 개작본**(02_rewrite)으로 다시 쓴다.

260711 `textbook-safe-rewrite` 스킬과 같은 규칙(prompts/rewrite.md):
  개념어·정의문은 유지, 사례만 다른 사례로 교체, 문단 수·흐름(정의→자료→해석→특징)·분량 유지, 가짜 통계 금지.
Claude 가 문단을 분류·재작성하고, **검증은 코드가 한다**(통과 못 하면 문제를 붙여 한 번 더 맡긴다):
  · 문단 수 동일 · 소제목/{자료 생략} 그대로 · 전체 글자 수 95~110%
  · 핵심 개념어 전부 보존 · 사례 문단의 원문 대비 문자 유사도 0.7 이하(difflib)
결과:
  02_rewrite/<원본 이름>.docx   원문 docx 의 서식(문단 스타일)을 그대로 두고 글자만 바꾼 것
  02_rewrite/검증.json          문단별 종류·유사도·글자 수, 보존 용어, 바꾼 사례, 통과 여부
원본(01_raw)은 절대 고치지 않는다. 개작본이 이미 있으면 건너뛴다(--force 로 다시).
※ 표현 겹침을 줄이는 장치이지 법적 판단은 아니다 — 최종 확인은 사람이.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List

from . import config
from .job import Job
from .llm import claude_cli
from .log import detail, log
from .manuscript import REWRITE

PROMPT = config.ROOT / "aside_shorts" / "prompts" / "rewrite.md"
SIM_MAX = 0.7
LEN_MIN, LEN_MAX = 0.95, 1.10
_STR = {"type": "string"}
SCHEMA = {
    "type": "object", "required": ["keep_terms", "paragraphs", "swaps"],
    "properties": {
        "keep_terms": {"type": "array", "items": _STR},
        "paragraphs": {"type": "array", "items": {
            "type": "object", "required": ["i", "kind", "text"],
            "properties": {"i": {"type": "integer"}, "kind": {"type": "string", "enum": ["heading", "placeholder", "concept", "example"]},
                           "text": _STR}}},
        "swaps": {"type": "array", "items": {"type": "object", "required": ["from", "to"],
                                             "properties": {"from": _STR, "to": _STR}}},
    },
}


def paragraphs(path: Path) -> List[str]:
    """docx 문단 전체(빈 문단 포함 — 서식을 보존하며 다시 쓰려면 위치가 필요하다)."""
    import docx
    return [p.text for p in docx.Document(str(path)).paragraphs]


def sim(a: str, b: str) -> float:
    return round(SequenceMatcher(None, a, b).ratio(), 2)


def check(orig: List[str], res: Dict[str, Any]) -> tuple:
    """(문제 목록, 문단별 보고)"""
    probs: List[str] = []
    new = res.get("paragraphs") or []
    if len(new) != len(orig):
        return [f"문단 수가 다릅니다: 원문 {len(orig)} · 결과 {len(new)} — 번호 하나에 하나씩"], []
    rows = []
    for o, p in zip(orig, new):
        k, t = p.get("kind"), p.get("text", "")
        s = sim(o, t)
        rows.append({"i": p.get("i"), "kind": k, "sim": s, "len_orig": len(o), "len_new": len(t)})
        if k in ("heading", "placeholder") and t.strip() != o.strip():
            probs.append(f"{p.get('i')}번({k})은 원문 그대로여야 합니다")
        if k == "example" and s > SIM_MAX:
            probs.append(f"{p.get('i')}번 사례 문단이 원문과 너무 비슷합니다(유사도 {s} > {SIM_MAX}) — 다른 사례·문장으로 완전히 새로")
    lo, ln = sum(len(x) for x in orig), sum(len(p.get("text", "")) for p in new)
    ratio = round(ln / max(1, lo), 3)
    if not (LEN_MIN <= ratio <= LEN_MAX):
        probs.append(f"전체 글자 수 비율 {ratio} — {LEN_MIN}~{LEN_MAX} 사이로 맞출 것(원문 {lo}자)")
    whole = "\n".join(p.get("text", "") for p in new)
    miss = [t for t in res.get("keep_terms") or [] if t and t not in whole]
    if miss:
        probs.append("빠진 핵심 개념어: " + ", ".join(miss))
    if not any(p.get("kind") == "example" for p in new):
        probs.append("사례(example) 문단이 하나도 없습니다 — 사례 문단을 찾아 교체할 것")
    return probs, rows


def write_docx(src: Path, out: Path, texts: List[str]) -> None:
    """원문 docx 를 열어 문단마다 첫 run 에 새 글자, 나머지 run 은 비운다(문단 스타일 보존)."""
    import docx
    d = docx.Document(str(src))
    for p, t in zip(d.paragraphs, texts):
        if p.text == t:
            continue
        runs = p.runs
        if runs:
            runs[0].text = t
            for r in runs[1:]:
                r.text = ""
        else:
            p.add_run(t)
    out.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(out))


def rewrite_unit(job: Job, unit, model: str) -> bool:
    raw = unit.raw
    orig = paragraphs(raw)
    idx = [i for i, t in enumerate(orig) if t.strip()]          # 빈 문단은 건드리지 않는다
    body = [orig[i] for i in idx]
    numbered = "\n".join(f"[{n}] {t}" for n, t in enumerate(body))
    prompt = f"[교과] {job.get('title', '')}\n[단원] {unit.chapter} / {unit.section}\n\n[원문 문단 {len(body)}개]\n{numbered}"
    system = PROMPT.read_text(encoding="utf-8")
    log(f"「{unit.section or unit.name}」 원문을 안전하게 다시 쓰는 중이에요 (Claude, 2~4분) …")
    res = claude_cli.structured(prompt, system, SCHEMA, model=model)
    probs, rows = check(body, res)
    if probs:
        log(f"  검증에서 {len(probs)}가지가 걸려 Claude 에게 다시 맡겨요 …")
        detail(f"  s0 {unit.prefix} 1차 문제: {probs}")
        fix = prompt + "\n\n[앞선 결과의 문제 — 모두 고쳐서 전체를 다시]\n- " + "\n- ".join(probs)
        res = claude_cli.structured(fix, system, SCHEMA, model=model)
        probs, rows = check(body, res)
    new = list(orig)
    paras = res.get("paragraphs") or []
    if len(paras) == len(body):
        for i, p in zip(idx, paras):
            new[i] = p.get("text", orig[i])
    out = unit.dir / REWRITE / raw.name
    for old in (unit.dir / REWRITE).glob("*.docx") if (unit.dir / REWRITE).exists() else []:
        old.unlink()
    write_docx(raw, out, new)
    ex = [r for r in rows if r["kind"] == "example"]
    report = {
        "source": f"01_raw/{raw.name}", "output": f"{REWRITE}/{raw.name}", "passed": not probs, "problems": probs,
        "paragraphs": len(body), "chars_orig": sum(map(len, body)),
        "chars_new": sum(len(p.get("text", "")) for p in paras),
        "example_sim_max": max((r["sim"] for r in ex), default=None),
        "keep_terms": res.get("keep_terms") or [], "swaps": res.get("swaps") or [], "rows": rows,
        "note": "표현 겹침을 줄이는 자동 검증입니다. 저작권에 대한 법적 판단은 아니므로 최종 확인은 사람이 합니다.",
    }
    config.write_json(unit.dir / REWRITE / "검증.json", report)
    sw = ", ".join(f"{s['from']}→{s['to']}" for s in report["swaps"][:6])
    if probs:
        log(f"  △ {unit.prefix} 개작본은 만들었지만 검증 문제가 남았어요: {probs[0][:80]}")
    else:
        log(f"  ✓ {unit.prefix} 안전 개작 완료 — 사례 교체: {sw or '-'} · 사례 유사도 최대 {report['example_sim_max']}")
    return not probs


def run(job: Job, only=None, force: bool = False, **_) -> None:
    units = job.units
    if only:
        want = {w.rsplit("-", 1)[0] if w.count("-") >= 2 else w for w in only}
        units = [u for u in units if u.prefix in want] or units
    if not units:
        raise SystemExit("원고가 없어요 — 「새 작업·원고」에서 원고를 넣어 주세요")
    model = config.load()["claude"]["script_model"]
    for u in units:
        if not u.raw:
            if u.rewrite:
                detail(f"s0: {u.prefix} 원본 없이 개작본만 있음 — 그대로 씀")
                log(f"「{u.section or u.name}」 은 개작본만 있어요 (원본 없음) — 그대로 써요.")
            else:
                log(f"  ✗ {u.prefix} 원고 파일이 없어요")
            continue
        if u.rewrite and not force:
            detail(f"s0: {u.prefix} 개작본 있음 — 건너뜀")
            log(f"「{u.section or u.name}」 개작본은 이미 있어요.")
            continue
        rewrite_unit(job, u, model)
