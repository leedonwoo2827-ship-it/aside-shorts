"""s4-motion — Claude 가 대사·음성 타이밍·그림을 받아 모션그래픽(HTML+GSAP)을 짠다.

레퍼런스 영상(5:40~)의 방식 그대로: 대사 + 나레이션 + 그림 몇 장 + 스타일 레퍼런스를 Claude 에게 주고
"목소리에 맞춰" 움직이는 장면을 만들게 한다. 영상 생성 모델은 쓰지 않는다.

  motion/<id>/scene.html, scene.js   Claude 가 쓴 조각 (사람이 고쳐도 된다 — 그러면 --force 없이 다시 조립만)
  motion/<id>/index.html             틀(후크·자막·런타임)과 합친 완성 페이지 — 브라우저로 열어 클릭하면 음성과 재생
  motion/<id>/meta.json              캐시 키·점검 결과
만든 뒤 바로 브라우저로 열어 오류·타임라인 길이를 점검하고, 문제가 있으면 오류를 붙여 Claude 에게 고치게 한다.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Tuple

from . import config, styles
from .job import Job
from .llm import claude_cli
from .log import detail, log

PROMPT = config.ROOT / "aside_shorts" / "prompts" / "motion.md"
BANNED = ("setTimeout", "setInterval", "requestAnimationFrame", "Math.random", "Date.now", "new Date",
          "TL.play(", "repeat: -1", "repeat:-1")


def _hash(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def brief(job: Job, sid: str) -> str:
    sh = job.short(sid) or {}
    data = styles.short_data(job, sid)
    st = styles.load(job.setting("shorts", "style"))
    see = bool(config.load()["claude"]["see_images"])
    lines = sh.get("lines") or []
    out: List[str] = [
        f"# 쇼츠 {sid} — {sh.get('perspective', '')}",
        f"단원: {sh.get('unit', '')}",
        f"상단 후크(런타임이 표시): {data['hook'].get('line1', '')} / {data['hook'].get('line2', '')}",
        f"전체 길이: {data['duration']}초 (음성 시작 {data['cues'][0]['start']}초)",
        "",
        "## 나레이션 문장과 타이밍 (cue 번호 i = at(i) 의 i)",
    ]
    for c, ln in zip(data["cues"], lines):
        out.append(f"- i={c['i']}  {c['start']:.2f}~{c['end']:.2f}초  「{c['text']}」")
        out.append(f"    키워드: {', '.join(c['keywords']) or '-'}   연출 메모: {ln.get('visual', '')}")
    out += ["", "## 그림"]
    for n, (key, im) in enumerate(data["images"].items(), 1):
        path = (job.sub("images", sid) / f"{n:02d}.png").resolve()
        exists = path.exists()
        line = f"- {key}  role={im['role']}  내용: {im['subject']}"
        if see and exists:
            line += f"\n    파일(Read 로 열어 보기): {path}"
        elif not exists:
            line += "  (※ 아직 그림 파일 없음 — 자리만 잡아 둘 것)"
        out.append(line)
    note = (sh.get("motion_note") or "").strip()
    if note:
        out += ["", "## 사람의 수정 요청 (가장 우선)", note]
    out += ["", "## 스타일 레퍼런스", st["guide"], "",
            "위 정보로 장면을 만들어 <scene-html> 과 <scene-js> 두 블록만 출력하라."]
    return "\n".join(out)


def parse(text: str) -> Tuple[str, str]:
    h = re.search(r"<scene-html>(.*?)</scene-html>", text, re.S)
    j = re.search(r"<scene-js>(.*?)</scene-js>", text, re.S)
    if not h or not j:
        raise claude_cli.ClaudeError("모션 결과에 <scene-html>/<scene-js> 블록이 없습니다")
    js = re.sub(r"^\s*<script[^>]*>|</script>\s*$", "", j.group(1).strip())
    return h.group(1).strip(), js.strip()


def lint(js: str, html: str) -> List[str]:
    probs = [f"금지된 코드 사용: {b}" for b in BANNED if b in js]
    if re.search(r"(?<![\w.])gsap\.(to|from|fromTo)\(", js):
        probs.append("TL 밖의 독립 트윈(gsap.to/from) 사용 — TL.to/from 으로 바꿀 것")
    if re.search(r"@keyframes|animation\s*:", html):
        probs.append("CSS 애니메이션 사용 — GSAP 트윈으로 바꿀 것")
    if re.search(r"https?://", html + js):
        probs.append("외부 URL 사용 — 금지")
    return probs


def run(job: Job, only=None, force: bool = False, **_) -> None:
    from .s5_render import check
    cfg = config.load()["claude"]
    model = cfg["motion_model"]
    system = PROMPT.read_text(encoding="utf-8")
    style = job.setting("shorts", "style")
    for sid in job.pick(only):
        d = job.sub("motion", sid)
        sh = job.short(sid) or {}
        key = _hash(sh.get("lines"), sh.get("images"), sh.get("hook"), sh.get("motion_note"), style,
                    styles.load(style)["guide"], system,
                    sorted(p.name for p in job.sub("images", sid).glob("*.png")) if job.sub("images", sid).exists() else [])
        meta = config.read_json(d / "meta.json", {}) or {}
        have = (d / "scene.html").exists() and (d / "scene.js").exists()
        if have and meta.get("key") == key and not force:
            styles.build_page(job, sid, (d / "scene.html").read_text(encoding="utf-8"),
                              (d / "scene.js").read_text(encoding="utf-8"))
            detail(f"s4: {sid} 장면 그대로 — 페이지만 다시 조립")
            continue
        log(f"  {sid} 모션을 짜는 중이에요 (Claude, 3~8분) …")
        see = bool(cfg["see_images"]) and job.sub("images", sid).exists()
        prompt = brief(job, sid)
        raw = claude_cli.text(prompt, system, model=model,
                              read_dirs=[job.sub("images", sid)] if see else None)
        html, js = parse(raw)
        problems: List[str] = []
        for rnd in range(int(cfg["fix_rounds"]) + 1):
            (d / "scene.html").parent.mkdir(parents=True, exist_ok=True)
            (d / "scene.html").write_text(html, encoding="utf-8")
            (d / "scene.js").write_text(js, encoding="utf-8")
            page = styles.build_page(job, sid, html, js)
            problems = lint(js, html) + check(page, styles.short_data(job, sid)["duration"])
            if not problems:
                break
            detail(f"  {sid} 점검 문제 {len(problems)}개: {problems}")
            if rnd >= int(cfg["fix_rounds"]):
                break
            log(f"  {sid} 장면에 고칠 점이 있어 Claude 에게 다시 맡겨요 ({rnd + 1}차) …")
            fix = (prompt + "\n\n## 네가 앞서 만든 결과\n<scene-html>\n" + html + "\n</scene-html>\n<scene-js>\n" + js +
                   "\n</scene-js>\n\n## 브라우저 점검에서 나온 문제 — 모두 고쳐서 두 블록 전체를 다시 출력하라\n- " +
                   "\n- ".join(problems))
            html, js = parse(claude_cli.text(fix, system, model=model))
        config.write_json(d / "meta.json", {"key": key, "problems": problems, "model": model, "style": style})
        if problems:
            log(f"  △ {sid} 모션은 만들었지만 점검 문제가 남았어요: {problems[0][:80]}")
        else:
            log(f"  ✓ {sid} 모션 완성 — motion/{sid}/index.html 을 열어 클릭하면 미리 볼 수 있어요")
