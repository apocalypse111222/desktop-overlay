import base64
import ctypes
import ctypes.wintypes
import io
import threading
import time
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import filedialog

from PIL import Image, ImageGrab, ImageTk

try:
    import win32clipboard
    import win32con
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False

MAX_HISTORY = 15
MIN_W, MIN_H = 200, 250
THUMB_W, THUMB_H = 64, 48
NOTE_IMG_MAX_W = 260      # max display width for embedded note images

# ── iOS 26 Liquid Glass colour palette ────────────────────────────────────
# Deep blue-navy glass — matches Apple's dark frosted glass cards in iOS 26.
_BG      = "#0d1525"   # deep navy (the glass body colour)
_PANEL   = "#111e38"   # slightly lighter for content panels
_ITEM_BG = "#141f3a"   # list row background
_FG      = "#c8d8f5"   # cool near-white text (slight blue tint)
_DIM     = "#5570a0"   # muted label text
_ACCENT  = "#60aaec"   # iOS blue accent
_SEL_BG  = "#1c2c50"   # row hover / selection
_TBAR    = "#090f1c"   # title bar (darkest layer)


# ── Windows visual effects ────────────────────────────────────────────────

class _ACCENTPOLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

class _WNDCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t)]


def _real_hwnd(tk_win) -> int:
    """Get the true Win32 top-level HWND for a tkinter widget."""
    raw = tk_win.winfo_id()
    top = ctypes.windll.user32.GetAncestor(raw, 2)   # GA_ROOT = 2
    return top if top else raw


def _apply_acrylic(hwnd: int, tint_abgr: int = 0xC025150D):
    """
    Frosted-glass acrylic effect (AccentState 4 = Acrylic blur behind window).
    tint_abgr format: 0xAABBGGRR — AA = opacity of tint colour.
    Default: 0xC025150D = 75 % opaque deep navy (#0D1525 in RGB) — iOS 26 dark
    glass card look.  The remaining 25 % blur from behind shows through, giving
    depth without washing out the content.
    """
    try:
        accent = _ACCENTPOLICY()
        accent.AccentState   = 4
        accent.AccentFlags   = 2
        accent.GradientColor = tint_abgr
        data = _WNDCOMPATTRDATA()
        data.Attribute  = 19
        data.Data       = ctypes.addressof(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
    except Exception:
        pass


def _apply_rounded_corners(hwnd: int, radius: int = 20):
    """
    Win11+: DWM DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND (2).
    Win10 fallback: GDI SetWindowRgn rounded rect.
    """
    dwm_ok = False
    try:
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        val = ctypes.c_int(2)   # DWMWCP_ROUND
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(val), ctypes.sizeof(val))
        dwm_ok = (hr == 0)
    except Exception:
        pass

    if not dwm_ok:
        try:
            rc = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
            w = rc.right - rc.left
            h = rc.bottom - rc.top
            d = radius * 2
            hrgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, d, d)
            ctypes.windll.user32.SetWindowRgn(hwnd, hrgn, True)
        except Exception:
            pass


def _apply_dwm_border(hwnd: int, r: int = 180, g: int = 210, b: int = 255):
    """Win11+: set a thin window border via DWM (DWMWA_BORDER_COLOR = 34)."""
    try:
        DWMWA_BORDER_COLOR = 34
        color = ctypes.c_int(r | (g << 8) | (b << 16))   # COLORREF = 0x00BBGGRR
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_BORDER_COLOR,
            ctypes.byref(color), ctypes.sizeof(color))
    except Exception:
        pass


# ── Dark scrollbar style ──────────────────────────────────────────────────

_style_created = False

def _ensure_scrollbar_style():
    global _style_created
    if _style_created:
        return
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Dark.Vertical.TScrollbar",
                    background=_ITEM_BG, darkcolor=_BG, lightcolor="#1a1a3a",
                    troughcolor=_BG, arrowcolor=_DIM, borderwidth=0, relief="flat")
    style.map("Dark.Vertical.TScrollbar", background=[("active", "#1c1c3c")])
    _style_created = True


# ── Widget ────────────────────────────────────────────────────────────────

class ClipboardWidget:
    """
    Floating Toplevel — liquid glass acrylic, rounded corners, iOS 26 style.
    Tab 1: clipboard history (text + images, auto-captured).
    Tab 2: memo with embedded images (paste / insert / drag-drop file).
    """

    def __init__(self, root: tk.Tk, config, on_position_changed):
        self._root = root
        self._config = config
        self._on_position_changed = on_position_changed

        self._history: list[dict] = []
        self._photos:  list       = []   # history thumbnail refs

        # Notes image tracking
        self._note_photos: dict[str, ImageTk.PhotoImage] = {}
        self._note_pil:    dict[str, Image.Image]        = {}
        self._note_img_counter = 0

        self._running       = False
        self._suppress_next = False

        cfg = config.get("clipboard_widget", {})
        self._x = cfg.get("x", 20)
        self._y = cfg.get("y", 100)
        self._w = cfg.get("w", 280)
        self._h = cfg.get("h", 420)

        self._hwnd: int = 0   # cached real Win32 HWND

        _ensure_scrollbar_style()
        self._win: tk.Toplevel | None = None
        self._build()
        self._start_monitor()

    # ── Build UI ──────────────────────────────────────────────────────────

    def _build(self):
        self._win = tk.Toplevel(self._root)
        self._win.overrideredirect(True)
        self._win.configure(bg=_BG)
        self._win.geometry(f"{self._w}x{self._h}+{self._x}+{self._y}")
        self._win.wm_attributes("-alpha", 0.96)   # let acrylic tint handle depth

        # Apply glass effects — use GetAncestor for the true Win32 top-level HWND
        self._win.update_idletasks()
        self._hwnd = _real_hwnd(self._win)
        _apply_acrylic(self._hwnd)
        _apply_rounded_corners(self._hwnd)
        _apply_dwm_border(self._hwnd, r=100, g=140, b=220)   # iOS blue border

        # ── Title bar ──────────────────────────────────────────────────────
        tbar = tk.Frame(self._win, bg=_TBAR, cursor="fleur")
        tbar.pack(fill="x", side="top")

        # iOS 26 specular top-edge — thin lighter line simulating glass rim
        tk.Frame(self._win, bg="#2a4080", height=1).place(x=0, y=0, relwidth=1)

        title_lbl = tk.Label(tbar, text="剪贴板 / 备忘录",
                             bg=_TBAR, fg="#8099cc",
                             font=("Segoe UI", 9, "bold"), padx=10, pady=5)
        title_lbl.pack(side="left")

        close_btn = tk.Label(tbar, text="✕", bg=_TBAR, fg="#505080",
                             font=("Segoe UI", 10), padx=10, cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>",  lambda e: self.hide())
        close_btn.bind("<Enter>",     lambda e: close_btn.configure(fg="#c0c0ff"))
        close_btn.bind("<Leave>",     lambda e: close_btn.configure(fg="#505080"))

        for w in (tbar, title_lbl):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>",     self._drag_move)

        # ── Bottom resize grip ─────────────────────────────────────────────
        bottom = tk.Frame(self._win, bg=_TBAR, height=14)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)
        grip = tk.Label(bottom, text="◢", bg=_TBAR, fg="#303060",
                        font=("Segoe UI", 9), cursor="size_nw_se", padx=4)
        grip.pack(side="right")
        grip.bind("<ButtonPress-1>", self._resize_start)
        grip.bind("<B1-Motion>",     self._resize_move)

        # ── Tab selector ───────────────────────────────────────────────────
        tab_row = tk.Frame(self._win, bg=_BG)
        tab_row.pack(fill="x", side="top", padx=8, pady=(6, 0))
        self._tab = tk.StringVar(value="notes")
        for label, val in [("剪贴板历史", "history"), ("备忘录", "notes")]:
            rb = tk.Radiobutton(
                tab_row, text=label, variable=self._tab, value=val,
                command=self._switch_tab,
                bg=_BG, fg=_FG, selectcolor="#1a1a40",
                activebackground=_BG, activeforeground=_FG,
                relief="flat", font=("Segoe UI", 8),
            )
            rb.pack(side="left", padx=3)

        # Separator line
        tk.Frame(self._win, bg="#20206a", height=1).pack(fill="x", side="top", pady=(4, 0))

        # ── History pane ───────────────────────────────────────────────────
        self._pane_history = tk.Frame(self._win, bg=_BG)

        list_row = tk.Frame(self._pane_history, bg=_BG)
        list_row.pack(fill="both", expand=True)

        sb_h = ttk.Scrollbar(list_row, orient="vertical",
                             style="Dark.Vertical.TScrollbar")
        sb_h.pack(side="right", fill="y")

        self._list_canvas = tk.Canvas(
            list_row, bg=_ITEM_BG, bd=0, highlightthickness=0,
            yscrollcommand=sb_h.set,
        )
        self._list_canvas.pack(side="left", fill="both", expand=True,
                               padx=(6, 0), pady=4)
        sb_h.configure(command=self._list_canvas.yview)

        self._list_frame = tk.Frame(self._list_canvas, bg=_ITEM_BG)
        self._list_win_id = self._list_canvas.create_window(
            (0, 0), window=self._list_frame, anchor="nw")
        self._list_frame.bind("<Configure>", self._on_list_frame_configure)
        self._list_canvas.bind("<Configure>",  self._on_list_canvas_configure)
        self._list_canvas.bind("<MouseWheel>", self._on_mousewheel)

        tk.Label(self._pane_history, text="双击条目可重新复制",
                 bg=_BG, fg=_DIM, font=("Segoe UI", 7)).pack(pady=(0, 4))

        # ── Notes pane ─────────────────────────────────────────────────────
        self._pane_notes = tk.Frame(self._win, bg=_BG)

        note_toolbar = tk.Frame(self._pane_notes, bg=_BG)
        note_toolbar.pack(fill="x", padx=6, pady=(4, 0))
        tk.Button(
            note_toolbar, text="＋ 插入图片",
            bg="#141440", fg=_ACCENT, relief="flat",
            font=("Segoe UI", 8), cursor="hand2",
            activebackground="#1e1e50", activeforeground=_FG,
            command=self._insert_image_from_file,
        ).pack(side="left")
        tk.Label(note_toolbar, text="Ctrl+V 可粘贴截图",
                 bg=_BG, fg=_DIM, font=("Segoe UI", 7)).pack(side="left", padx=8)

        note_row = tk.Frame(self._pane_notes, bg=_BG)
        note_row.pack(fill="both", expand=True, padx=6, pady=4)

        self._txt = tk.Text(
            note_row,
            bg=_PANEL, fg=_FG, insertbackground=_ACCENT,
            font=("Segoe UI", 9), relief="flat", bd=6,
            wrap="word", undo=True, highlightthickness=0,
        )
        sb_n = ttk.Scrollbar(note_row, orient="vertical",
                             style="Dark.Vertical.TScrollbar",
                             command=self._txt.yview)
        self._txt.configure(yscrollcommand=sb_n.set)
        sb_n.pack(side="right", fill="y")
        self._txt.pack(side="left", fill="both", expand=True)

        self._txt.bind("<KeyRelease>", self._save_notes)
        self._txt.bind("<Control-v>",  self._on_note_paste)

        self._load_notes()
        self._switch_tab()

        if not self._config.get("clipboard_widget", {}).get("visible", False):
            self._win.withdraw()

    # ── Scrollable list helpers ───────────────────────────────────────────

    def _on_list_frame_configure(self, event):
        self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))

    def _on_list_canvas_configure(self, event):
        self._list_canvas.itemconfig(self._list_win_id, width=event.width)

    def _on_mousewheel(self, event):
        self._list_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ── Tabs ──────────────────────────────────────────────────────────────

    def _switch_tab(self):
        self._pane_history.pack_forget()
        self._pane_notes.pack_forget()
        if self._tab.get() == "history":
            self._pane_history.pack(fill="both", expand=True)
        else:
            self._pane_notes.pack(fill="both", expand=True)

    # ── Drag ──────────────────────────────────────────────────────────────

    def _drag_start(self, event):
        self._dsx = event.x_root
        self._dsy = event.y_root

    def _drag_move(self, event):
        dx = event.x_root - self._dsx
        dy = event.y_root - self._dsy
        self._dsx = event.x_root
        self._dsy = event.y_root
        self._x += dx
        self._y += dy
        self._win.geometry(f"+{self._x}+{self._y}")
        self._on_position_changed(self._x, self._y)

    # ── Resize ────────────────────────────────────────────────────────────

    def _resize_start(self, event):
        self._rsx = event.x_root
        self._rsy = event.y_root

    def _resize_move(self, event):
        dw = event.x_root - self._rsx
        dh = event.y_root - self._rsy
        self._rsx = event.x_root
        self._rsy = event.y_root
        self._w = max(MIN_W, self._w + dw)
        self._h = max(MIN_H, self._h + dh)
        self._win.geometry(f"{self._w}x{self._h}+{self._x}+{self._y}")
        cfg = dict(self._config.get("clipboard_widget", {}))
        cfg["w"] = self._w
        cfg["h"] = self._h
        self._config.set("clipboard_widget", cfg)
        # Refresh region so rounded corners stay correct after resize
        if self._hwnd:
            _apply_rounded_corners(self._hwnd)

    # ── Clipboard copy helpers ────────────────────────────────────────────

    def _copy_item(self, idx: int):
        if idx >= len(self._history):
            return
        entry = self._history[idx]
        self._suppress_next = True
        if entry["type"] == "text":
            self._set_clipboard_text(entry["content"])
        elif entry["type"] == "image":
            self._set_clipboard_image(entry["pil"])

    def _set_clipboard_text(self, text: str):
        if not _HAS_WIN32:
            return
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            win32clipboard.CloseClipboard()
        except Exception:
            pass

    def _set_clipboard_image(self, img: Image.Image):
        if not _HAS_WIN32:
            return
        try:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "BMP")
            dib = buf.getvalue()[14:]
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
            win32clipboard.CloseClipboard()
        except Exception:
            pass

    # ── History list ──────────────────────────────────────────────────────

    def _add_to_history(self, entry: dict):
        if entry["type"] == "text":
            self._history = [e for e in self._history
                             if not (e["type"] == "text"
                                     and e["content"] == entry["content"])]
        self._history.insert(0, entry)
        self._history = self._history[:MAX_HISTORY]
        self._refresh_list()

    def _refresh_list(self):
        for w in self._list_frame.winfo_children():
            w.destroy()
        self._photos.clear()
        for i, entry in enumerate(self._history):
            self._build_history_row(i, entry)
        self._list_frame.update_idletasks()
        self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))

    def _build_history_row(self, idx: int, entry: dict):
        row = tk.Frame(self._list_frame, bg=_ITEM_BG, cursor="hand2")
        row.pack(fill="x", pady=1, padx=2)

        if entry["type"] == "image":
            thumb = entry["pil"].copy()
            thumb.thumbnail((THUMB_W, THUMB_H), Image.LANCZOS)
            photo = ImageTk.PhotoImage(thumb)
            self._photos.append(photo)
            img_lbl = tk.Label(row, image=photo, bg=_ITEM_BG, cursor="hand2")
            img_lbl.pack(side="left", padx=(4, 6), pady=3)
            info_lbl = tk.Label(
                row, text=f"{entry['pil'].width} × {entry['pil'].height}",
                bg=_ITEM_BG, fg=_FG, font=("Segoe UI", 8), anchor="w", cursor="hand2")
            info_lbl.pack(side="left", fill="x", expand=True)
            widgets = [row, img_lbl, info_lbl]
        else:
            txt_lbl = tk.Label(
                row, text=entry["display"].strip(),
                bg=_ITEM_BG, fg=_FG,
                font=("Consolas", 8), anchor="w", padx=6, cursor="hand2")
            txt_lbl.pack(fill="x", ipady=4)
            widgets = [row, txt_lbl]

        def on_enter(e, ws=widgets):
            for w in ws:
                try: w.configure(bg=_SEL_BG)
                except Exception: pass

        def on_leave(e, ws=widgets):
            for w in ws:
                try: w.configure(bg=_ITEM_BG)
                except Exception: pass

        for w in widgets:
            w.bind("<Enter>",           on_enter)
            w.bind("<Leave>",           on_leave)
            w.bind("<Double-Button-1>", lambda e, i=idx: self._copy_item(i))
            w.bind("<MouseWheel>",      self._on_mousewheel)

    # ── Notes: image insert/paste/save/load ───────────────────────────────

    def _insert_note_image(self, pil_img: Image.Image):
        """Resize and embed image at cursor position in the notes widget."""
        if pil_img.width > NOTE_IMG_MAX_W:
            ratio = NOTE_IMG_MAX_W / pil_img.width
            pil_img = pil_img.resize(
                (NOTE_IMG_MAX_W, int(pil_img.height * ratio)), Image.LANCZOS)

        photo = ImageTk.PhotoImage(pil_img)
        name  = f"note_img_{self._note_img_counter}"
        self._note_img_counter += 1
        self._note_photos[name] = photo
        self._note_pil[name]    = pil_img

        self._txt.image_create("insert", image=photo, name=name)
        self._txt.insert("insert", "\n")
        self._save_notes()

    def _insert_image_from_file(self):
        path = filedialog.askopenfilename(
            parent=self._win,
            title="选择图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                       ("所有文件", "*.*")],
        )
        if path:
            try:
                self._insert_note_image(Image.open(path).copy())
            except Exception:
                pass

    def _on_note_paste(self, event):
        """Ctrl+V: paste image from clipboard if available, else default text paste."""
        try:
            obj = ImageGrab.grabclipboard()
            if isinstance(obj, Image.Image):
                self._insert_note_image(obj.copy())
                return "break"   # suppress default paste
        except Exception:
            pass
        return None   # let tkinter do default text paste

    def _save_notes(self, event=None):
        """Save notes as a list of {type, content/data} segments."""
        segments: list[dict] = []
        try:
            for item_type, value, _ in self._txt.dump(
                    "1.0", "end-1c", image=True, text=True):
                if item_type == "text":
                    segments.append({"type": "text", "content": value})
                elif item_type == "image":
                    pil = self._note_pil.get(value)
                    if pil:
                        buf = io.BytesIO()
                        pil.save(buf, format="PNG")
                        segments.append({
                            "type": "image",
                            "data": base64.b64encode(buf.getvalue()).decode(),
                        })
        except Exception:
            pass
        self._config.set("clipboard_notes", segments)

    def _load_notes(self):
        """Load notes from config, handling both old (str list) and new (segment) formats."""
        saved = self._config.get("clipboard_notes", [])
        if not saved:
            return

        # Old format: plain list of strings
        if isinstance(saved[0], str):
            self._txt.insert("1.0", "\n".join(saved))
            return

        # New format: list of {type, ...} segments
        for seg in saved:
            seg_type = seg.get("type")
            if seg_type == "text":
                self._txt.insert("end", seg.get("content", ""))
            elif seg_type == "image":
                try:
                    raw = base64.b64decode(seg["data"])
                    pil = Image.open(io.BytesIO(raw))
                    photo = ImageTk.PhotoImage(pil)
                    name  = f"note_img_{self._note_img_counter}"
                    self._note_img_counter += 1
                    self._note_photos[name] = photo
                    self._note_pil[name]    = pil
                    self._txt.image_create("end", image=photo, name=name)
                except Exception:
                    pass

    # ── Monitor ───────────────────────────────────────────────────────────

    def _start_monitor(self):
        if not _HAS_WIN32:
            return
        self._running = True
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _monitor_loop(self):
        try:
            last_seq = win32clipboard.GetClipboardSequenceNumber()
        except Exception:
            return
        while self._running:
            time.sleep(0.4)
            try:
                seq = win32clipboard.GetClipboardSequenceNumber()
                if seq != last_seq:
                    last_seq = seq
                    if self._suppress_next:
                        self._suppress_next = False
                        continue
                    entry = self._read_clipboard()
                    if entry:
                        self._root.after(0, lambda e=entry: self._add_to_history(e))
            except Exception:
                pass

    def _read_clipboard(self) -> dict | None:
        try:
            obj = ImageGrab.grabclipboard()
            if isinstance(obj, Image.Image):
                img = obj.copy()
                w, h = img.size
                return {"type": "image", "pil": img,
                        "display": f"[图片  {w} × {h}]"}
            if isinstance(obj, list) and obj:
                text = "\n".join(str(p) for p in obj)
                return {"type": "text", "content": text,
                        "display": "  " + text[:60].replace("\n", " ↵ ")}
        except Exception:
            pass
        if not _HAS_WIN32:
            return None
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                    if text and text.strip():
                        return {"type": "text", "content": text,
                                "display": "  " + text[:60].replace("\n", " ↵ ")}
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass
        return None

    # ── Visibility ────────────────────────────────────────────────────────

    def lift(self):
        if self._win and self._config.get("clipboard_widget", {}).get("visible", False):
            self._win.lift()

    def show(self):
        if self._win:
            self._win.deiconify()
            # Re-apply glass effects after deiconify (Win10 may reset region)
            if self._hwnd:
                self._win.after(50, lambda: _apply_rounded_corners(self._hwnd))
            self._win.lift()
        cfg = dict(self._config.get("clipboard_widget", {}))
        cfg["visible"] = True
        self._config.set("clipboard_widget", cfg)

    def hide(self):
        if self._win:
            self._win.withdraw()
        cfg = dict(self._config.get("clipboard_widget", {}))
        cfg["visible"] = False
        self._config.set("clipboard_widget", cfg)

    def toggle(self):
        if self._config.get("clipboard_widget", {}).get("visible", False):
            self.hide()
        else:
            self.show()

    def stop(self):
        self._running = False

    def destroy(self):
        self.stop()
        if self._win:
            self._win.destroy()
