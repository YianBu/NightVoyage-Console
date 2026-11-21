from __future__ import annotations

import ctypes
import atexit
import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from project_meta import NAME_ZH


ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

_OUTPUT_STREAM = None


def _setup_output() -> None:
    """pythonw 下 stdout/stderr 是 None；给日志系统一个真实文件句柄。"""
    global _OUTPUT_STREAM
    try:
        folder = ROOT / "logs"
        try:
            folder.mkdir(parents=True)
        except FileExistsError:
            pass
        _OUTPUT_STREAM = (folder / "console.log").open("a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _OUTPUT_STREAM
        if sys.stderr is None:
            sys.stderr = _OUTPUT_STREAM
        logging.raiseExceptions = False
    except Exception:
        pass


def _setup_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _report(exc: BaseException) -> None:
    try:
        folder = ROOT / "logs"
        try:
            folder.mkdir(parents=True)
        except FileExistsError:
            pass
        text = f"{datetime.now():%Y-%m-%d %H:%M:%S}\n{exc}\n\n{traceback.format_exc()}"
        (folder / "error.log").write_text(text, encoding="utf-8")
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(0, f"启动失败：{exc}\n\n详情见 logs/error.log",
                                         NAME_ZH, 0x10)
    except Exception:
        pass


def _life(message: str) -> None:
    try:
        folder = ROOT / "logs"
        try:
            folder.mkdir(parents=True)
        except FileExistsError:
            pass
        with (folder / "lifecycle.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")
    except Exception:
        pass


def main() -> int:
    _setup_output()
    _setup_dpi()
    _life("start")
    atexit.register(lambda: _life("exit"))
    try:
        import tkinter as tk
        import ui

        root = tk.Tk()
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
            root.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass
        ui.App(root)
        root.report_callback_exception = lambda exc, val, tb: _life(
            "tk: " + "".join(traceback.format_exception(exc, val, tb)).replace("\n", " | "))
        root.mainloop()
        _life("mainloop-return")
        return 0
    except Exception as exc:
        _report(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
