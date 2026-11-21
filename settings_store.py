from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "profile.json"

DEFAULTS = {
    "target_level": 110,
    "click_mode": "auto",
    "window_hwnd": 0,
    "active_tab": "calamity",
    "character": "黎瑟SP",
    "character_input": "foreground",
    "character_lock_first": True,
    "character_custom": "终结技:1",
    "game_keys": {
        "interact": "f",
        "combat": "e",
        "ultimate": "q",
        "geniemon": "z",
        "dodge": "lshift",
        "helix": "4",
        "normal": "鼠标左键",
        "shoot": "鼠标右键",
        "lock": "鼠标中键",
    },
}


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        if PROFILE.exists():
            loaded = json.loads(PROFILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key in DEFAULTS:
                    if key in loaded:
                        data[key] = loaded[key]
    except Exception:
        pass
    try:
        data["target_level"] = max(1, int(data["target_level"]))
    except Exception:
        data["target_level"] = DEFAULTS["target_level"]
    if data.get("click_mode") not in ("auto", "background", "foreground"):
        data["click_mode"] = "auto"
    if data.get("active_tab") not in ("calamity", "character"):
        data["active_tab"] = "calamity"
    if data.get("character_input") not in ("foreground", "background"):
        data["character_input"] = "foreground"
    keys = dict(DEFAULTS["game_keys"])
    loaded_keys = data.get("game_keys")
    if isinstance(loaded_keys, dict):
        for key in keys:
            if key in loaded_keys and str(loaded_keys[key]).strip():
                keys[key] = str(loaded_keys[key]).strip()
    old_keys = data.get("character_keys")
    if isinstance(old_keys, dict):
        for key in keys:
            if key in old_keys and str(old_keys[key]).strip():
                keys[key] = str(old_keys[key]).strip()
    data["game_keys"] = keys
    data.pop("character_keys", None)
    return data


def save(data: dict) -> None:
    try:
        PROFILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
