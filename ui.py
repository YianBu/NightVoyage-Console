from __future__ import annotations

import json
import queue
import random
import re
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from character_mixin import CUSTOM_CHARACTER, NO_CHARACTER
from character_runner import CharacterRunner, MAIN_ROTATIONS
from engine import Monitor, VisionHost
from project_meta import NAME_EN, NAME_ZH, REPO_URL, VERSION
import settings_store
import windowing


APP_TITLE = NAME_ZH
APP_SUB = f"{NAME_EN} · 游戏半自动操作台"
FONT = "Microsoft YaHei UI"

BG = "#070811"
PANEL = "#0f1224"
PANEL_2 = "#171b34"
PANEL_3 = "#080a14"
BORDER = "#2b2f5a"
TEXT = "#e9eaff"
MUTED = "#8c91bb"
ACCENT = "#b49bff"
SUCCESS = "#8ab4ff"
RED = "#ff7897"
BLUE = "#6ea8ff"

MODE_LABELS = {
    "自动（后台优先）": "auto",
    "仅后台消息": "background",
    "仅前台点击": "foreground",
}
MODE_NAMES = {value: key for key, value in MODE_LABELS.items()}

INPUT_LABELS = {
    "前台按键（推荐）": "foreground",
    "后台消息": "background",
}
INPUT_NAMES = {value: key for key, value in INPUT_LABELS.items()}

GAME_KEY_FIELDS = (
    ("interact", "交互"),
    ("combat", "战技"),
    ("ultimate", "终结技"),
    ("geniemon", "魔灵支援"),
    ("dodge", "闪避"),
    ("helix", "螺旋飞跃"),
    ("normal", "普攻"),
    ("shoot", "射击"),
    ("lock", "锁定"),
)

DEFAULT_GAME_KEYS = {
    "interact": "f",
    "combat": "e",
    "ultimate": "q",
    "geniemon": "z",
    "dodge": "lshift",
    "helix": "4",
    "normal": "鼠标左键",
    "shoot": "鼠标右键",
    "lock": "鼠标中键",
}

DEFAULT_CHARACTER = "黎瑟SP" if "黎瑟SP" in MAIN_ROTATIONS else next(iter(MAIN_ROTATIONS))


def rotation_summary(name: str) -> str:
    if name == NO_CHARACTER:
        return "不套角色手法：基础普攻循环"
    if name == CUSTOM_CHARACTER:
        return "按下方自定义序列执行，格式：动作:等待秒数, 动作:等待秒数"
    rotation = MAIN_ROTATIONS.get(name)
    if not rotation:
        return ""
    parts = []
    opening = rotation.get("opening") or ()
    if opening:
        parts.append("起手 " + " → ".join(action for action, _wait in opening))
    spam = rotation.get("spam")
    if spam:
        action = spam if isinstance(spam, str) else spam[0]
        parts.append("持续 " + action)
    for interval, actions in rotation.get("timed") or ():
        parts.append(f"每{float(interval):g}秒 " + "/".join(actions))
    if rotation.get("charge_shot"):
        parts.append("满蓄力射击")
    if rotation.get("loop"):
        parts.append("整段循环")
    text = " · ".join(parts)
    return text if len(text) <= 110 else text[:110] + "…"


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.configure(bg=BG)
        screen_width = self.root.winfo_screenwidth()
        self.root.geometry(f"860x880+{max(20, (screen_width - 880) // 2)}+10")
        self.root.minsize(820, 820)

        self.root_dir = Path(__file__).resolve().parent
        self.log_dir = self.root_dir / "logs"
        self.shot_dir = self.root_dir / "screenshots"
        for folder in (self.log_dir, self.shot_dir):
            try:
                folder.mkdir(parents=True)
            except FileExistsError:
                pass
        self.log_file = self.log_dir / f"run-{datetime.now():%Y%m%d}.log"

        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cfg = settings_store.load()
        self.host = VisionHost()
        self.monitor: Monitor | None = None
        self.char_runner: CharacterRunner | None = None
        self.char_thread: threading.Thread | None = None
        self.char_running = False
        self.char_started_at = 0.0
        self.char_run_id = 0
        self.calamity_running = False
        self.dialog_pause = threading.Event()
        self.calamity_state: tuple[str, str] = ("idle", "未运行")
        self.char_state: tuple[str, str] = ("idle", "未运行")
        self.active_tab = self.cfg.get("active_tab", "calamity")
        self.selected_hwnd = 0
        self.window_rows: list[windowing.WindowInfo] = []
        self.key_entries: dict[str, ttk.Entry] = {}
        self._syncing_target = False
        self._update_checking = False

        self._setup_style()
        self._setup_icon()
        self._build()
        self._refresh_windows(auto=True)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(120, self._drain)
        self.root.after(250, self._poll_engine)
        self.root.after(1000, self._tick_char_time)
        self.root.after(150, self._show_front)

    def _show_front(self) -> None:
        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.focus_force()
            self.root.after(700, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    # ------------------------------------------------------------------ UI
    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background=BG, foreground=TEXT, font=(FONT, 10))
        style.configure("Dark.TCombobox",
                        fieldbackground=PANEL_2, background=PANEL_2, foreground=TEXT,
                        arrowcolor=TEXT, bordercolor=BORDER, lightcolor=PANEL_2,
                        darkcolor=PANEL_2, padding=6)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", PANEL_2)], foreground=[("readonly", TEXT)],
                  selectbackground=[("readonly", PANEL_2)], selectforeground=[("readonly", TEXT)])
        style.configure("Dark.TSpinbox",
                        fieldbackground=PANEL_2, background=PANEL_2, foreground=TEXT,
                        arrowcolor=TEXT, bordercolor=BORDER, lightcolor=PANEL_2,
                        darkcolor=PANEL_2, insertcolor=TEXT, padding=6)
        style.map("Dark.TSpinbox", fieldbackground=[("readonly", PANEL_2)])
        style.configure("Dark.TEntry",
                        fieldbackground=PANEL_2, background=PANEL_2, foreground=TEXT,
                        bordercolor=BORDER, lightcolor=PANEL_2, darkcolor=PANEL_2,
                        insertcolor=TEXT, padding=4)
        style.configure("Dark.Vertical.TScrollbar",
                        background=PANEL_2, troughcolor=PANEL_3, bordercolor=PANEL,
                        arrowcolor=MUTED, lightcolor=PANEL_2, darkcolor=PANEL_2)
        style.map("Dark.Vertical.TScrollbar", background=[("active", BORDER)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL_2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", BORDER)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        self.root.option_add("*TCombobox*Listbox.font", (FONT, 9))

    def _setup_icon(self) -> None:
        ico = self.root_dir / "assets" / "app.ico"
        png = self.root_dir / "assets" / "app.png"
        try:
            if ico.exists():
                self.root.iconbitmap(default=str(ico))
            if png.exists():
                self._icon_image = tk.PhotoImage(file=str(png))
                self.root.iconphoto(True, self._icon_image)
        except Exception:
            pass

    def _card(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)

    def _label(self, parent: tk.Misc, text: str, size: int = 10, color: str = TEXT,
               bold: bool = False, bg: str = PANEL, **kwargs) -> tk.Label:
        weight = "bold" if bold else "normal"
        return tk.Label(parent, text=text, bg=bg, fg=color, font=(FONT, size, weight), **kwargs)

    def _button(self, parent: tk.Misc, text: str, command, fg: str = TEXT, bg: str = PANEL_2,
                active: str = BORDER, width: int = 0) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg,
            activebackground=active, activeforeground=fg, relief="flat", bd=0,
            highlightthickness=0, font=(FONT, 10), cursor="hand2",
            padx=14, pady=8, width=width,
        )

    def _build(self) -> None:
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=22, pady=18)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(4, weight=1)

        self._build_header(main)

        self._build_window_card(main)
        self._build_tabs(main)

        content = tk.Frame(main, bg=BG)
        content.grid(row=3, column=0, sticky="ew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)
        self.calamity_frame = tk.Frame(content, bg=BG)
        self.character_frame = tk.Frame(content, bg=BG)
        self.keys_frame = tk.Frame(content, bg=BG)
        for frame in (self.calamity_frame, self.character_frame, self.keys_frame):
            frame.grid(row=0, column=0, sticky="nsew")
            frame.grid_columnconfigure(0, weight=1)
        self._build_calamity(self.calamity_frame)
        self._build_character(self.character_frame)
        self._build_keys(self.keys_frame)

        self._build_log(main)

        self.status_line = self._label(main, "等待开始", size=8, color=MUTED, bg=BG)
        self.status_line.grid(row=5, column=0, sticky="w", pady=(8, 0))

        self._apply_target(initial=True)
        self._apply_character()
        self._switch_tab(self.active_tab, save=False)
        self._refresh_pill()
        self._append_log("info", "[灾厄] 半自动模式：只在灾厄行动抉择出现时点击“继续挑战”。")
        self._append_log("info", "[主控] 选角色和按键后开始；只按键，不接管走位。")

    def _build_header(self, main: tk.Frame) -> None:
        header = tk.Canvas(main, height=96, bg=BG, highlightthickness=0, bd=0)
        header.grid(row=0, column=0, sticky="ew")
        self.header_canvas = header
        self._header_image = self._make_header_image()
        header.create_image(0, 0, anchor="nw", image=self._header_image)
        header.create_text(8, 28, anchor="w", text=APP_TITLE, fill=TEXT,
                           font=(FONT, 20, "bold"))
        header.create_text(10, 56, anchor="w", text=APP_SUB, fill=MUTED,
                           font=(FONT, 9))
        self.pill = tk.Label(header, text="未运行", bg=BORDER, fg=MUTED,
                             font=(FONT, 9, "bold"), padx=14, pady=6)
        self.pill.place(relx=1.0, x=-18, y=12, anchor="ne")
        self.update_text_id = header.create_text(
            760, 66, anchor="ne", text=f"v{VERSION} · 检查更新", fill=BLUE,
            font=(FONT, 8), tags=("update",))
        header.tag_bind("update", "<Button-1>", self._check_update)
        header.tag_bind("update", "<Enter>", lambda _event: (
            header.itemconfigure(self.update_text_id, fill=ACCENT),
            header.configure(cursor="hand2")))
        header.tag_bind("update", "<Leave>", lambda _event: (
            header.itemconfigure(self.update_text_id, fill=BLUE),
            header.configure(cursor="")))

        def align_update(event):
            header.coords(self.update_text_id, max(10, event.width - 18), 66)

        header.bind("<Configure>", align_update)

    def _set_update_text(self, text: str, color: str = BLUE) -> None:
        try:
            self.header_canvas.itemconfigure(self.update_text_id, text=text, fill=color)
        except Exception:
            pass

    @staticmethod
    def _mix(first: tuple[int, int, int], second: tuple[int, int, int], t: float) -> tuple[int, int, int]:
        return tuple(int(a + (b - a) * t) for a, b in zip(first, second))

    def _make_header_image(self):
        width, height = 1000, 96
        image = Image.new("RGB", (width, height), (7, 8, 17))
        draw = ImageDraw.Draw(image)
        left = (7, 8, 17)
        middle = (20, 24, 58)
        right = (42, 23, 72)
        for x in range(width):
            t = x / (width - 1)
            color = self._mix(left, middle, t / 0.55) if t < 0.55 else self._mix(middle, right, (t - 0.55) / 0.45)
            draw.line([(x, 0), (x, height)], fill=color)

        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        for radius, alpha in ((260, 26), (190, 32), (120, 38), (70, 44)):
            glow_draw.ellipse((70 - radius, 48 - radius * 0.55, 70 + radius, 48 + radius * 0.55),
                              fill=(110, 80, 255, alpha))
        for radius, alpha in ((220, 18), (140, 24), (80, 30)):
            glow_draw.ellipse((790 - radius, 40 - radius * 0.45, 790 + radius, 40 + radius * 0.45),
                              fill=(50, 120, 255, alpha))
        glow = glow.filter(ImageFilter.GaussianBlur(24))
        image = Image.alpha_composite(image.convert("RGBA"), glow)

        grid = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        grid_draw = ImageDraw.Draw(grid)
        for x in range(0, width, 24):
            grid_draw.line([(x, 0), (x, height)], fill=(110, 120, 190, 26))
        for y in range(0, height, 24):
            grid_draw.line([(0, y), (width, y)], fill=(110, 120, 190, 20))
        image = Image.alpha_composite(image, grid)

        streaks = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        streak_draw = ImageDraw.Draw(streaks)
        for x in range(-height, width, 92):
            streak_draw.line([(x, 0), (x + height, height)], fill=(150, 120, 255, 18), width=2)
        image = Image.alpha_composite(image, streaks)

        stars = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        star_draw = ImageDraw.Draw(stars)
        rnd = random.Random(20261121)
        for _ in range(88):
            x = rnd.randrange(width)
            y = rnd.randrange(height)
            radius = rnd.choice([0, 0, 1, 1, 2])
            color = rnd.choice([(110, 168, 255, 120), (180, 155, 255, 135), (233, 234, 255, 90)])
            star_draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
        image = Image.alpha_composite(image, stars)

        line = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        line_draw = ImageDraw.Draw(line)
        blue = (91, 141, 255, 220)
        purple = (180, 155, 255, 220)
        for x in range(width):
            t = x / (width - 1)
            line_draw.point((x, 0), fill=self._mix(blue[:3], purple[:3], t) + (220,))
            line_draw.point((x, 1), fill=self._mix(blue[:3], purple[:3], t) + (160,))
            line_draw.point((x, 2), fill=self._mix(blue[:3], purple[:3], t) + (90,))
        image = Image.alpha_composite(image, line)
        return ImageTk.PhotoImage(image.convert("RGB"))

    def _build_window_card(self, main: tk.Frame) -> None:
        card = self._card(main)
        card.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        card.grid_columnconfigure(0, weight=1)
        top = tk.Frame(card, bg=PANEL)
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        top.grid_columnconfigure(1, weight=1)
        self._label(top, "游戏窗口", size=9, color=MUTED).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.window_box = ttk.Combobox(top, state="readonly", style="Dark.TCombobox", font=(FONT, 9))
        self.window_box.grid(row=0, column=1, sticky="ew")
        self.window_box.bind("<<ComboboxSelected>>", self._on_window_selected)
        self.refresh_btn = self._button(top, "刷新", lambda: self._refresh_windows(auto=True), width=5)
        self.refresh_btn.grid(row=0, column=2, padx=(10, 0))
        bottom = tk.Frame(card, bg=PANEL)
        bottom.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))
        bottom.grid_columnconfigure(0, weight=1)
        self.window_hint = self._label(bottom, "请选择正在运行的游戏或云游戏窗口", size=8, color=MUTED)
        self.window_hint.grid(row=0, column=0, sticky="w")
        self.show_all = tk.BooleanVar(value=False)
        self.show_all_check = tk.Checkbutton(
            bottom, text="显示全部窗口", variable=self.show_all, command=self._refresh_windows,
            bg=PANEL, fg=MUTED, activebackground=PANEL, activeforeground=TEXT,
            selectcolor=PANEL_2, font=(FONT, 8), bd=0, highlightthickness=0)
        self.show_all_check.grid(row=0, column=1, sticky="e")

    def _build_tabs(self, main: tk.Frame) -> None:
        wrap = tk.Frame(main, bg=BG)
        wrap.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        wrap.grid_columnconfigure(0, weight=1)
        bar = tk.Frame(wrap, bg=BG)
        bar.grid(row=0, column=0, sticky="w")
        self.tab_buttons: dict[str, tk.Button] = {}
        for key, text in (("calamity", "灾厄"), ("character", "主控角色"), ("keys", "按键设置")):
            button = tk.Button(bar, text=text, command=lambda value=key: self._switch_tab(value),
                               bg=BG, fg=MUTED, activebackground=PANEL_2, activeforeground=TEXT,
                               relief="flat", bd=0, highlightthickness=0,
                               font=(FONT, 10, "bold"), padx=16, pady=6, cursor="hand2")
            button.pack(side="left", padx=(0, 6))
            self.tab_buttons[key] = button
        tk.Frame(wrap, bg=BLUE, height=1).grid(row=1, column=0, sticky="ew", pady=(6, 0))
        tk.Frame(wrap, bg=ACCENT, height=1).grid(row=2, column=0, sticky="ew")

    def _build_calamity(self, parent: tk.Frame) -> None:
        parent.grid_rowconfigure(3, weight=1)
        card = self._card(parent)
        card.grid(row=0, column=0, sticky="ew", pady=(12, 0))
        card.grid_columnconfigure(0, weight=1)
        top = tk.Frame(card, bg=PANEL)
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        top.grid_columnconfigure(0, weight=1)
        self._label(top, "目标等级", size=9, color=MUTED).grid(row=0, column=0, sticky="w")
        self.target_big = self._label(top, "Lv.110", size=22, color=ACCENT, bold=True)
        self.target_big.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.target_hint = self._label(top, "等级按 5 递增：100 / 105 / 110 …", size=8, color=MUTED)
        self.target_hint.grid(row=2, column=0, sticky="w", pady=(2, 0))

        ctl = tk.Frame(top, bg=PANEL)
        ctl.grid(row=0, column=1, rowspan=3, sticky="e")
        self.minus_btn = self._button(ctl, "−5", lambda: self._step_target(-5), width=3)
        self.minus_btn.grid(row=0, column=0, padx=(0, 8))
        self.target_var = tk.StringVar(value=str(self.cfg["target_level"]))
        self.target_box = ttk.Spinbox(ctl, from_=1, to=9999, increment=5, width=7,
                                      textvariable=self.target_var, justify="center",
                                      style="Dark.TSpinbox", font=(FONT, 13, "bold"))
        self.target_box.grid(row=0, column=1)
        self.plus_btn = self._button(ctl, "+5", lambda: self._step_target(5), width=3)
        self.plus_btn.grid(row=0, column=2, padx=(8, 0))
        self.target_var.trace_add("write", self._on_target_typed)
        self.target_box.bind("<FocusOut>", lambda _event: self._apply_target())
        self.target_box.bind("<Return>", lambda _event: self._apply_target())

        quick = tk.Frame(card, bg=PANEL)
        quick.grid(row=1, column=0, sticky="w", padx=16, pady=(8, 0))
        self._label(quick, "常用", size=8, color=MUTED).pack(side="left", padx=(0, 8))
        for value in (100, 105, 110, 120, 130, 150):
            tk.Button(quick, text=str(value), command=lambda v=value: self._set_target(v),
                      bg=PANEL_2, fg=MUTED, activebackground=BORDER, activeforeground=TEXT,
                      relief="flat", bd=0, highlightthickness=0, font=(FONT, 8),
                      padx=8, pady=2, cursor="hand2").pack(side="left", padx=(0, 6))

        mode_row = tk.Frame(card, bg=PANEL)
        mode_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 12))
        mode_row.grid_columnconfigure(2, weight=1)
        self._label(mode_row, "点击方式", size=8, color=MUTED).grid(row=0, column=0, sticky="w")
        self.mode_box = ttk.Combobox(mode_row, state="readonly", style="Dark.TCombobox",
                                     values=list(MODE_LABELS), width=18, font=(FONT, 9))
        self.mode_box.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.mode_box.set(MODE_NAMES.get(self.cfg.get("click_mode", "auto"), MODE_NAMES["auto"]))
        self._label(mode_row, "后台点击无效时会自动改用前台点击", size=8, color=MUTED).grid(
            row=0, column=2, sticky="w", padx=(12, 0))

        stats = tk.Frame(parent, bg=BG)
        stats.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        for col in range(3):
            stats.grid_columnconfigure(col, weight=1)
        self.cur_value, _ = self._stat(stats, 0, "当前等级")
        self.next_value, _ = self._stat(stats, 1, "下一等级")
        self.count_value, _ = self._stat(stats, 2, "已继续")

        controls = tk.Frame(parent, bg=BG)
        controls.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        self.start_btn = self._button(controls, "开始监视", self._start_calamity, fg="#f4f5ff",
                                      bg="#5b5bd6", active="#7268ff", width=14)
        self.start_btn.pack(side="left")
        self.stop_btn = self._button(controls, "停止", self._stop_calamity, fg=TEXT, bg=PANEL_2, width=10)
        self.stop_btn.pack(side="left", padx=(10, 0))
        self.stop_btn.config(state="disabled")
        self.test_btn = self._button(controls, "识别测试", self._test, fg=MUTED, bg=PANEL_2, width=10)
        self.test_btn.pack(side="left", padx=(10, 0))

    def _build_character(self, parent: tk.Frame) -> None:
        parent.grid_rowconfigure(3, weight=1)
        card = self._card(parent)
        card.grid(row=0, column=0, sticky="ew", pady=(12, 0))
        card.grid_columnconfigure(1, weight=1)

        top = tk.Frame(card, bg=PANEL)
        top.grid(row=0, column=0, columnspan=4, sticky="ew", padx=16, pady=(12, 0))
        top.grid_columnconfigure(1, weight=1)
        self._label(top, "角色", size=9, color=MUTED).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.char_box = ttk.Combobox(top, state="readonly", style="Dark.TCombobox",
                                     values=list(MAIN_ROTATIONS) + [CUSTOM_CHARACTER],
                                     width=18, font=(FONT, 9))
        self.char_box.grid(row=0, column=1, sticky="w")
        self.char_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_character())
        self._label(top, "输入方式", size=9, color=MUTED).grid(row=0, column=2, sticky="w", padx=(18, 8))
        self.input_box = ttk.Combobox(top, state="readonly", style="Dark.TCombobox",
                                      values=list(INPUT_LABELS), width=16, font=(FONT, 9))
        self.input_box.grid(row=0, column=3, sticky="w")
        self.input_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_input_mode())

        option_row = tk.Frame(card, bg=PANEL)
        option_row.grid(row=1, column=0, columnspan=4, sticky="w", padx=16, pady=(8, 0))
        self.lock_first = tk.BooleanVar(value=bool(self.cfg.get("character_lock_first", True)))
        self.lock_check = tk.Checkbutton(
            option_row, text="开始前先锁定目标", variable=self.lock_first,
            bg=PANEL, fg=MUTED, activebackground=PANEL, activeforeground=TEXT,
            selectcolor=PANEL_2, font=(FONT, 9), bd=0, highlightthickness=0)
        self.lock_check.pack(side="left")
        self.char_hint = self._label(option_row, "", size=8, color=MUTED)
        self.char_hint.pack(side="left", padx=(14, 0))

        self.custom_row = tk.Frame(card, bg=PANEL)
        self.custom_row.grid(row=2, column=0, columnspan=4, sticky="ew", padx=16, pady=(8, 0))
        self.custom_row.grid_columnconfigure(1, weight=1)
        self._label(self.custom_row, "自定义序列", size=8, color=MUTED).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.custom_entry = ttk.Entry(self.custom_row, style="Dark.TEntry", font=(FONT, 9))
        self.custom_entry.grid(row=0, column=1, sticky="ew")
        self.custom_entry.insert(0, str(self.cfg.get("character_custom", "终结技:1")))

        self.char_desc = self._label(card, "", size=8, color=MUTED, wraplength=760, justify="left")
        self.char_desc.grid(row=3, column=0, columnspan=4, sticky="w", padx=16, pady=(8, 12))

        stats = tk.Frame(parent, bg=BG)
        stats.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        for col in range(3):
            stats.grid_columnconfigure(col, weight=1)
        self.char_name_value, _ = self._stat(stats, 0, "当前角色")
        self.char_action_value, _ = self._stat(stats, 1, "当前动作")
        self.char_time_value, _ = self._stat(stats, 2, "运行时长")

        controls = tk.Frame(parent, bg=BG)
        controls.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        self.char_start_btn = self._button(controls, "开始主控", self._start_character,
                                           fg="#f4f5ff", bg="#5b5bd6", active="#7268ff", width=14)
        self.char_start_btn.pack(side="left")
        self.char_stop_btn = self._button(controls, "停止", self._stop_character,
                                          fg=TEXT, bg=PANEL_2, width=10)
        self.char_stop_btn.pack(side="left", padx=(10, 0))
        self.char_stop_btn.config(state="disabled")

        self.char_box.set(self.cfg.get("character", DEFAULT_CHARACTER))
        if self.char_box.get() not in self.char_box.cget("values"):
            self.char_box.set(DEFAULT_CHARACTER)
        self.input_box.set(INPUT_NAMES.get(self.cfg.get("character_input", "foreground"),
                                           INPUT_NAMES["foreground"]))
        self._apply_input_mode()

    def _build_keys(self, parent: tk.Frame) -> None:
        parent.grid_rowconfigure(1, weight=1)
        card = self._card(parent)
        card.grid(row=0, column=0, sticky="ew", pady=(12, 0))
        card.grid_columnconfigure(0, weight=1)
        head = tk.Frame(card, bg=PANEL)
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        head.grid_columnconfigure(0, weight=1)
        self._label(head, "游戏按键", size=10, color=TEXT, bold=True).grid(row=0, column=0, sticky="w")
        self.reset_keys_btn = self._button(head, "恢复默认", self._reset_keys, fg=MUTED,
                                           bg=PANEL_2, width=8)
        self.reset_keys_btn.grid(row=0, column=1, sticky="e")
        self._label(head, "与游戏内按键设置保持一致；鼠标键写鼠标左键 / 鼠标右键 / 鼠标中键",
                    size=8, color=MUTED).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))

        grid = tk.Frame(card, bg=PANEL)
        grid.grid(row=1, column=0, sticky="w", padx=16, pady=(8, 4))
        saved = self.cfg.get("game_keys", DEFAULT_GAME_KEYS)
        for index, (key, label) in enumerate(GAME_KEY_FIELDS):
            row, column = divmod(index, 3)
            group = tk.Frame(grid, bg=PANEL)
            group.grid(row=row, column=column, sticky="w", padx=(0, 34), pady=5)
            self._label(group, label, size=9, color=MUTED).pack(side="left", padx=(0, 8))
            entry = ttk.Entry(group, style="Dark.TEntry", width=15, font=(FONT, 9))
            entry.insert(0, str(saved.get(key, DEFAULT_GAME_KEYS[key])))
            entry.pack(side="left")
            self.key_entries[key] = entry
        self._label(card, "键盘键可填 e、q、f、lshift、4；鼠标键可填鼠标左键、鼠标右键、鼠标中键",
                    size=8, color=MUTED).grid(row=2, column=0, sticky="w", padx=16, pady=(4, 12))

    def _reset_keys(self) -> None:
        for key, _label in GAME_KEY_FIELDS:
            entry = self.key_entries.get(key)
            if entry is None:
                continue
            entry.delete(0, "end")
            entry.insert(0, DEFAULT_GAME_KEYS[key])
        self._append_log("info", "[设置] 游戏按键已恢复默认")

    def _build_log(self, main: tk.Frame) -> None:
        card = self._card(main)
        card.grid(row=4, column=0, sticky="nsew", pady=(14, 0))
        card.grid_rowconfigure(1, weight=1)
        card.grid_columnconfigure(0, weight=1)
        head = tk.Frame(card, bg=PANEL)
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))
        self._label(head, "运行记录", size=9, color=MUTED).pack(side="left")
        self.engine_hint = self._label(head, "识别引擎加载中…", size=8, color=MUTED)
        self.engine_hint.pack(side="right")
        self.clear_btn = self._button(head, "清空记录", self._clear_log, fg=MUTED, bg=PANEL, width=8)
        self.clear_btn.pack(side="right", padx=(0, 10))
        wrap = tk.Frame(card, bg=PANEL_3)
        wrap.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        self.log_text = tk.Text(wrap, bg=PANEL_3, fg="#cfd6e4", insertbackground=TEXT,
                                relief="flat", bd=0, highlightthickness=0, wrap="word",
                                font=(FONT, 9), padx=10, pady=8, height=10)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.log_text.yview,
                               style="Dark.Vertical.TScrollbar")
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set, state="disabled")
        self.log_text.tag_configure("info", foreground="#b9bede")
        self.log_text.tag_configure("good", foreground=SUCCESS)
        self.log_text.tag_configure("warn", foreground=ACCENT)
        self.log_text.tag_configure("error", foreground=RED)
        self.log_text.tag_configure("time", foreground="#5c6284")

    def _stat(self, parent: tk.Frame, column: int, title: str) -> tuple[tk.Label, tk.Frame]:
        card = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        self._label(card, title, size=8, color=MUTED).pack(anchor="w", padx=14, pady=(10, 0))
        value = self._label(card, "--", size=16, color=TEXT, bold=True)
        value.pack(anchor="w", padx=14, pady=(1, 10))
        return value, card

    def _switch_tab(self, name: str, save: bool = True) -> None:
        if name not in ("calamity", "character", "keys"):
            name = "calamity"
        self.active_tab = name
        if name == "character":
            self.character_frame.tkraise()
        elif name == "keys":
            self.keys_frame.tkraise()
        else:
            self.calamity_frame.tkraise()
        for key, button in self.tab_buttons.items():
            active = key == name
            button.config(bg=PANEL_2 if active else BG, fg=ACCENT if active else MUTED)
        if save:
            self.cfg["active_tab"] = name
        self._refresh_pill()

    # ------------------------------------------------------------ data flow
    def _emit(self, kind: str, payload: object) -> None:
        self.queue.put((kind, payload))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    level, message = payload  # type: ignore[misc]
                    self._append_log(str(level), "[灾厄] " + str(message))
                elif kind == "state":
                    state_kind, message = payload  # type: ignore[misc]
                    self._set_calamity_state(str(state_kind), str(message))
                elif kind == "levels":
                    current, nxt = payload  # type: ignore[misc]
                    self.cur_value.config(text=f"Lv.{current}" if current else "--")
                    self.next_value.config(text=f"Lv.{nxt}" if nxt else "--")
                elif kind == "count":
                    self.count_value.config(text=str(payload))
                elif kind == "char_log":
                    run_id, level, message = payload  # type: ignore[misc]
                    if int(run_id) == self.char_run_id:
                        self._append_log(str(level), "[主控] " + str(message))
                elif kind == "char_state":
                    run_id, state_kind, message = payload  # type: ignore[misc]
                    if int(run_id) == self.char_run_id:
                        self._set_char_state(str(state_kind), str(message))
                elif kind == "char_action":
                    run_id, action = payload  # type: ignore[misc]
                    if int(run_id) == self.char_run_id:
                        self.char_action_value.config(text=str(action))
                elif kind == "char_started":
                    run_id, started = payload  # type: ignore[misc]
                    if int(run_id) == self.char_run_id:
                        self.char_started_at = float(started)
                        self.char_time_value.config(text="00:00:00")
                elif kind == "char_stopped":
                    if int(payload) == self.char_run_id:
                        self._finish_character()
                elif kind == "update":
                    tag, url = payload  # type: ignore[misc]
                    self._update_checking = False
                    self._set_update_text(f"v{VERSION} · 检查更新", BLUE)
                    if tag and self._version_tuple(tag) > self._version_tuple(VERSION):
                        self._append_log("good", f"[更新] 发现新版本 {tag}，正在打开下载页")
                        webbrowser.open(str(url))
                    else:
                        self._append_log("info", f"[更新] 已是最新版本 v{VERSION}")
                elif kind == "update_none":
                    self._update_checking = False
                    self._set_update_text(f"v{VERSION} · 检查更新", BLUE)
                    self._append_log("warn", "[更新] 仓库还没有 Release，已打开发布页")
                    webbrowser.open(f"{REPO_URL}/releases")
                elif kind == "update_error":
                    self._update_checking = False
                    self._set_update_text(f"v{VERSION} · 检查更新", BLUE)
                    self._append_log("warn", f"[更新] 检查失败：{payload}")
                elif kind == "test_done":
                    busy = self.calamity_running or self.char_running
                    self.test_btn.config(state="normal" if not busy else "disabled")
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def _poll_engine(self) -> None:
        if self.host.ready.is_set():
            if self.host.vision is not None:
                self.engine_hint.config(text="识别引擎就绪", fg=SUCCESS)
            else:
                self.engine_hint.config(text="识别引擎加载失败", fg=RED)
        else:
            self.root.after(250, self._poll_engine)

    # ------------------------------------------------------------- update
    @staticmethod
    def _version_tuple(text: str) -> tuple[int, ...]:
        numbers = re.findall(r"\d+", str(text or ""))
        return tuple(int(number) for number in numbers[:4]) or (0,)

    def _check_update(self, _event=None) -> None:
        if self._update_checking:
            return
        self._update_checking = True
        self._set_update_text(f"v{VERSION} · 检查中…", MUTED)
        self._append_log("info", f"[更新] 当前 v{VERSION}，正在检查 GitHub Release…")
        threading.Thread(target=self._check_update_worker, daemon=True).start()

    def _check_update_worker(self) -> None:
        api = REPO_URL.replace("https://github.com/", "https://api.github.com/repos/") + "/releases/latest"
        try:
            request = urllib.request.Request(api, headers={"User-Agent": "NightVoyage-Console"})
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            tag = str(data.get("tag_name") or "").strip()
            url = data.get("html_url") or f"{REPO_URL}/releases/latest"
            self.queue.put(("update", (tag, url)))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.queue.put(("update_none", None))
            else:
                self.queue.put(("update_error", f"HTTP {exc.code}"))
        except Exception as exc:
            self.queue.put(("update_error", str(exc)))

    # ------------------------------------------------------------ windows
    def _refresh_windows(self, auto: bool = False) -> None:
        keep = self.selected_hwnd
        self.window_rows = windowing.list_windows(only_candidates=not self.show_all.get())
        labels = [row.label for row in self.window_rows]
        self.window_box.config(values=labels)

        index = -1
        if keep:
            index = next((i for i, row in enumerate(self.window_rows) if row.hwnd == keep), -1)
        if index < 0:
            saved = int(self.cfg.get("window_hwnd", 0) or 0)
            index = next((i for i, row in enumerate(self.window_rows) if row.hwnd == saved), -1)
        if index < 0 and auto and self.window_rows:
            best = self.window_rows[0]
            if best.score >= 8:
                index = 0

        if index >= 0:
            self.window_box.current(index)
            self.selected_hwnd = self.window_rows[index].hwnd
            row = self.window_rows[index]
            self.window_hint.config(text=f"已选：{row.cls or '窗口'}  ·  {row.width}×{row.height}")
        else:
            self.window_box.set("")
            self.selected_hwnd = 0
            if self.window_rows:
                self.window_hint.config(text=f"找到 {len(self.window_rows)} 个窗口，请选择游戏窗口")
            else:
                self.window_hint.config(text="未找到游戏窗口，请先打开游戏；如已打开，勾选右侧“显示全部窗口”")

    def _on_window_selected(self, _event=None) -> None:
        index = self.window_box.current()
        if 0 <= index < len(self.window_rows):
            row = self.window_rows[index]
            self.selected_hwnd = row.hwnd
            self.window_hint.config(text=f"已选：{row.cls or '窗口'}  ·  {row.width}×{row.height}")
            self.cfg["window_hwnd"] = row.hwnd

    # ------------------------------------------------------------- target
    def _on_target_typed(self, *_args) -> None:
        if self._syncing_target:
            return
        try:
            value = int(self.target_var.get().strip())
        except Exception:
            self.target_big.config(text="Lv.--")
            return
        value = max(5, min(9999, value))
        value = int(round(value / 5.0) * 5) or 5
        self.target_big.config(text=f"Lv.{value}")
        self.target_hint.config(text="等级按 5 递增：100 / 105 / 110 …", fg=MUTED)
        if self.monitor is not None:
            self.monitor.set_target(value)

    def _apply_target(self, initial: bool = False) -> None:
        if self._syncing_target:
            return
        raw = self.target_var.get().strip()
        try:
            value = int(raw)
        except Exception:
            if not initial:
                self.target_hint.config(text="请输入数字等级", fg=RED)
            return
        value = max(5, min(9999, value))
        value = int(round(value / 5.0) * 5)
        if value < 5:
            value = 5
        if str(value) != raw:
            self._syncing_target = True
            self.target_var.set(str(value))
            self._syncing_target = False
        self.target_big.config(text=f"Lv.{value}")
        self.target_hint.config(text="等级按 5 递增：100 / 105 / 110 …", fg=MUTED)
        if self.monitor is not None:
            self.monitor.set_target(value)

    def _target_value(self) -> int:
        try:
            value = int(self.target_var.get().strip())
        except Exception:
            value = 110
        value = max(5, min(9999, value))
        return int(round(value / 5.0) * 5) or 5

    def _step_target(self, delta: int) -> None:
        self._set_target(self._target_value() + delta)

    def _set_target(self, value: int) -> None:
        self.target_var.set(str(max(5, value)))
        self._apply_target()

    # ------------------------------------------------------------ calamity
    def _start_calamity(self) -> None:
        if self.selected_hwnd == 0:
            self._append_log("warn", "[灾厄] 请先选择游戏窗口")
            self._set_calamity_state("error", "请先选择游戏窗口")
            return
        info = windowing.find_window(self.selected_hwnd)
        if info is None:
            self._append_log("warn", "[灾厄] 所选窗口已失效，请刷新后重新选择")
            self._set_calamity_state("error", "窗口不可用")
            self._refresh_windows(auto=False)
            return

        target = self._target_value()
        self._set_target(target)
        mode = MODE_LABELS.get(self.mode_box.get(), "auto")
        self.cfg.update({
            "target_level": target,
            "click_mode": mode,
            "window_hwnd": self.selected_hwnd,
            "active_tab": "calamity",
        })
        settings_store.save(self.cfg)

        if self.monitor is not None:
            self.monitor.stop()
        self.monitor = Monitor(self.host, self.selected_hwnd, target, mode, self._emit)
        self.monitor.start()
        self.calamity_running = True
        self.dialog_pause.clear()
        self.calamity_state = ("running", f"目标 Lv.{target} · 等待行动抉择")
        self.cur_value.config(text="--")
        self.next_value.config(text="--")
        self.count_value.config(text="0")
        self._set_running_ui("calamity", True)
        self._switch_tab("calamity", save=False)
        self._append_log("good", f"[灾厄] 开始监视：目标 Lv.{target}，窗口 {info.width}×{info.height}")
        self._refresh_pill()

    def _stop_calamity(self, wait: bool = True) -> None:
        if self.monitor is not None:
            self._append_log("info", "[灾厄] 正在停止…")
            self.monitor.stop()
            self.monitor = None
            self._append_log("info", "[灾厄] 已停止")
        self.calamity_running = False
        self.dialog_pause.clear()
        self._set_running_ui("calamity", False)
        self._refresh_pill()

    def _test(self) -> None:
        if self.selected_hwnd == 0:
            self._append_log("warn", "[灾厄] 请先选择游戏窗口")
            return
        self.test_btn.config(state="disabled")
        self._append_log("info", "[灾厄] 正在截取当前画面做识别测试…")
        threading.Thread(target=self._test_worker, daemon=True).start()

    def _test_worker(self) -> None:
        try:
            self.host.ready.wait(timeout=30)
            vision = self.host.vision
            if vision is None:
                self._emit("log", ("error", "识别引擎不可用"))
                return
            frame, error = windowing.capture(self.selected_hwnd)
            if frame is None:
                self._emit("log", ("warn", error or "截图失败"))
                return
            path = self.shot_dir / f"diagnose-{datetime.now():%Y%m%d-%H%M%S}.png"
            try:
                frame.save(path)
            except Exception:
                pass
            reading = vision.read(frame, allow_full_scan=True)
            self._emit("log", ("info", f"标题：{reading.title or '未读到'}；继续：{reading.continue_text or '未读到'}；撤离：{reading.retreat_text or '未读到'}"))
            if reading.current or reading.next_level:
                self._emit("log", ("info", f"快速识别：当前 Lv.{reading.current or '?'}，下一 Lv.{reading.next_level or '?'}；{reading.note}"))
            else:
                self._emit("log", ("info", f"快速识别：{reading.note}"))
            if not reading.found:
                self._emit("log", ("warn", "当前画面未检测到行动抉择弹窗"))
            lines = vision.full_texts(frame) if reading.found else []
            if lines:
                summary = " / ".join(text for text, _score, _box in lines[:10])
                self._emit("log", ("info", f"整块识别：{summary}"))
            self._emit("log", ("info", f"诊断图已保存：screenshots/{path.name}"))
        except Exception as exc:
            self._emit("log", ("error", f"识别测试异常：{exc}"))
        finally:
            self._emit("test_done", None)

    # ----------------------------------------------------------- character
    def _apply_input_mode(self) -> None:
        mode = INPUT_LABELS.get(self.input_box.get(), "foreground")
        if mode == "foreground":
            self.char_hint.config(text="开始后请把游戏窗口切到前台；灾厄弹窗出现时自动暂停")
        else:
            self.char_hint.config(text="后台发送按键；灾厄弹窗出现时自动暂停，蓄力识别需要窗口可见")

    def _apply_character(self) -> None:
        name = self.char_box.get()
        if name not in self.char_box.cget("values"):
            name = DEFAULT_CHARACTER
            self.char_box.set(name)
        self.char_name_value.config(text=name or "--")
        self.char_desc.config(text=rotation_summary(name))
        if name == CUSTOM_CHARACTER:
            self.custom_row.grid()
        else:
            self.custom_row.grid_remove()

    def _collect_keys(self) -> dict:
        return {key: (entry.get().strip() or DEFAULT_GAME_KEYS[key])
                for key, entry in self.key_entries.items()}

    def _start_character(self) -> None:
        if self.selected_hwnd == 0:
            self._append_log("warn", "[主控] 请先选择游戏窗口")
            return
        info = windowing.find_window(self.selected_hwnd)
        if info is None:
            self._append_log("warn", "[主控] 所选窗口已失效，请刷新后重新选择")
            self._refresh_windows(auto=False)
            return

        self._cancel_character(wait=True)
        if not self.calamity_running:
            self.dialog_pause.clear()

        character = self.char_box.get()
        keys = self._collect_keys()
        input_mode = INPUT_LABELS.get(self.input_box.get(), "foreground")
        require_foreground = input_mode == "foreground"
        custom = self.custom_entry.get().strip()
        lock_first = bool(self.lock_first.get())
        self.char_run_id += 1
        run_id = self.char_run_id
        self.char_runner = CharacterRunner(
            self.host, self.selected_hwnd, character, keys, input_mode,
            require_foreground, custom, lock_first, run_id, self._emit,
            pause_check=lambda: self.dialog_pause.is_set())
        self.char_running = True
        self.char_started_at = time.time()
        self.char_state = ("starting", "正在准备角色循环…")
        self.char_name_value.config(text=character)
        self.char_action_value.config(text="--")
        self.char_time_value.config(text="00:00:00")
        self.cfg.update({
            "character": character,
            "game_keys": keys,
            "character_input": input_mode,
            "character_lock_first": lock_first,
            "character_custom": custom,
            "window_hwnd": self.selected_hwnd,
            "active_tab": "character",
        })
        settings_store.save(self.cfg)
        self._set_running_ui("character", True)
        self._switch_tab("character", save=False)
        self._append_log("good", f"[主控] 开始：{character}，{self.input_box.get()}")
        if require_foreground:
            self._append_log("info", "[主控] 请把游戏窗口切到前台；切走会自动暂停")
        self.char_thread = threading.Thread(target=self.char_runner.run, daemon=True)
        self.char_thread.start()
        self._refresh_pill()

    def _stop_character(self, wait: bool = False) -> None:
        if self.char_runner is not None:
            self._append_log("info", "[主控] 正在停止…")
            self.char_runner.stop()
            if wait and self.char_thread is not None:
                self.char_thread.join(timeout=2.0)
                self._cancel_character(wait=False)
            else:
                self.char_stop_btn.config(state="disabled")

    def _cancel_character(self, wait: bool = True) -> None:
        if self.char_runner is not None:
            try:
                self.char_runner.stop()
            except Exception:
                pass
            if wait and self.char_thread is not None:
                self.char_thread.join(timeout=2.0)
        self.char_runner = None
        self.char_thread = None
        self.char_running = False
        self._set_running_ui("character", False)
        self._refresh_pill()

    def _finish_character(self) -> None:
        self.char_runner = None
        self.char_thread = None
        self.char_running = False
        self._set_running_ui("character", False)
        self._append_log("info", "[主控] 已停止")
        self._refresh_pill()

    def _tick_char_time(self) -> None:
        if self.char_running and self.char_started_at:
            elapsed = max(0, int(time.time() - self.char_started_at))
            self.char_time_value.config(text=time.strftime("%H:%M:%S", time.gmtime(elapsed)))
        self.root.after(1000, self._tick_char_time)

    # -------------------------------------------------------------- status
    def _set_pill(self, kind: str, text: str) -> None:
        colors = {
            "idle": (BORDER, MUTED),
            "running": ("#132a4a", "#8fc2ff"),
            "loading": ("#1c1b42", "#c3b4ff"),
            "dialog": ("#2a1f45", "#d9c9ff"),
            "reached": ("#2a1f45", "#d9c9ff"),
            "paused": ("#2a1f45", "#d9c9ff"),
            "error": ("#3a1a2c", "#ff9db2"),
        }
        bg, fg = colors.get(kind, (BORDER, MUTED))
        self.pill.config(text=text, bg=bg, fg=fg)

    def _apply_state(self, module: str, kind: str, message: str) -> None:
        if module == "calamity":
            mapping = {
                "loading": ("loading", "加载中"),
                "waiting": ("running", "监视中"),
                "dialog": ("dialog", "弹窗"),
                "clicked": ("running", "继续"),
                "reached": ("reached", "已达标"),
                "ignored": ("idle", "已忽略"),
                "error": ("error", "异常"),
                "stopped": ("idle", "已停止"),
            }
        else:
            mapping = {
                "starting": ("loading", "准备中"),
                "paused": ("paused", "已暂停"),
                "running": ("running", "运行中"),
                "error": ("error", "异常"),
                "stopped": ("idle", "已停止"),
            }
        pill_kind, pill_text = mapping.get(kind, ("idle", "运行"))
        self._set_pill(pill_kind, pill_text)
        self.status_line.config(text=message)

    def _set_calamity_state(self, kind: str, message: str) -> None:
        self.calamity_state = (kind, message)
        if self.calamity_running:
            if kind in ("dialog", "clicked", "reached", "ignored"):
                self.dialog_pause.set()
            elif kind in ("waiting", "loading", "error"):
                self.dialog_pause.clear()
        self._refresh_pill()

    def _set_char_state(self, kind: str, message: str) -> None:
        self.char_state = (kind, message)
        self._refresh_pill()

    def _refresh_pill(self) -> None:
        if self.calamity_running and self.char_running:
            if self.char_state[0] == "paused":
                self._set_pill("paused", "双运行")
                self.status_line.config(text=self.char_state[1])
            else:
                self._set_pill("running", "双运行")
                self.status_line.config(text="灾厄监视中 · 主控运行中")
        elif self.calamity_running:
            self._apply_state("calamity", *self.calamity_state)
        elif self.char_running:
            self._apply_state("character", *self.char_state)
        else:
            self._set_pill("idle", "未运行")
            if self.active_tab == "character":
                self.status_line.config(text="选择角色和按键后开始；开始后请切到游戏窗口")
            elif self.active_tab == "keys":
                self.status_line.config(text="全局按键：与游戏内按键设置保持一致")
            else:
                self.status_line.config(text="等待开始")

    def _set_running_ui(self, module: str, running: bool) -> None:
        busy = self.calamity_running or self.char_running
        self.window_box.config(state="disabled" if busy else "readonly")
        self.refresh_btn.config(state="disabled" if busy else "normal")
        self.show_all_check.config(state="disabled" if busy else "normal")
        self.reset_keys_btn.config(state="disabled" if busy else "normal")
        for entry in self.key_entries.values():
            entry.config(state="disabled" if busy else "normal")
        if module == "calamity":
            self.start_btn.config(state="disabled" if running else "normal")
            self.stop_btn.config(state="normal" if running else "disabled")
            self.test_btn.config(state="disabled" if busy else "normal")
        elif module == "character":
            char_state = "disabled" if running else "readonly"
            entry_state = "disabled" if running else "normal"
            self.char_start_btn.config(state="disabled" if running else "normal")
            self.char_stop_btn.config(state="normal" if running else "disabled")
            self.char_box.config(state=char_state)
            self.input_box.config(state=char_state)
            self.custom_entry.config(state=entry_state)
            self.lock_check.config(state=entry_state)

    # --------------------------------------------------------------- log
    def _append_log(self, level: str, message: str) -> None:
        level = level if level in ("info", "good", "warn", "error") else "info"
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{stamp} ", "time")
        self.log_text.insert("end", f"{message}\n", level)
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 600:
            self.log_text.delete("1.0", "120.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            with self.log_file.open("a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{level}] {message}\n")
        except Exception:
            pass

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        try:
            self._cancel_character(wait=True)
        except Exception:
            pass
        try:
            if self.monitor is not None:
                self.monitor.stop()
        except Exception:
            pass
        self.cfg.update({
            "target_level": self._target_value(),
            "click_mode": MODE_LABELS.get(self.mode_box.get(), "auto"),
            "window_hwnd": self.selected_hwnd,
            "active_tab": self.active_tab,
            "character": self.char_box.get(),
            "game_keys": self._collect_keys(),
            "character_input": INPUT_LABELS.get(self.input_box.get(), "foreground"),
            "character_lock_first": bool(self.lock_first.get()),
            "character_custom": self.custom_entry.get().strip(),
        })
        settings_store.save(self.cfg)
        self.root.destroy()


def run() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()
