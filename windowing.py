from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass

import psutil
import win32api
import win32con
import win32gui
import win32process
from PIL import ImageGrab, Image


DWMWA_CLOAKED = 14


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    cls: str
    proc: str
    width: int
    height: int
    score: int = 0

    @property
    def label(self) -> str:
        return f"{self.title}  ·  {self.width}×{self.height}"


def _is_cloaked(hwnd: int) -> bool:
    value = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        return False
    return value.value != 0


def _proc_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except Exception:
        return ""


def _score(title: str, cls: str, proc: str, width: int, height: int) -> int:
    t = title.lower()
    p = proc.lower()
    score = 0
    if cls == "UnrealWindow":
        score += 8
    if "em-win64-shipping" in p:
        score += 12
    if "云游戏" in title:
        score += 8
    if "duet" in t or "abyss" in t:
        score += 6
    if "夜渊" in title or "交错" in title:
        score += 6
    if width >= 1200 and height >= 675:
        score += 2
    if height and abs(width / height - 16 / 9) < 0.04:
        score += 2
    if "文件资源管理器" in title or title == "Program Manager":
        score -= 20
    if "夜航操作台" in title or "灾厄推进器" in title:
        score -= 100
    return score


def _is_candidate(title: str, cls: str, proc: str) -> bool:
    if cls == "UnrealWindow":
        return True
    if "em-win64-shipping" in proc.lower():
        return True
    if "云游戏" in title:
        return True
    if "duet" in title.lower() or "abyss" in title.lower():
        return True
    if "夜渊" in title or "交错" in title:
        return True
    return False


def list_windows(only_candidates: bool = True) -> list[WindowInfo]:
    rows: list[WindowInfo] = []

    def callback(hwnd: int, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return
        if _is_cloaked(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return
        if "夜航操作台" in title or "灾厄推进器" in title:
            return
        try:
            cls = win32gui.GetClassName(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            width, height = right - left, bottom - top
        except Exception:
            return
        if width < 320 or height < 180:
            return
        proc = _proc_name(pid)
        if only_candidates and not _is_candidate(title, cls, proc):
            return
        rows.append(WindowInfo(hwnd, title, cls, proc, width, height,
                               _score(title, cls, proc, width, height)))

    win32gui.EnumWindows(callback, None)
    rows.sort(key=lambda row: (-row.score, -row.width * row.height))
    return rows


def find_window(hwnd: int) -> WindowInfo | None:
    if not hwnd or not win32gui.IsWindow(hwnd):
        return None
    try:
        title = win32gui.GetWindowText(hwnd).strip()
        cls = win32gui.GetClassName(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        width, height = right - left, bottom - top
    except Exception:
        return None
    if not title or width < 320 or height < 180:
        return None
    proc = _proc_name(pid)
    return WindowInfo(hwnd, title, cls, proc, width, height,
                      _score(title, cls, proc, width, height))


def client_origin(hwnd: int) -> tuple[int, int]:
    return win32gui.ClientToScreen(hwnd, (0, 0))


def is_minimized(hwnd: int) -> bool:
    return bool(win32gui.IsIconic(hwnd))


def is_foreground(hwnd: int) -> bool:
    return win32gui.GetForegroundWindow() == hwnd


def capture(hwnd: int) -> tuple[Image.Image | None, str]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        return None, "窗口不存在"
    if is_minimized(hwnd):
        return None, "窗口已最小化"
    try:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return None, "窗口尺寸无效"
        ox, oy = client_origin(hwnd)
        frame = ImageGrab.grab(bbox=(ox, oy, ox + width, oy + height), all_screens=True)
        return frame, ""
    except Exception as exc:
        return None, f"截图失败：{exc}"


def activate(hwnd: int) -> None:
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        foreground = win32gui.GetForegroundWindow()
        current_thread = win32api.GetCurrentThreadId()
        foreground_thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
        attached = False
        if foreground_thread and foreground_thread != current_thread:
            try:
                ctypes.windll.user32.AttachThreadInput(current_thread, foreground_thread, True)
                attached = True
            except Exception:
                attached = False
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.SetFocus(hwnd)
        if attached:
            try:
                ctypes.windll.user32.AttachThreadInput(current_thread, foreground_thread, False)
            except Exception:
                pass
    except Exception:
        # 前台锁偶尔会拒绝切换；下一次出弹窗时再试。
        pass
    time.sleep(0.12)


def click_background(hwnd: int, x: int, y: int) -> None:
    lparam = (int(y) << 16) | (int(x) & 0xFFFF)
    win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
    time.sleep(0.02)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
    time.sleep(0.04)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)


def click_foreground(hwnd: int, x: int, y: int) -> None:
    activate(hwnd)
    ox, oy = client_origin(hwnd)
    old = win32api.GetCursorPos()
    win32api.SetCursorPos((ox + int(x), oy + int(y)))
    time.sleep(0.04)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.04)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.05)
    try:
        win32api.SetCursorPos(old)
    except Exception:
        pass


def click(hwnd: int, x: int, y: int, mode: str = "auto") -> None:
    if mode == "foreground":
        click_foreground(hwnd, x, y)
    elif mode == "background":
        click_background(hwnd, x, y)
    else:
        click_background(hwnd, x, y)
