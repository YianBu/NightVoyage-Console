from __future__ import annotations

import time

import win32api
import win32con
import win32gui

import windowing


MOUSE_NAMES = {
    "鼠标左键": "left", "左键": "left", "left": "left",
    "鼠标右键": "right", "右键": "right", "right": "right",
    "鼠标中键": "middle", "中键": "middle", "middle": "middle",
}

KEY_NAMES = {
    "lshift": win32con.VK_LSHIFT, "shift": win32con.VK_SHIFT,
    "lalt": win32con.VK_LMENU, "alt": win32con.VK_MENU,
    "ctrl": win32con.VK_CONTROL, "control": win32con.VK_CONTROL,
    "space": win32con.VK_SPACE, "空格": win32con.VK_SPACE,
    "esc": win32con.VK_ESCAPE, "escape": win32con.VK_ESCAPE,
    "tab": win32con.VK_TAB, "enter": win32con.VK_RETURN,
    "return": win32con.VK_RETURN, "backspace": win32con.VK_BACK,
}

for number in range(1, 13):
    KEY_NAMES[f"f{number}"] = getattr(win32con, f"VK_F{number}")

MOUSE_FLAGS = {
    # button: (down event, up event, down message, up message, message flag)
    "left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP,
             win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP, win32con.MK_LBUTTON),
    "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP,
              win32con.WM_RBUTTONDOWN, win32con.WM_RBUTTONUP, win32con.MK_RBUTTON),
    "middle": (win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP,
               win32con.WM_MBUTTONDOWN, win32con.WM_MBUTTONUP, win32con.MK_MBUTTON),
}


def parse_key(value: str) -> tuple[str, object] | None:
    text = str(value or "").strip()
    if not text:
        return None
    button = MOUSE_NAMES.get(text.lower())
    if button:
        return "mouse", button
    low = text.lower()
    if low in KEY_NAMES:
        return "key", KEY_NAMES[low]
    if len(text) == 1:
        char = text.upper()
        if "A" <= char <= "Z" or "0" <= char <= "9":
            return "key", ord(char)
    return None


class KeySender:
    def __init__(self, hwnd: int, mode: str = "foreground") -> None:
        self.hwnd = hwnd
        self.mode = mode if mode in ("foreground", "background") else "foreground"
        self._held_keys: set[int] = set()
        self._held_mouse: set[str] = set()
        self._bad_keys: set[str] = set()
        self.on_bad_key = None

    def can_send(self) -> bool:
        if self.mode == "background":
            return True
        return windowing.is_foreground(self.hwnd)

    def _bad(self, key: str) -> None:
        if key in self._bad_keys:
            return
        self._bad_keys.add(key)
        if self.on_bad_key:
            self.on_bad_key(key)

    @staticmethod
    def _scan(vk: int) -> int:
        return int(win32api.MapVirtualKey(vk, 0)) & 0xFF

    def _post_key(self, vk: int, down: bool) -> None:
        scan = self._scan(vk)
        lparam = 1 | (scan << 16)
        if not down:
            lparam |= 0xC0000000
        win32gui.PostMessage(self.hwnd, win32con.WM_KEYDOWN if down else win32con.WM_KEYUP,
                             vk, lparam)

    def _physical_key(self, vk: int, down: bool) -> None:
        flags = 0 if down else win32con.KEYEVENTF_KEYUP
        win32api.keybd_event(vk, self._scan(vk), flags, 0)

    def key_down(self, vk: int) -> None:
        if not self.can_send():
            return
        if self.mode == "background":
            self._post_key(vk, True)
        else:
            self._physical_key(vk, True)
        self._held_keys.add(vk)

    def key_up(self, vk: int) -> None:
        if vk not in self._held_keys and self.mode == "foreground" and not self.can_send():
            return
        if self.mode == "background":
            self._post_key(vk, False)
        else:
            if not self.can_send():
                return
            self._physical_key(vk, False)
        self._held_keys.discard(vk)

    def send_key(self, key: str, down_time: float = 0.02) -> bool:
        parsed = parse_key(key)
        if parsed is None:
            self._bad(key)
            return False
        kind, code = parsed
        if kind == "mouse":
            self.click(str(code), down_time)
            return True
        self.key_down(int(code))
        time.sleep(max(0.0, down_time))
        self.key_up(int(code))
        return True

    def _client_cursor(self) -> tuple[int, int]:
        try:
            left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
            width, height = right - left, bottom - top
            x, y = win32gui.ScreenToClient(self.hwnd, win32api.GetCursorPos())
            if 0 <= x < width and 0 <= y < height:
                return int(x), int(y)
            return int(width / 2), int(height / 2)
        except Exception:
            return 1, 1

    def _post_mouse(self, button: str, down: bool) -> None:
        _, _, down_msg, up_msg, flag = MOUSE_FLAGS[button]
        x, y = self._client_cursor()
        lparam = (y << 16) | (x & 0xFFFF)
        win32gui.PostMessage(self.hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
        win32gui.PostMessage(self.hwnd, down_msg if down else up_msg,
                             flag if down else 0, lparam)

    def _physical_mouse(self, button: str, down: bool) -> None:
        down_flag, up_flag, _, _, _ = MOUSE_FLAGS[button]
        win32api.mouse_event(down_flag if down else up_flag, 0, 0, 0, 0)

    def mouse_down(self, button: str = "left") -> None:
        if button not in MOUSE_FLAGS or not self.can_send():
            return
        if self.mode == "background":
            self._post_mouse(button, True)
        else:
            self._physical_mouse(button, True)
        self._held_mouse.add(button)

    def mouse_up(self, button: str = "left") -> None:
        if button not in MOUSE_FLAGS:
            return
        if self.mode == "background":
            self._post_mouse(button, False)
        else:
            if not self.can_send():
                return
            self._physical_mouse(button, False)
        self._held_mouse.discard(button)

    def click(self, button: str = "left", down_time: float = 0.02) -> None:
        self.mouse_down(button)
        time.sleep(max(0.0, down_time))
        self.mouse_up(button)

    def release_all(self) -> None:
        for vk in list(self._held_keys):
            try:
                if self.mode == "background":
                    self._post_key(vk, False)
                else:
                    self._physical_key(vk, False)
            except Exception:
                pass
        self._held_keys.clear()
        for button in list(self._held_mouse):
            try:
                if self.mode == "background":
                    self._post_mouse(button, False)
                else:
                    self._physical_mouse(button, False)
            except Exception:
                pass
        self._held_mouse.clear()
