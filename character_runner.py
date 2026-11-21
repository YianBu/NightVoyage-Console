from __future__ import annotations

import re
import threading
import time
from typing import Callable

from character_mixin import (
    CUSTOM_SEQUENCE_KEY,
    CharacterRotationMixin,
)
from character_rotation import CHARACTER_ROTATIONS
from input_keys import KeySender
import windowing


# 主控角色只放通用循环；带「迷津」的是迷津副本的特化循环，
# 留给以后单独的迷津模块，不在这一栏里出现。
MAIN_ROTATIONS = {
    name: rotation for name, rotation in CHARACTER_ROTATIONS.items()
    if "迷津" not in name
}


# 黎瑟SP 准星下方的蓄力百分比（1600×900 基准：770,505 - 850,550）
CHARGE_PERCENT_BOX = (770 / 1600, 505 / 900, 850 / 1600, 550 / 900)
CHARGE_PERCENT_RE = re.compile(r"(\d{1,3})\s*%")


class _FallbackTicker:
    """「无」角色时退回到最基础的普攻循环。"""

    def __init__(self, runner: "CharacterRunner", interval: float = 0.22) -> None:
        self.runner = runner
        self.interval = interval
        self.last = 0.0

    def __call__(self) -> None:
        now = time.monotonic()
        if now - self.last >= self.interval:
            self.last = now
            self.runner.press_hotkey(self.runner.get_normal_attack_key())

    def reset(self) -> None:
        self.last = 0.0

    def touch(self) -> None:
        self.last = time.monotonic()

    def start_next_tick(self) -> None:
        self.last = time.monotonic()


class CharacterRunner(CharacterRotationMixin):
    """把共享角色循环接到当前项目的窗口/按键/OCR 上。"""

    character_config_key = "角色"
    rotation_table = MAIN_ROTATIONS

    def __init__(self, host, hwnd: int, character: str, keys: dict, input_mode: str,
                 require_foreground: bool, custom_sequence: str, lock_first: bool,
                 run_id: int, emit: Callable[[str, object], None], pause_check=None) -> None:
        self._host = host
        self._hwnd = hwnd
        self._emit = emit
        self._stop = threading.Event()
        self.run_id = int(run_id)
        self._pause_check = pause_check
        self._sender = KeySender(hwnd, input_mode)
        self._sender.on_bad_key = lambda key: self.log_warning(
            f"按键「{key}」无法识别，请检查主控角色按键设置")
        self._require_foreground = bool(require_foreground)
        self._lock_first = bool(lock_first)
        self.keys = keys
        self.config = {
            "角色": character,
            CUSTOM_SEQUENCE_KEY: custom_sequence or "终结技:1",
        }
        self.frame = None
        self.started_at = 0.0
        self.current_action = ""
        self._last_log = ""
        self._last_log_at = 0.0
        self._last_capture_error = 0.0
        self._running = False

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sender.release_all()
        except Exception:
            pass

    def run(self) -> None:
        try:
            self._emit("char_state", (self.run_id, "starting", "正在准备角色循环…"))
            while not self._stop.is_set() and self._pause_check and self._pause_check():
                self._emit("char_state", (self.run_id, "paused", "灾厄弹窗中，主控暂停"))
                self.sleep(0.2)
            while not self._stop.is_set() and self._require_foreground and not windowing.is_foreground(self._hwnd):
                self._emit("char_state", (self.run_id, "paused", "请把游戏窗口切到前台"))
                self.sleep(0.3)
            if self._stop.is_set():
                return

            self.setup_character()
            self.reset_sequence_state()
            if self._lock_first and not self.plan_has_lock():
                self.log_info("先锁定目标（%s）" % self.get_lock_key())
                self.press_hotkey(self.get_lock_key())
                self.sleep(0.4)

            self.started_at = time.time()
            self._running = True
            self._emit("char_started", (self.run_id, self.started_at))
            self._emit("char_state", (self.run_id, "running", f"运行中：{self.current_character()}"))

            while not self._stop.is_set():
                if self._pause_check and self._pause_check():
                    self._emit("char_state", (self.run_id, "paused", "灾厄弹窗中，主控暂停"))
                    self.sleep(0.2)
                    continue
                if windowing.find_window(self._hwnd) is None:
                    self._emit("char_state", (self.run_id, "error", "游戏窗口已关闭"))
                    self.sleep(1.0)
                    continue
                if self._require_foreground and not windowing.is_foreground(self._hwnd):
                    self._emit("char_state", (self.run_id, "paused", "游戏窗口不在前台，已暂停按键"))
                    self.sleep(0.25)
                    continue
                self.next_frame()
                self.play_character_sequence()
                self.sleep(0.05 if getattr(self, "charge_state", None) == "charging" else 0.2)
        except Exception as exc:
            self.log_warning(f"主控角色异常：{exc}")
            self._emit("char_state", (self.run_id, "error", f"主控角色异常：{exc}"))
        finally:
            self._running = False
            try:
                self._sender.release_all()
            except Exception:
                pass
            self._emit("char_stopped", self.run_id)
            self._emit("char_state", (self.run_id, "stopped", "主控角色已停止"))

    # ---------------------------------------------------------- 基础适配
    def current_character(self) -> str:
        return self.config.get("角色", "无")

    def log_info(self, message: str) -> None:
        now = time.monotonic()
        if message == self._last_log and now - self._last_log_at < 1.2:
            return
        self._last_log = message
        self._last_log_at = now
        self._emit("char_log", (self.run_id, "info", message))

    def log_warning(self, message: str) -> None:
        now = time.monotonic()
        if message == self._last_log and now - self._last_log_at < 2.0:
            return
        self._last_log = message
        self._last_log_at = now
        self._emit("char_log", (self.run_id, "warn", message))

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, float(seconds))
        while not self._stop.is_set():
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.05, left))

    def next_frame(self):
        frame, error = windowing.capture(self._hwnd)
        if frame is not None:
            self.frame = frame
        else:
            now = time.monotonic()
            if now - self._last_capture_error > 3.0:
                self._last_capture_error = now
                self.log_warning(error or "截图失败")
        return self.frame

    def create_skill_ticker(self):
        return _FallbackTicker(self)

    def read_charge_percent(self):
        vision = getattr(self._host, "vision", None)
        if vision is None or self.frame is None:
            return None
        try:
            text, _ = vision.read_text(self.frame, CHARGE_PERCENT_BOX, scale=2.0)
        except Exception as exc:
            self.log_warning(f"蓄力百分比识别失败：{exc}")
            return None
        raw = str(text or "").replace(" ", "").replace("O", "0").replace("o", "0")
        raw = raw.replace("l", "1").replace("I", "1").replace("％", "%")
        match = CHARGE_PERCENT_RE.search(raw) or re.search(r"(?<!\d)(\d{2,3})(?!\d)", raw)
        if not match:
            return None
        try:
            value = int(match.group(1))
        except (TypeError, ValueError):
            return None
        return value if 0 <= value <= 100 else None

    # ------------------------------------------------------------ 按键
    def get_combat_key(self) -> str:
        return str(self.keys.get("combat", "e"))

    def get_ultimate_key(self) -> str:
        return str(self.keys.get("ultimate", "q"))

    def get_geniemon_key(self) -> str:
        return str(self.keys.get("geniemon", "z"))

    def get_normal_attack_key(self) -> str:
        return str(self.keys.get("normal", "鼠标左键"))

    def get_lock_key(self) -> str:
        return str(self.keys.get("lock", "鼠标中键"))

    def send_key(self, key: str, down_time: float = 0.02, **kwargs) -> bool:
        return self._sender.send_key(key, down_time)

    def send_key_down(self, key: str) -> bool:
        parsed = self._sender_parse(key)
        if parsed is None:
            return False
        kind, code = parsed
        if kind == "mouse":
            self._sender.mouse_down(str(code))
        else:
            self._sender.key_down(int(code))
        return True

    def send_key_up(self, key: str) -> bool:
        parsed = self._sender_parse(key)
        if parsed is None:
            return False
        kind, code = parsed
        if kind == "mouse":
            self._sender.mouse_up(str(code))
        else:
            self._sender.key_up(int(code))
        return True

    def _sender_parse(self, key: str):
        from input_keys import parse_key
        parsed = parse_key(key)
        if parsed is None:
            self._sender._bad(str(key))
        return parsed

    def press_hotkey(self, key: str, down_time: float = 0.02) -> bool:
        return self._sender.send_key(key, down_time)

    def hotkey_down(self, key: str) -> bool:
        return self.send_key_down(key)

    def hotkey_up(self, key: str) -> bool:
        return self.send_key_up(key)

    def mouse_down(self, key: str = "left") -> None:
        self._sender.mouse_down(key)

    def mouse_up(self, key: str = "left") -> None:
        self._sender.mouse_up(key)

    def release_character_action(self, action: str) -> None:
        if self._stop.is_set():
            return
        self.current_action = action
        self._emit("char_action", (self.run_id, action))
        super().release_character_action(action)
