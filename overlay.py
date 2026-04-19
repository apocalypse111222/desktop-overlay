import ctypes
import ctypes.wintypes as wt
import os
import sys
import winreg
import tkinter as tk
from tkinter import filedialog

from PIL import Image, ImageTk

from clipboard_widget import ClipboardWidget
from config import Config
from desktop_icons import hide_desktop_icons, show_desktop_icons
from icon_extractor import cache_icon
from section import Section
from shortcut import Shortcut, ICON_SIZE, LABEL_WIDTH, GRID_SNAP
from web_widget import WebWidget


def _draw_glass_icon(canvas: tk.Canvas, cx: int, cy: int, R: int,
                     fill: str, border: str, shine: str,
                     emoji: str, tag: str) -> list[int]:
    """
    iOS 26-style liquid glass circular button — dark frosted glass + white icon.

    Layers (bottom → top):
      1. Drop shadow        — soft black aura, slightly larger
      2. Outer glow ring    — semi-dark ring for depth
      3. Glass body         — dark navy circle (iOS 26 dark card colour)
      4. Specular highlight — small brighter crescent at top (glass lens flare)
      5. White icon         — canvas-drawn white symbol (NOT coloured emoji)
    """
    items: list[int] = []

    # Determine icon type from tag (drives which white icon to draw)
    is_clipboard = "clipboard" in tag

    # 1. Drop shadow — slightly larger, pitch black, no outline
    items.append(canvas.create_oval(
        cx - R - 5, cy - R - 5, cx + R + 5, cy + R + 5,
        fill="#000000", outline="",
        tags=(tag,),
    ))

    # 2. Outer glow ring — 2 px larger than body, very dark border
    items.append(canvas.create_oval(
        cx - R - 2, cy - R - 2, cx + R + 2, cy + R + 2,
        fill="#0a0f1e", outline=border, width=1,
        tags=(tag,),
    ))

    # 3. Glass body — iOS 26 dark navy fill
    items.append(canvas.create_oval(
        cx - R, cy - R, cx + R, cy + R,
        fill=fill, outline=border, width=1,
        tags=(tag,),
    ))

    # 4. Specular highlight — narrow brighter oval at top-centre (lens flare)
    hw = int(R * 0.45)
    items.append(canvas.create_oval(
        cx - hw, cy - R + 5,
        cx + hw, cy - R + 5 + int(R * 0.28),
        fill=shine, outline="",
        tags=(tag,),
    ))

    # 5. White icon drawn with canvas primitives
    #    (Coloured emoji cannot be recoloured — canvas primitives stay white)
    s = int(R * 0.42)   # icon half-size, scales with button radius
    iy = cy + 1          # vertical centre with 1 px nudge

    if is_clipboard:
        # Notes / clipboard: three horizontal lines (hamburger list)
        for dy in (-s // 2, 0, s // 2):
            items.append(canvas.create_line(
                cx - s, iy + dy, cx + s, iy + dy,
                fill="white", width=2, capstyle="round",
                tags=(tag,),
            ))
        # Small vertical nub at top-left (clipboard clip)
        items.append(canvas.create_rectangle(
            cx - s // 3, iy - s - 3,
            cx + s // 3, iy - s + 3,
            fill="white", outline="",
            tags=(tag,),
        ))
    else:
        # AirPortal / upload: upward arrow
        ax, ay = cx, iy
        # Shaft
        items.append(canvas.create_line(
            ax, ay + s, ax, ay - s + 3,
            fill="white", width=2, capstyle="round",
            tags=(tag,),
        ))
        # Arrow head (two diagonal lines)
        items.append(canvas.create_line(
            ax - s + 3, ay - s // 2 + 5,
            ax, ay - s + 3,
            ax + s - 3, ay - s // 2 + 5,
            fill="white", width=2,
            joinstyle="miter", capstyle="round",
            tags=(tag,),
        ))
        # Small base line
        items.append(canvas.create_line(
            ax - s, ay + s, ax + s, ay + s,
            fill="white", width=2, capstyle="round",
            tags=(tag,),
        ))

    return items


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


_EnumWindowsProc  = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
_GWL_STYLE        = -16
_GWL_EXSTYLE      = -20
_WS_CAPTION       = 0x00C00000
_WS_EX_TOOLWINDOW = 0x00000080
_SW_MINIMIZE      = 6
_SW_RESTORE       = 9

_AUTOSTART_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_NAME = "Desktop Overlay"


class OverlayWindow:
    def __init__(self, root: tk.Tk, config: Config):
        self.root = root
        self.config = config
        self.visible = False
        self._shortcuts: dict[str, Shortcut] = {}
        self._sections:  dict[str, Section]  = {}
        self._selected:  set[str]            = set()
        self._minimized_by_us: list[int]     = []
        self._clipboard_widget: ClipboardWidget | None = None
        self._web_widget: WebWidget | None = None

        # Rubber-band state
        self._rb_start = None
        self._rb_item  = None

        self._setup_window()
        self._setup_canvas()
        self._load_wallpaper()
        self._load_sections()
        self._load_shortcuts()
        self._arrange_z_order()
        self._setup_clipboard_widget()
        self._setup_web_widget()
        self._bind_events()

        if config.get("overlay_visible_on_start"):
            self.show()
        else:
            self.hide()

    # ── Display area ──────────────────────────────────────────────────

    def _get_display_area(self):
        if self.config.get("cover_taskbar", False):
            return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        rect = _RECT()
        ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

    # ── Window setup ──────────────────────────────────────────────────

    def _setup_window(self):
        self.root.title("Desktop Overlay")
        self.root.configure(bg="black")
        self.root.overrideredirect(True)
        self.root.wm_attributes("-toolwindow", True)
        x, y, w, h = self._get_display_area()
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _setup_canvas(self):
        _, _, w, h = self._get_display_area()
        self.canvas = tk.Canvas(
            self.root, width=w, height=h,
            bg="#1a1a2e", highlightthickness=0, bd=0, relief="flat",
        )
        self.canvas.pack(fill="both", expand=True)

    # ── Visibility ────────────────────────────────────────────────────

    def show(self):
        self._restore_other_windows()
        self.root.deiconify()
        self.root.lift()
        self.visible = True
        hide_desktop_icons()
        # Re-lift floating widgets above the overlay after it settles
        if self._clipboard_widget:
            self.root.after(50, self._clipboard_widget.lift)
        if self._web_widget:
            self.root.after(50, lambda: self._web_widget.lift(self._overlay_hwnd()))

    def lower_for_launch(self):
        self._minimize_other_windows()

    def hide(self):
        self._restore_other_windows()
        self.root.withdraw()
        self.visible = False
        show_desktop_icons()

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    # ── Background window helpers ──────────────────────────────────────

    def _minimize_other_windows(self):
        self._minimized_by_us.clear()
        overlay_hwnd = self.root.winfo_id()
        u32 = ctypes.windll.user32

        def _cb(hwnd, _):
            if hwnd == overlay_hwnd or not u32.IsWindowVisible(hwnd) or u32.IsIconic(hwnd):
                return True
            style    = u32.GetWindowLongW(hwnd, _GWL_STYLE)
            ex_style = u32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            if ex_style & _WS_EX_TOOLWINDOW or not (style & _WS_CAPTION):
                return True
            u32.ShowWindow(hwnd, _SW_MINIMIZE)
            self._minimized_by_us.append(hwnd)
            return True

        ctypes.windll.user32.EnumWindows(_EnumWindowsProc(_cb), 0)

    def _restore_other_windows(self):
        u32 = ctypes.windll.user32
        for hwnd in self._minimized_by_us:
            if u32.IsWindow(hwnd):
                u32.ShowWindow(hwnd, _SW_RESTORE)
        self._minimized_by_us.clear()

    # ── Wallpaper ─────────────────────────────────────────────────────

    def _load_wallpaper(self):
        path = self.config.get("wallpaper")
        if not path:
            return
        # PyInstaller 6+ places bundled data in _internal/ (sys._MEIPASS).
        # Relative paths (e.g. "assets/default_wallpaper.jpg") must be resolved
        # there when running frozen; absolute user-chosen paths are used as-is.
        if not os.path.isabs(path) and getattr(sys, "frozen", False):
            candidate = os.path.join(getattr(sys, "_MEIPASS", ""), path)
            if os.path.exists(candidate):
                path = candidate
        if not os.path.exists(path):
            return
        fit = self.config.get("wallpaper_fit", "fill")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        try:
            img = Image.open(path)
            img = self._fit_image(img, sw, sh, fit)
            self._wallpaper_photo = ImageTk.PhotoImage(img)
            self.canvas.create_image(0, 0, anchor="nw",
                                     image=self._wallpaper_photo, tags=("wallpaper",))
            self.canvas.tag_lower("wallpaper")
        except Exception:
            pass

    def _fit_image(self, img: Image.Image, w: int, h: int, mode: str) -> Image.Image:
        if mode == "fill":
            ratio = max(w / img.width, h / img.height)
            nw, nh = int(img.width * ratio), int(img.height * ratio)
            img = img.resize((nw, nh), Image.LANCZOS)
            img = img.crop(((nw - w) // 2, (nh - h) // 2,
                            (nw - w) // 2 + w, (nh - h) // 2 + h))
        elif mode == "fit":
            img.thumbnail((w, h), Image.LANCZOS)
            canvas_img = Image.new("RGBA", (w, h), (0, 0, 0, 255))
            canvas_img.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
            img = canvas_img
        elif mode == "tile":
            canvas_img = Image.new("RGBA", (w, h))
            for ty in range(0, h, img.height):
                for tx in range(0, w, img.width):
                    canvas_img.paste(img, (tx, ty))
            img = canvas_img
        else:  # center
            canvas_img = Image.new("RGBA", (w, h), (0, 0, 0, 255))
            canvas_img.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
            img = canvas_img
        return img.convert("RGBA")

    def change_wallpaper(self):
        path = filedialog.askopenfilename(
            title="选择壁纸",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.webp *.gif"),
                       ("所有文件", "*.*")],
        )
        if not path:
            return
        self.config.set("wallpaper", path)
        self.canvas.delete("wallpaper")
        self._wallpaper_photo = None
        self._load_wallpaper()

    # ── Sections ──────────────────────────────────────────────────────

    def _load_sections(self):
        for sec_data in self.config.get("sections", []):
            self._add_section_widget(sec_data)

    def _add_section_widget(self, sec_data: dict):
        sec = Section(
            canvas=self.canvas,
            sec_data=sec_data,
            config=self.config,
            on_update=self.config.update_section,
            on_remove=self._on_section_removed,
        )
        self._sections[sec_data["id"]] = sec

    def _on_section_removed(self, sec_id: str):
        self._sections.pop(sec_id, None)

    def add_section(self, x: int, y: int):
        sec_data = self.config.add_section(x, y)
        self._add_section_widget(sec_data)

    # ── Shortcuts ─────────────────────────────────────────────────────

    def _load_shortcuts(self):
        for sc_data in self.config.get("shortcuts", []):
            cache_path = sc_data.get("icon_cache")
            target     = sc_data.get("target", "")
            if cache_path and target:
                needs_regen = (
                    not os.path.exists(cache_path)
                    or os.path.getsize(cache_path) < 1024
                )
                if needs_regen:
                    cache_icon(target, cache_path)
            self._add_shortcut_widget(sc_data)

    def _add_shortcut_widget(self, sc_data: dict):
        sc = Shortcut(
            canvas=self.canvas,
            sc_data=sc_data,
            config=self.config,
            on_position_changed=self.config.update_shortcut_position,
            on_remove=self._on_shortcut_removed,
            on_launch=self.lower_for_launch,
            on_group_drag=self._on_group_drag,
            on_group_drag_end=self._on_group_drag_end,
        )
        self._shortcuts[sc_data["id"]] = sc

    def _on_shortcut_removed(self, sc_id: str):
        self._selected.discard(sc_id)
        self._shortcuts.pop(sc_id, None)

    def add_file_shortcut_dialog(self):
        path = filedialog.askopenfilename(
            title="选择文件或程序",
            filetypes=[("所有文件", "*.*"),
                       ("程序", "*.exe *.lnk *.bat *.cmd")],
        )
        if path:
            self._create_shortcut(path)

    def add_folder_shortcut_dialog(self):
        path = filedialog.askdirectory(title="选择文件夹")
        if path:
            self._create_shortcut(path)

    def _create_shortcut(self, path: str):
        sc_data = self.config.add_shortcut(path, x=80, y=80)
        cache_path = sc_data.get("icon_cache")
        if cache_path:
            cache_icon(path, cache_path)
        self._add_shortcut_widget(sc_data)

    # ── Multi-select ──────────────────────────────────────────────────

    def _deselect_all(self):
        for sc_id in list(self._selected):
            sc = self._shortcuts.get(sc_id)
            if sc:
                sc.set_selected(False)
        self._selected.clear()

    def _select_shortcuts_in_rect(self, x1, y1, x2, y2):
        for sc_id, sc in self._shortcuts.items():
            cx = sc.x + LABEL_WIDTH // 2
            cy = sc.y + ICON_SIZE // 2
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                self._selected.add(sc_id)
                sc.set_selected(True)

    def _on_group_drag(self, sc_id: str, dx: int, dy: int):
        for sid in self._selected:
            sc = self._shortcuts.get(sid)
            if sc:
                sc.move_by(dx, dy)

    def _on_group_drag_end(self, sc_id: str):
        for sid in self._selected:
            sc = self._shortcuts.get(sid)
            if sc:
                sc.x = round(sc.x / GRID_SNAP) * GRID_SNAP
                sc.y = round(sc.y / GRID_SNAP) * GRID_SNAP
                self.config.update_shortcut_position(sid, sc.x, sc.y)

    # ── Z-order ───────────────────────────────────────────────────────

    def _arrange_z_order(self):
        """wallpaper → sections → shortcuts (bottom to top)."""
        try:
            self.canvas.tag_lower("wallpaper")
        except Exception:
            pass
        try:
            self.canvas.tag_raise("shortcut")
        except Exception:
            pass

    # ── Clipboard widget + corner icon ───────────────────────────────

    def _setup_clipboard_widget(self):
        self._clipboard_widget = ClipboardWidget(
            root=self.root,
            config=self.config,
            on_position_changed=self._on_clipboard_moved,
        )
        self._clipboard_icon_items: list[int] = []
        self._draw_clipboard_icon()

    def _draw_clipboard_icon(self):
        """Draw iOS 26-style liquid glass clipboard icon in the bottom-right corner."""
        for item in self._clipboard_icon_items:
            self.canvas.delete(item)
        self._clipboard_icon_items.clear()

        _, _, w, h = self._get_display_area()
        R  = 28
        # Place to the left of the AirPortal icon
        cx = w - R - 16 - (R * 2) - 8
        cy = h - R - 16

        items = _draw_glass_icon(self.canvas, cx, cy, R,
                                 fill="#0e1530", border="#4060a0",
                                 shine="#1e3060", emoji="",
                                 tag="clipboard_icon")
        self._clipboard_icon_items = items

        for item in self._clipboard_icon_items:
            self.canvas.tag_bind(item, "<Button-1>",
                                 lambda e: self._clipboard_widget.toggle()
                                 if self._clipboard_widget else None)
            self.canvas.tag_bind(item, "<Enter>",
                                 lambda e: self.canvas.config(cursor="hand2"))
            self.canvas.tag_bind(item, "<Leave>",
                                 lambda e: self.canvas.config(cursor=""))
            self.canvas.tag_raise(item)

    def _on_clipboard_moved(self, x: int, y: int):
        cfg = dict(self.config.get("clipboard_widget", {}))
        cfg["x"] = x
        cfg["y"] = y
        self.config.set("clipboard_widget", cfg)

    # ── AirPortal web widget + corner icon ───────────────────────────

    def _setup_web_widget(self):
        self._web_widget = WebWidget(root=self.root, config=self.config)
        self._airportal_icon_items: list[int] = []
        self._draw_airportal_icon()

    def _draw_airportal_icon(self):
        """Draw iOS 26-style liquid glass AirPortal icon in the bottom-right corner."""
        for item in self._airportal_icon_items:
            self.canvas.delete(item)
        self._airportal_icon_items.clear()

        _, _, w, h = self._get_display_area()
        R  = 28
        cx = w - R - 16
        cy = h - R - 16

        items = _draw_glass_icon(self.canvas, cx, cy, R,
                                 fill="#0c1830", border="#3a6090",
                                 shine="#183058", emoji="",
                                 tag="airportal_icon")
        self._airportal_icon_items = items

        for item in self._airportal_icon_items:
            self.canvas.tag_bind(item, "<Button-1>",
                                 lambda e: self._web_widget.toggle()
                                 if self._web_widget else None)
            self.canvas.tag_bind(item, "<Enter>",
                                 lambda e: self.canvas.config(cursor="hand2"))
            self.canvas.tag_bind(item, "<Leave>",
                                 lambda e: self.canvas.config(cursor=""))

        # Keep icon on top of wallpaper/sections
        for item in self._airportal_icon_items:
            self.canvas.tag_raise(item)

    # ── Autostart ─────────────────────────────────────────────────────

    def _is_autostart_enabled(self) -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY) as k:
                winreg.QueryValueEx(k, _AUTOSTART_NAME)
            return True
        except OSError:
            return False

    def _set_autostart(self, enable: bool):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY,
                                0, winreg.KEY_SET_VALUE) as k:
                if enable:
                    if getattr(sys, "frozen", False):
                        cmd = f'"{sys.executable}"'
                    else:
                        pythonw = os.path.join(
                            os.path.dirname(sys.executable), "pythonw.exe")
                        if not os.path.exists(pythonw):
                            pythonw = sys.executable
                        cmd = f'"{pythonw}" "{os.path.abspath("main.py")}"'
                    winreg.SetValueEx(k, _AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(k, _AUTOSTART_NAME)
                    except OSError:
                        pass
        except OSError:
            pass

    def _toggle_autostart(self):
        self._set_autostart(not self._is_autostart_enabled())

    # ── Geometry reload ───────────────────────────────────────────────

    def _reload_geometry(self):
        x, y, w, h = self._get_display_area()
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.canvas.config(width=w, height=h)
        self.canvas.delete("wallpaper")
        self._wallpaper_photo = None
        self._load_wallpaper()

    def _toggle_cover_taskbar(self):
        self.config.set("cover_taskbar", not self.config.get("cover_taskbar", False))
        self._reload_geometry()

    # ── Events ────────────────────────────────────────────────────────

    def _overlay_hwnd(self) -> int:
        """Return the true Win32 top-level HWND of the overlay root window."""
        raw = self.root.winfo_id()
        # GA_ROOT = 2: walk up to the outermost ancestor
        top = ctypes.windll.user32.GetAncestor(raw, 2)
        return top if top else raw

    def _bind_events(self):
        self.canvas.bind("<Button-3>",        self._on_canvas_right_click)
        self.canvas.bind("<ButtonPress-1>",   self._on_canvas_press)
        self.canvas.bind("<B1-Motion>",       self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.root.bind("<Escape>", lambda e: self.hide())
        # Re-lift clipboard widget above overlay whenever overlay gains focus
        self.root.bind("<FocusIn>", self._on_overlay_focus)
        # Continuous z-order poll — keeps AirPortal (subprocess window) above overlay
        self._z_order_poll()

    def _on_overlay_focus(self, event):
        if self._clipboard_widget:
            self.root.after(0,  self._clipboard_widget.lift)
            self.root.after(80, self._clipboard_widget.lift)
        if self._web_widget:
            hwnd = self._overlay_hwnd()
            self.root.after(0,  lambda: self._web_widget.lift(hwnd))
            self.root.after(80, lambda: self._web_widget.lift(hwnd))

    def _z_order_poll(self):
        """Every 150 ms enforce: clipboard → AirPortal → overlay (front→back)."""
        if self.visible:
            try:
                overlay_hwnd  = self._overlay_hwnd()
                air_hwnd      = (self._web_widget._hwnd
                                 if self._web_widget and self._web_widget.visible
                                 else None)
                clip_win      = (self._clipboard_widget._win
                                 if self._clipboard_widget else None)
                clip_visible  = (self.config.get("clipboard_widget", {})
                                 .get("visible", False))
                clip_hwnd     = None
                if clip_win and clip_visible:
                    raw = clip_win.winfo_id()
                    clip_hwnd = ctypes.windll.user32.GetAncestor(raw, 2) or raw

                SWP = 0x0001 | 0x0002 | 0x0010   # NOSIZE|NOMOVE|NOACTIVATE

                if air_hwnd:
                    # Push overlay below AirPortal
                    ctypes.windll.user32.SetWindowPos(
                        overlay_hwnd, air_hwnd, 0, 0, 0, 0, SWP)
                    if clip_hwnd:
                        # Push AirPortal below clipboard
                        ctypes.windll.user32.SetWindowPos(
                            air_hwnd, clip_hwnd, 0, 0, 0, 0, SWP)
                elif clip_hwnd:
                    # Only clipboard visible — push overlay below clipboard
                    ctypes.windll.user32.SetWindowPos(
                        overlay_hwnd, clip_hwnd, 0, 0, 0, 0, SWP)
            except Exception:
                pass
        self.root.after(150, self._z_order_poll)

    def _click_on_managed_item(self, x, y) -> bool:
        """Return True if (x, y) overlaps a shortcut, section, or clipboard widget."""
        items = self.canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1)
        managed = set(self._shortcuts) | set(self._sections) | {"clipboard_widget"}
        for item in items:
            for tag in self.canvas.gettags(item):
                if tag in managed:
                    return True
        return False

    # ── Rubber-band selection ─────────────────────────────────────────

    def _on_canvas_press(self, event):
        if self._click_on_managed_item(event.x, event.y):
            # Clicked on a shortcut/section — don't deselect, let item handle drag
            self._rb_start = None
            return
        # Clicked on empty canvas — deselect all and prepare rubber-band
        self._deselect_all()
        self._rb_start = (event.x, event.y)
        self._rb_item  = None

    def _on_canvas_drag(self, event):
        if not self._rb_start:
            return
        x0, y0 = self._rb_start
        x1, y1 = event.x, event.y
        if self._rb_item:
            self.canvas.coords(self._rb_item, x0, y0, x1, y1)
        elif abs(x1 - x0) > 4 or abs(y1 - y0) > 4:
            self._rb_item = self.canvas.create_rectangle(
                x0, y0, x1, y1,
                outline=_ACCENT_COLOR, width=1, dash=(5, 3), fill="",
                tags=("rubber_band",),
            )

    def _on_canvas_release(self, event):
        if self._rb_item:
            x0, y0 = self._rb_start
            x1, y1 = event.x, event.y
            self._select_shortcuts_in_rect(
                min(x0, x1), min(y0, y1),
                max(x0, x1), max(y0, y1),
            )
            self.canvas.delete(self._rb_item)
            self._rb_item = None
        self._rb_start = None

    # ── Context menu ──────────────────────────────────────────────────

    def _on_canvas_right_click(self, event):
        if self._click_on_managed_item(event.x, event.y):
            return

        cover    = self.config.get("cover_taskbar", False)
        auto     = self._is_autostart_enabled()
        clip_on  = self.config.get("clipboard_widget", {}).get("visible", False)
        air_on   = self._web_widget.visible if self._web_widget else False

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="添加文件 / 程序快捷方式",
                         command=self.add_file_shortcut_dialog)
        menu.add_command(label="添加文件夹快捷方式",
                         command=self.add_folder_shortcut_dialog)
        menu.add_command(label="添加分区框",
                         command=lambda: self.add_section(event.x, event.y))
        menu.add_separator()
        menu.add_command(label="更换壁纸", command=self.change_wallpaper)
        menu.add_separator()
        menu.add_command(
            label="剪贴板 / 备忘录: " + ("显示中" if clip_on else "已隐藏"),
            command=self._clipboard_widget.toggle if self._clipboard_widget else None,
        )
        menu.add_command(
            label="AirPortal 空投快传: " + ("显示中" if air_on else "已隐藏"),
            command=self._web_widget.toggle if self._web_widget else None,
        )
        menu.add_separator()
        menu.add_command(
            label="隐藏任务栏: " + ("开" if cover else "关"),
            command=self._toggle_cover_taskbar,
        )
        menu.add_command(
            label="开机自启: " + ("已开启" if auto else "已关闭"),
            command=self._toggle_autostart,
        )
        menu.add_separator()
        menu.add_command(label="隐藏 Overlay", command=self.hide)
        menu.add_separator()
        menu.add_command(label="退出", command=self._exit)
        menu.tk_popup(event.x_root, event.y_root)

    def _exit(self):
        self._restore_other_windows()
        if self._clipboard_widget:
            self._clipboard_widget.stop()
        if self._web_widget:
            self._web_widget.destroy()
        show_desktop_icons()
        self.root.quit()


_ACCENT_COLOR = "#4fc3f7"
