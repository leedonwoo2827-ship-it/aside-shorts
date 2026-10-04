"""Claude — 구독 로그인한 `claude` CLI 를 headless(`-p`)로 부른다. API 키가 필요 없다.

  structured(prompt, system, schema)   JSON 스키마에 맞는 dict (대본)
  text(prompt, system, read_dirs=…)    마지막 답 글자 그대로 (모션 HTML). read_dirs 를 주면 그 폴더의
                                       그림을 Read 도구로 직접 보게 한다(이미지가 실제로 어떻게 생겼는지 보고 배치).

★ 프롬프트는 표준입력으로 UTF-8 바이트를 넘긴다 — 명령줄 인자로 넘기면 Windows 에서 한글이 깨지고 길이 제한에 걸린다.
★ 사용자 설정·MCP·세션 기록을 끄고(--setting-sources "" · --strict-mcp-config · --no-session-persistence)
  도구도 꼭 필요한 것만 연다 — 매번 같은 조건으로 돈다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import config
from ..log import detail


class ClaudeError(RuntimeError):
    pass


class ClaudeNotLoggedIn(ClaudeError):
    pass


class ClaudeLimit(ClaudeError):
    """사용 한도·과부하 — 기다렸다 이어서 한다(claude_wait)."""


def exe() -> Optional[str]:
    return shutil.which("claude") or shutil.which("claude.cmd")


def _run(prompt: str, system: str, *, model: str, schema: Optional[Dict[str, Any]] = None,
         read_dirs: Optional[List[Path]] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
    path = exe()
    if not path:
        raise ClaudeError("claude CLI 가 없습니다 — npm i -g @anthropic-ai/claude-code 후 claude 로 로그인")
    cfg = config.load()["claude"]
    with tempfile.TemporaryDirectory(prefix="aside-claude-") as tmp:
        sp = Path(tmp) / "system.md"
        sp.write_text(system, encoding="utf-8")
        args = [path, "-p", "--output-format", "json", "--model", model,
                "--system-prompt-file", str(sp), "--setting-sources", "",
                "--strict-mcp-config", "--no-session-persistence"]
        if cfg.get("effort"):
            args += ["--effort", str(cfg["effort"])]
        if read_dirs:
            args += ["--tools", "Read", "--allowedTools", "Read"]
            for d in read_dirs:
                args += ["--add-dir", str(Path(d).resolve())]
        else:
            args += ["--tools", ""]
        if schema:
            args += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        t0 = time.time()
        try:
            proc = subprocess.run(args, input=prompt.encode("utf-8"), capture_output=True,
                                  cwd=tmp, env=env, timeout=timeout or int(cfg["timeout_sec"]),
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise ClaudeError(f"Claude 응답 시간 초과 ({timeout or cfg['timeout_sec']}초)")
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    err = proc.stderr.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        from .claude_wait import is_limit
        low = (out + err).lower()
        if is_limit(out + err):
            raise ClaudeLimit((err or out)[:400])
        if "login" in low or "authenticat" in low or "/login" in low:
            raise ClaudeNotLoggedIn("claude 로그인이 필요합니다 — 터미널에서 claude 를 실행해 로그인하세요")
        raise ClaudeError(f"Claude 응답을 읽지 못했습니다 (code {proc.returncode}): {(err or out)[:400]}")
    detail(f"  claude {model} {time.time() - t0:.0f}초 · turns {data.get('num_turns')} · "
           f"in {((data.get('usage') or {}).get('input_tokens'))} out {((data.get('usage') or {}).get('output_tokens'))}")
    if data.get("is_error"):
        from .claude_wait import is_limit
        msg = str(data.get("result") or data.get("subtype") or "")
        status = data.get("api_error_status")
        if is_limit(msg) or status in (429, 529):
            raise ClaudeLimit(f"{msg[:400]} (status {status})")
        if "login" in msg.lower() or "auth" in msg.lower():
            raise ClaudeNotLoggedIn(msg[:300])
        raise ClaudeError(f"Claude 오류: {msg[:400]}")
    return data


def _patient(fn):
    """한도에 걸리면 풀릴 때까지 기다렸다 같은 호출을 다시 한다(밤새·며칠 무인 제작)."""
    from .claude_wait import wait
    while True:
        try:
            return fn()
        except ClaudeLimit as e:
            detail(f"  Claude 한도: {e}")
            if not wait(str(e)):
                raise


def structured(prompt: str, system: str, schema: Dict[str, Any], *, model: str,
               retries: Optional[int] = None) -> Dict[str, Any]:
    n = int(config.load()["claude"]["retries"]) if retries is None else retries
    last: Exception = ClaudeError("?")
    for attempt in range(n + 1):
        try:
            data = _patient(lambda: _run(prompt, system, model=model, schema=schema))
            res = data.get("structured_output")
            if res is None:
                res = json.loads(str(data.get("result") or "").strip().strip("`").removeprefix("json"))
            return res
        except (ClaudeNotLoggedIn, ClaudeLimit):
            raise
        except (ClaudeError, json.JSONDecodeError) as e:
            last = e
            detail(f"  claude structured 재시도 {attempt + 1}/{n}: {e}")
            time.sleep(5)
    raise ClaudeError(str(last))


def text(prompt: str, system: str, *, model: str, read_dirs: Optional[List[Path]] = None,
         timeout: Optional[int] = None) -> str:
    data = _patient(lambda: _run(prompt, system, model=model, read_dirs=read_dirs, timeout=timeout))
    return str(data.get("result") or "")


def auth_status() -> Dict[str, Any]:
    """`claude auth status --json` — {installed, logged_in, email, plan}. 빠르다(모델을 부르지 않는다)."""
    path = exe()
    if not path:
        return {"installed": False, "logged_in": False, "email": "", "plan": ""}
    try:
        r = subprocess.run([path, "auth", "status", "--json"], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        d = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except Exception as e:      # noqa: BLE001
        detail(f"claude auth status 실패: {e}")
        d = {}
    return {"installed": True, "logged_in": bool(d.get("loggedIn")), "email": d.get("email") or "",
            "plan": d.get("subscriptionType") or "", "method": d.get("authMethod") or ""}


def ping() -> tuple:
    """(ok, 설명) — doctor 용. 가장 싼 모델로 한 마디."""
    try:
        r = _run("ok 라고만 답해", "짧게 답한다", model="haiku", timeout=120)
        return True, str(r.get("result", ""))[:40]
    except ClaudeError as e:
        return False, str(e)
