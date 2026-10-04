"""Windows 기본 선택 창 — 패널의 「폴더 지정하기」·「파일 선택하기」.

브라우저는 보안상 실제 경로를 알려 주지 않으므로, 같은 PC 에서 도는 서버가 대신 창을 띄운다.
tkinter 는 스레드를 가리므로 서버가 이 모듈을 **별도 프로세스**로 부르고 stdout 의 JSON 만 읽는다.
  python -m aside_shorts.pick folder   → {"folder": "...", "files": [...]}
  python -m aside_shorts.pick files    → {"files": [...]}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(kind: str) -> dict:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)     # 패널·Chrome 뒤에 숨지 않게
    root.update()
    if kind == "folder":
        from .manuscript import classify_sources
        d = filedialog.askdirectory(parent=root, title="원고 폴더 지정하기 (절 폴더 또는 .docx 가 있는 폴더)")
        files = [str(i["file"]) for i in classify_sources(Path(d))] if d else []
        out = {"folder": d or "", "files": files}
    else:
        fs = filedialog.askopenfilenames(parent=root, title="원고 파일 선택하기 (여러 개 가능)",
                                         filetypes=[("원고 Word", "*.docx"), ("텍스트", "*.txt *.md"), ("모든 파일", "*.*")])
        out = {"files": sorted(fs)}
    root.destroy()
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1] if len(sys.argv) > 1 else "folder"), ensure_ascii=False))
