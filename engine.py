from __future__ import annotations

import threading
import time
from typing import Callable

import winsound

from vision import Reading, Vision
import windowing


Emit = Callable[[str, object], None]


class VisionHost:
    """识别引擎放到后台加载，避免窗口出现时卡住。"""

    def __init__(self) -> None:
        self.vision: Vision | None = None
        self.error = ""
        self.ready = threading.Event()
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            self.vision = Vision()
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.ready.set()


class Monitor:
    def __init__(self, host: VisionHost, hwnd: int, target: int, click_mode: str, emit: Emit) -> None:
        self._host = host
        self._hwnd = hwnd
        self._target = int(target)
        self._click_mode = click_mode
        self._emit = emit
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_sig: tuple[int | None, int | None] | None = None
        self._last_click = 0.0
        self._last_capture_error = 0.0
        self._announced = False
        self._ratio_warned = False
        self.click_count = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def set_target(self, target: int) -> None:
        with self._lock:
            if target != self._target:
                self._target = int(target)
                self._announced = False
                self._last_sig = None

    @property
    def target(self) -> int:
        with self._lock:
            return self._target

    def _log(self, level: str, message: str) -> None:
        self._emit("log", (level, message))

    def _state(self, kind: str, text: str) -> None:
        self._emit("state", (kind, text))

    def _run(self) -> None:
        self._state("loading", "正在加载识别引擎…")
        self._host.ready.wait(timeout=25)
        vision = self._host.vision
        if vision is None:
            self._state("error", "识别引擎加载失败")
            self._log("warn", f"识别引擎加载失败：{self._host.error or '未知原因'}")
            return
        self._log("good", "识别引擎已就绪")

        while not self._stop.is_set():
            info = windowing.find_window(self._hwnd)
            if info is None:
                self._state("error", "游戏窗口已关闭")
                time.sleep(1.0)
                continue

            if not self._ratio_warned and info.height and abs(info.width / info.height - 16 / 9) > 0.05:
                self._ratio_warned = True
                self._log("warn", f"窗口比例 {info.width}×{info.height} 不是 16:9，识别位置可能偏移")

            frame, error = windowing.capture(self._hwnd)
            if frame is None:
                now = time.monotonic()
                if now - self._last_capture_error > 3.0:
                    self._last_capture_error = now
                    self._state("error", error or "截图失败")
                    self._log("warn", error or "截图失败")
                time.sleep(0.6)
                continue

            try:
                reading = vision.read(frame)
            except Exception as exc:
                self._state("error", "识别异常")
                self._log("warn", f"识别异常：{exc}")
                time.sleep(1.0)
                continue

            if reading.found and reading.calamity:
                self._handle_dialog(reading, info)
            elif reading.found:
                self._state("ignored", reading.note or "已忽略")
            else:
                if self._announced:
                    self._state("reached", f"已到达目标 Lv.{self.target}")
                else:
                    self._state("waiting", "等待行动抉择…")

            self._stop.wait(0.32)

        self._state("stopped", "已停止")

    def _handle_dialog(self, reading: Reading, info: windowing.WindowInfo) -> None:
        current = reading.current
        next_level = reading.next_level
        if next_level is None:
            self._state("dialog", "检测到弹窗，等级未读清")
            return

        self._emit("levels", (current, next_level))
        self._state("dialog", f"行动抉择：Lv.{current} → Lv.{next_level}")

        target = self.target
        if next_level > target:
            if not self._announced:
                self._announced = True
                self._log("good", f"已到达目标 Lv.{target}，停止点击继续挑战")
                try:
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                except Exception:
                    pass
            self._state("reached", f"已到达目标 Lv.{target}")
            return

        now = time.monotonic()
        signature = (current, next_level)
        if signature == self._last_sig and now - self._last_click < 2.2:
            return

        x = int(info.width * 0.660)
        y = int(info.height * reading.button_y)
        self._log("info", f"识别到 Lv.{current}→Lv.{next_level}，目标 Lv.{target}，点击继续挑战")
        self._last_sig = signature
        self._last_click = now

        first_mode = self._click_mode
        modes = [first_mode]
        if first_mode == "auto":
            modes = ["background", "foreground"]

        for index, mode in enumerate(modes):
            if self._stop.is_set():
                return
            try:
                windowing.click(self._hwnd, x, y, mode)
            except Exception as exc:
                self._log("warn", f"点击失败：{exc}")
                continue
            self.click_count += 1
            self._emit("count", self.click_count)
            if self._wait_dialog_closed(vision=self._host.vision, seconds=2.4):
                self._log("good", "已继续，等待下一轮抉择")
                self._state("clicked", "已点击继续挑战")
                return
            if index + 1 < len(modes):
                self._log("warn", "后台点击未生效，改用前台点击")
                time.sleep(0.25)

        self._log("warn", "继续挑战点击未生效，2 秒后重试")
        time.sleep(2.0)

    def _wait_dialog_closed(self, vision: Vision | None, seconds: float) -> bool:
        if vision is None:
            return False
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._stop.is_set():
            time.sleep(0.32)
            frame, _ = windowing.capture(self._hwnd)
            if frame is None:
                continue
            try:
                reading = vision.read(frame, allow_full_scan=False)
            except Exception:
                continue
            if not reading.found:
                return True
        return False
