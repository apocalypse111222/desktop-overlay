import ctypes
import io
import threading
import time
import tkinter as tk
import tkinter.ttk as ttk

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

_BG      = "#1a1a30"
_ITEM_BG = "#22223a"
_FG      = "#d0d0f0"
_DIM     = "#5a5a80"
_ACCENT  = "#4fc3f7"
_SEL_BG  = "#383868"
_TBAR    = "#141428"


# ── Acrylic (Windows 10 1803+ / Windows 11) ───────────────────────────

class _ACCENTPOLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

class _WNDCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t)]

def _apply_acrylic(hwnd: int, tint_abgr: int = 0xBB201e38):
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


# ── Dark scrollbar style ──────────────────────────────────────────────

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
                    background=_ITEM_BG, darkcolor=_BG, lightcolor="#303050",
                    troughcolor=_BG, arrowcolor=_DIM, borderwidth=0, relief="flat")
    style.map("Dark.Vertical.TScrollbar", background=[("active", "#303050")])
    _style_created = True


# ── Widget ────────────────────────────────────────────────────────────

class ClipboardWidget:
    """
    Floating Toplevel with acrylic/frosted-glass effect.
    Tab 1 — clipboard history (text + images, auto-captured, double-click to re-copy).
    Tab 2 — freeform memo (persisted to config.json).
    Drag title bar to move; drag bottom-right grip to resize.
    """

    def __init__(self, root: tk.Tk, config, on_position_changed):
        self._root = root
        self._config = config
        self._on_position_changed = on_position_changed
        self._history: list[dict] = []
        self._photos: list = []       # keep PhotoImage refs alive
        self._running = False
        self._suppress_next = False   # skip next clipboard event after self-copy

        cfg = config.get("clipboard_widget", {})
        self._x = cfg.get("x", 20)
        self._y = cfg.get("y", 100)
        self._w = cfg.get("w", 270)
        self._h = cfg.get("h", 400)

        _ensure_scrollbar_style()
        self._win: tk.Toplevel | None = None
        self._build()
        self._start_monitor()

    # ── Build UI ──────────────────────────────────────────────────────

    def _build(self):
        self._win = tk.Toplevel(self._root)
        self._win.overrideredirect(True)
        # NOT topmost — overlay.show() lifts us above the overlay after showing itself
        self._win.configure(bg=_BG)
        self._win.geometry(f"{self._w}x{self._h}+{self._x}+{self._y}")
        self._win.wm_attributes("-alpha", 0.93)
        self._win.update_idletasks()
        _apply_acrylic(self._win.winfo_id())

        # ── Title bar ─────────────────────────────────────────────────
        tbar = tk.Frame(self._win, bg=_TBAR, cursor="fleur")
        tbar.pack(fill="x", side="top")

        title_lbl = tk.Label(tbar, text="剪贴板 / 备忘录",
                             bg=_TBAR, fg=_DIM,
                             font=("Segoe UI", 9, "bold"), padx=8, pady=4)
        title_lbl.pack(side="left")

        close_btn = tk.Label(tbar, text="✕", bg=_TBAR, fg=_DIM,
                             font=("Segoe UI", 10), padx=10, cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self.hide())

        # Drag: only tbar frame and title label — NOT close button
        for w in (tbar, title_lbl):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>",     self._drag_move)

        # ── Bottom resize bar (packed before content so it anchors bottom) ──
        bottom = tk.Frame(self._win, bg=_TBAR, height=14)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)
        grip = tk.Label(bottom, text="◢", bg=_TBAR, fg=_DIM,
                        font=("Segoe UI", 9), cursor="size_nw_se", padx=4)
        grip.pack(side="right", pady=0)
        grip.bind("<ButtonPress-1>", self._resize_start)
        grip.bind("<B1-Motion>",     self._resize_move)

        # ── Tab selector ──────────────────────────────────────────────
        tab_row = tk.Frame(self._win, bg=_BG)
        tab_row.pack(fill="x", side="top", padx=6, pady=(5, 0))
        self._tab = tk.StringVar(value="history")
        for label, val in [("剪贴板历史", "history"), ("备忘录", "notes")]:
            rb = tk.Radiobutton(
                tab_row, text=label, variable=self._tab, value=val,
                command=self._switch_tab,
                bg=_BG, fg=_FG, selectcolor="#2a2a4a",
                activebackground=_BG, activeforeground=_FG,
                relief="flat", font=("Segoe UI", 8),
            )
            rb.pack(side="left", padx=2)

        tk.Frame(self._win, bg="#2a2a48", height=1).pack(fill="x", side="top")

        # ── History pane ──────────────────────────────────────────────
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
            (0, 0), window=self._list_frame, anchor="nw"
        )
        self._list_frame.bind("<Configure>", self._on_list_frame_configure)
        self._list_canvas.bind("<Configure>",  self._on_list_canvas_configure)
        self._list_canvas.bind("<MouseWheel>", self._on_mousewheel)

        tk.Label(self._pane_history, text="双击条目可重新复制",
                 bg=_BG, fg=_DIM, font=("Segoe UI", 7)).pack(pady=(0, 4))

        # ── Notes pane ────────────────────────────────────────────────
        self._pane_notes = tk.Frame(self._win, bg=_BG)

        note_row = tk.Frame(self._pane_notes, bg=_BG)
        note_row.pack(fill="both", expand=True, padx=6, pady=4)

        self._txt = tk.Text(
            note_row,
            bg=_ITEM_BG, fg=_FG, insertbackground=_ACCENT,
            font=("Segoe UI", 9), relief="flat", bd=6,
            wrap="word", undo=True, highlightthickness=0,
        )
        sb_n = ttk.Scrollbar(note_row, orient="vertical",
                             style="Dark.Vertical.TScrollbar",
                             command=self._txt.yview)
        self._txt.configure(yscrollcommand=sb_n.set)
        sb_n.pack(side="right", fill="y")
        self._txt.pack(side="left", fill="both", expand=True)

        saved = "\n".join(self._config.get("clipboard_notes", []))
        if saved:
            self._txt.insert("1.0", saved)
        self._txt.bind("<KeyRelease>", self._save_notes)

        self._switch_tab()

        if not self._config.get("clipboard_widget", {}).get("visible", False):
            self._win.withdraw()

    # ── Scrollable list helpers ───────────────────────────────────────

    def _on_list_frame_configure(self, event):
        self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))

    def _on_list_canvas_configure(self, event):
        self._list_canvas.itemconfig(self._list_win_id, width=event.width)

    def _on_mousewheel(self, event):
        self._list_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ── Tabs ──────────────────────────────────────────────────────────

    def _switch_tab(self):
        self._pane_history.pack_forget()
        self._pane_notes.pack_forget()
        if self._tab.get() == "history":
            self._pane_history.pack(fill="both", expand=True)
        else:
            self._pane_notes.pack(fill="both", expand=True)

    # ── Drag ──────────────────────────────────────────────────────────

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

    # ── Resize ────────────────────────────────────────────────────────

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

    # ── Copy item ─────────────────────────────────────────────────────

    def _copy_item(self, idx: int):
        if idx >= len(self._history):
            return
        entry = self._history[idx]
        self._suppress_next = True   # prevent re-adding this copy to history
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
            dib = buf.getvalue()[14:]   # strip 14-byte BMP file header → raw DIB
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
            win32clipboard.CloseClipboard()
        except Exception:
            pass

    # ── History list ──────────────────────────────────────────────────

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
            self._build_row(i, entry)

        self._list_frame.update_idletasks()
        self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))

    def _build_row(self, idx: int, entry: dict):
        row = tk.Frame(self._list_frame, bg=_ITEM_BG, cursor="hand2")
        row.pack(fill="x", pady=1, padx=2)

        if entry["type"] == "image":
            pil = entry["pil"]
            thumb = pil.copy()
            thumb.thumbnail((THUMB_W, THUMB_H), Image.LANCZOS)
            photo = ImageTk.PhotoImage(thumb)
            self._photos.append(photo)

            img_lbl = tk.Label(row, image=photo, bg=_ITEM_BG, cursor="hand2")
            img_lbl.pack(side="left", padx=(4, 6), pady=3)

            info_lbl = tk.Label(
                row,
                text=f"{pil.width} × {pil.height}",
                bg=_ITEM_BG, fg=_FG,
                font=("Segoe UI", 8), anchor="w", cursor="hand2",
            )
            info_lbl.pack(side="left", fill="x", expand=True)
            widgets = [row, img_lbl, info_lbl]
        else:
            txt_lbl = tk.Label(
                row,
                text=entry["display"].strip(),
                bg=_ITEM_BG, fg=_FG,
                font=("Consolas", 8), anchor="w", padx=6, cursor="hand2",
            )
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

        def on_dbl(e, i=idx):
            self._copy_item(i)

        for w in widgets:
            w.bind("<Enter>",            on_enter)
            w.bind("<Leave>",            on_leave)
            w.bind("<Double-Button-1>",  on_dbl)
            w.bind("<MouseWheel>",       self._on_mousewheel)

    # ── Notes ─────────────────────────────────────────────────────────

    def _save_notes(self, event=None):
        self._config.set("clipboard_notes",
                         self._txt.get("1.0", "end-1c").split("\n"))

    # ── Monitor ───────────────────────────────────────────────────────

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
                        continue   # ignore the copy we triggered ourselves
                    entry = self._read_clipboard()
                    if entry:
                        self._root.after(0, lambda e=entry: self._add_to_history(e))
            except Exception:
                pass

    def _read_clipboard(self) -> dict | None:
        # Try image first via ImageGrab (handles CF_BITMAP / CF_DIB cleanly)
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

        # Text fallback via win32
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

    # ── Visibility ────────────────────────────────────────────────────

    def lift(self):
        """Raise above the overlay after the overlay lifts itself."""
        if self._win and self._config.get("clipboard_widget", {}).get("visible", False):
            self._win.lift(self._root)   # explicitly above the overlay root window

    def show(self):
        if self._win:
            self._win.deiconify()
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
