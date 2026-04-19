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
        # Re-lift clipboard widget above the overlay after it settles
        if self._clipboard_widget:
            self.root.after(50, self._clipboard_widget.lift)

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
        if not path or not os.path.exists(path):
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

    # ── Clipboard widget ──────────────────────────────────────────────

    def _setup_clipboard_widget(self):
        self._clipboard_widget = ClipboardWidget(
            root=self.root,
            config=self.config,
            on_position_changed=self._on_clipboard_moved,
        )

    def _on_clipboard_moved(self, x: int, y: int):
        cfg = dict(self.config.get("clipboard_widget", {}))
        cfg["x"] = x
        cfg["y"] = y
        self.config.set("clipboard_widget", cfg)

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

    def _bind_events(self):
        self.canvas.bind("<Button-3>",        self._on_canvas_right_click)
        self.canvas.bind("<ButtonPress-1>",   self._on_canvas_press)
        self.canvas.bind("<B1-Motion>",       self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.root.bind("<Escape>", lambda e: self.hide())
        # Re-lift clipboard widget above overlay whenever overlay gains focus
        self.root.bind("<FocusIn>", self._on_overlay_focus)

    def _on_overlay_focus(self, event):
        if self._clipboard_widget:
            self.root.after(20, self._clipboard_widget.lift)

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

        cover   = self.config.get("cover_taskbar", False)
        auto    = self._is_autostart_enabled()
        clip_on = self.config.get("clipboard_widget", {}).get("visible", False)

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
        show_desktop_icons()
        self.root.quit()


_ACCENT_COLOR = "#4fc3f7"
