import json
import os
import tempfile
import threading
import uuid

DEFAULT_CONFIG = {
    "wallpaper": "assets/default_wallpaper.jpg",   # shown on first launch
    "wallpaper_fit": "fill",
    "shortcuts": [],
    "sections": [],
    "hotkey": "alt+`",
    "overlay_visible_on_start": False,
    "tray_icon_path": "assets/tray_icon.png",
    "cover_taskbar": False,
    "clipboard_widget": {"visible": False, "x": 20, "y": 100, "w": 270, "h": 380},
    "clipboard_notes": [],
}


class Config:
    def __init__(self, path="config.json"):
        self.path = os.path.abspath(path)
        self._data = {}
        self._lock = threading.Lock()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                self._data = {**DEFAULT_CONFIG, **stored}
            except (json.JSONDecodeError, OSError):
                self._data = dict(DEFAULT_CONFIG)
        else:
            self._data = dict(DEFAULT_CONFIG)
            self.save()
        return self

    def save(self):
        with self._lock:
            dir_ = os.path.dirname(self.path)
            os.makedirs(dir_, exist_ok=True)
            try:
                with tempfile.NamedTemporaryFile(
                    "w", dir=dir_, delete=False, suffix=".tmp", encoding="utf-8"
                ) as f:
                    json.dump(self._data, f, indent=2, ensure_ascii=False)
                    tmp = f.name
                os.replace(tmp, self.path)
            except OSError:
                pass

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    # --- Shortcut helpers ---

    def add_shortcut(self, target: str, x: int = 80, y: int = 80) -> dict:
        sc_id = "sc_" + uuid.uuid4().hex[:8]
        label = os.path.splitext(os.path.basename(target))[0]
        cache_path = os.path.join("cache", f"{sc_id}.png")
        entry = {
            "id": sc_id,
            "label": label,
            "target": target,
            "x": x,
            "y": y,
            "icon_cache": cache_path,
        }
        self._data["shortcuts"].append(entry)
        self.save()
        return entry

    def remove_shortcut(self, sc_id: str):
        self._data["shortcuts"] = [
            s for s in self._data["shortcuts"] if s["id"] != sc_id
        ]
        self.save()

    def update_shortcut_position(self, sc_id: str, x: int, y: int):
        for s in self._data["shortcuts"]:
            if s["id"] == sc_id:
                s["x"] = x
                s["y"] = y
                break
        self.save()

    def get_shortcut_by_id(self, sc_id: str):
        for s in self._data["shortcuts"]:
            if s["id"] == sc_id:
                return s
        return None

    # --- Section helpers ---

    def add_section(self, x: int, y: int, w: int = 240, h: int = 160) -> dict:
        sec_id = "sec_" + uuid.uuid4().hex[:8]
        entry = {"id": sec_id, "label": "区域", "x": x, "y": y, "w": w, "h": h,
                 "color": "#2a2a4a"}
        self._data["sections"].append(entry)
        self.save()
        return entry

    def remove_section(self, sec_id: str):
        self._data["sections"] = [
            s for s in self._data["sections"] if s["id"] != sec_id
        ]
        self.save()

    def update_section(self, sec_id: str, **kwargs):
        for s in self._data["sections"]:
            if s["id"] == sec_id:
                s.update(kwargs)
                break
        self.save()
