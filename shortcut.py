import os
import subprocess
import tkinter as tk
from typing import Callable, Optional

from PIL import Image, ImageTk

ICON_SIZE = 48
LABEL_WIDTH = 80
DRAG_DEAD_ZONE = 4
GRID_SNAP = 8


class Shortcut:
    """
    One draggable shortcut item rendered on a tk.Canvas.
    Owns two canvas items (image/rect + text) grouped under tag sc_id.
    """

    def __init__(self, canvas: tk.Canvas, sc_data: dict, config,
                 on_position_changed, on_remove, on_launch=None,
                 on_group_drag: Optional[Callable] = None,
                 on_group_drag_end: Optional[Callable] = None):
        self.canvas = canvas
        self.sc_id = sc_data["id"]
        self.label = sc_data["label"]
        self.target = sc_data["target"]
        self.x = sc_data["x"]
        self.y = sc_data["y"]
        self._config = config
        self._on_position_changed = on_position_changed
        self._on_remove = on_remove
        self._on_launch = on_launch
        self._on_group_drag = on_group_drag
        self._on_group_drag_end = on_group_drag_end

        self._drag_sx = 0
        self._drag_sy = 0
        self._dragging = False
        self._drag_moved = False

        # Selection state
        self._selected = False
        self._sel_item = None

        self._photo = self._load_icon(sc_data.get("icon_cache"))
        self._draw()

    # --- Icon loading ---

    def _load_icon(self, cache_path):
        if cache_path and os.path.exists(cache_path):
            try:
                img = Image.open(cache_path).resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
                return ImageTk.PhotoImage(img)
            except Exception:
                pass
        return None

    # --- Drawing ---

    def _draw(self):
        cx = self.x + LABEL_WIDTH // 2

        if self._photo:
            self._img_item = self.canvas.create_image(
                cx, self.y, anchor="n", image=self._photo,
                tags=("shortcut", self.sc_id),
            )
        else:
            self._img_item = self.canvas.create_rectangle(
                cx - ICON_SIZE // 2, self.y,
                cx + ICON_SIZE // 2, self.y + ICON_SIZE,
                fill="#556677", outline="", tags=("shortcut", self.sc_id),
            )

        self._text_item = self.canvas.create_text(
            cx, self.y + ICON_SIZE + 3,
            text=self.label,
            fill="white",
            font=("Segoe UI", 9),
            width=LABEL_WIDTH,
            anchor="n",
            tags=("shortcut", self.sc_id),
        )

        for item in (self._img_item, self._text_item):
            self.canvas.tag_bind(item, "<ButtonPress-1>", self._on_press)
            self.canvas.tag_bind(item, "<B1-Motion>", self._on_drag)
            self.canvas.tag_bind(item, "<ButtonRelease-1>", self._on_release)
            self.canvas.tag_bind(item, "<Double-Button-1>", self._on_double_click)
            self.canvas.tag_bind(item, "<Button-3>", self._on_right_click)

    # --- Selection ---

    def set_selected(self, selected: bool):
        self._selected = selected
        if selected and not self._sel_item:
            cx = self.x + LABEL_WIDTH // 2
            pad = 5
            self._sel_item = self.canvas.create_rectangle(
                cx - ICON_SIZE // 2 - pad, self.y - pad,
                cx + ICON_SIZE // 2 + pad, self.y + ICON_SIZE + pad,
                outline="#4fc3f7", width=2, fill="",
                tags=("shortcut", self.sc_id),
            )
            self.canvas.tag_lower(self._sel_item, self._img_item)
        elif not selected and self._sel_item:
            self.canvas.delete(self._sel_item)
            self._sel_item = None

    # --- Movement ---

    def move_by(self, dx: int, dy: int):
        """Move canvas items without snapping (used during group drag)."""
        self.canvas.move(self.sc_id, dx, dy)
        self.x += dx
        self.y += dy

    # --- Drag ---

    def _on_press(self, event):
        self._drag_sx = event.x
        self._drag_sy = event.y
        self._dragging = False
        self._drag_moved = False
        self.canvas.tag_raise(self.sc_id)

    def _on_drag(self, event):
        dx = event.x - self._drag_sx
        dy = event.y - self._drag_sy
        if abs(dx) > DRAG_DEAD_ZONE or abs(dy) > DRAG_DEAD_ZONE:
            self._dragging = True
            self._drag_moved = True
        if self._dragging:
            if self._selected and self._on_group_drag:
                self._on_group_drag(self.sc_id, dx, dy)
            else:
                self.move_by(dx, dy)
            self._drag_sx = event.x
            self._drag_sy = event.y

    def _on_release(self, event):
        if self._dragging:
            if self._selected and self._on_group_drag_end:
                self._on_group_drag_end(self.sc_id)
            else:
                self.x = round(self.x / GRID_SNAP) * GRID_SNAP
                self.y = round(self.y / GRID_SNAP) * GRID_SNAP
                self._on_position_changed(self.sc_id, self.x, self.y)
        self._dragging = False

    # --- Launch ---

    def _on_double_click(self, event):
        if not self._drag_moved:
            self._launch()

    def _launch(self):
        if self._on_launch:
            self._on_launch()
        try:
            os.startfile(self.target)
        except Exception:
            try:
                subprocess.Popen([self.target], shell=False)
            except Exception:
                pass

    # --- Context menu ---

    def _on_right_click(self, event):
        menu = tk.Menu(self.canvas, tearoff=0)
        menu.add_command(label=f"打开  {self.label}", command=self._launch)
        menu.add_separator()
        menu.add_command(label="删除快捷方式", command=self._remove)
        menu.tk_popup(event.x_root, event.y_root)

    def _remove(self):
        self.canvas.delete(self.sc_id)
        self._config.remove_shortcut(self.sc_id)
        self._on_remove(self.sc_id)

    def destroy(self):
        self.canvas.delete(self.sc_id)
