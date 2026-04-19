import tkinter as tk
from tkinter import simpledialog

HANDLE = 12   # px near bottom-right corner that activates resize
MIN_W = 80
MIN_H = 60
GRID_SNAP = 8


class Section:
    """
    A labelled, resizable rectangle drawn on the canvas as a visual organiser.
    Drag the body to move; drag the bottom-right corner handle to resize.
    Double-click to rename; right-click to delete.
    """

    def __init__(self, canvas: tk.Canvas, sec_data: dict, config,
                 on_update, on_remove):
        self.canvas = canvas
        self.sec_id = sec_data["id"]
        self.label = sec_data.get("label", "区域")
        self.x = sec_data["x"]
        self.y = sec_data["y"]
        self.w = sec_data["w"]
        self.h = sec_data["h"]
        self.color = sec_data.get("color", "#2a2a4a")
        self._config = config
        self._on_update = on_update
        self._on_remove = on_remove

        self._mode = None   # 'move' | 'resize'
        self._sx = self._sy = 0

        self._rect = self._lbl = self._handle = None
        self._draw()

    # --- Drawing ---

    def _draw(self):
        x1, y1 = self.x, self.y
        x2, y2 = self.x + self.w, self.y + self.h
        tag = ("section", self.sec_id)

        self._rect = self.canvas.create_rectangle(
            x1, y1, x2, y2,
            fill=self.color, outline="#5a5a8a", width=1,
            stipple="gray25", tags=tag,
        )
        self._lbl = self.canvas.create_text(
            x1 + 8, y1 + 7,
            text=self.label,
            fill="#9999cc", font=("Segoe UI", 9, "bold"),
            anchor="nw", tags=tag,
        )
        # Resize grip — small filled square at bottom-right
        self._handle = self.canvas.create_rectangle(
            x2 - HANDLE, y2 - HANDLE, x2, y2,
            fill="#5a5a9a", outline="", tags=tag,
        )

        # z-order is managed by overlay._arrange_z_order() after all items are loaded

        for item in (self._rect, self._lbl, self._handle):
            self.canvas.tag_bind(item, "<ButtonPress-1>",   self._on_press)
            self.canvas.tag_bind(item, "<B1-Motion>",       self._on_drag)
            self.canvas.tag_bind(item, "<ButtonRelease-1>", self._on_release)
            self.canvas.tag_bind(item, "<Double-Button-1>", self._on_double_click)
            self.canvas.tag_bind(item, "<Button-3>",        self._on_right_click)

    def _redraw_coords(self):
        x1, y1 = self.x, self.y
        x2, y2 = self.x + self.w, self.y + self.h
        self.canvas.coords(self._rect, x1, y1, x2, y2)
        self.canvas.coords(self._lbl,  x1 + 8, y1 + 7)
        self.canvas.coords(self._handle, x2 - HANDLE, y2 - HANDLE, x2, y2)

    # --- Interaction ---

    def _on_press(self, event):
        self._sx, self._sy = event.x, event.y
        x2, y2 = self.x + self.w, self.y + self.h
        # Bottom-right corner → resize; anywhere else → move
        if event.x >= x2 - HANDLE and event.y >= y2 - HANDLE:
            self._mode = "resize"
        else:
            self._mode = "move"

    def _on_drag(self, event):
        dx = event.x - self._sx
        dy = event.y - self._sy
        self._sx, self._sy = event.x, event.y
        if self._mode == "move":
            self.x += dx
            self.y += dy
            self.canvas.move(self.sec_id, dx, dy)
        elif self._mode == "resize":
            self.w = max(MIN_W, self.w + dx)
            self.h = max(MIN_H, self.h + dy)
            self._redraw_coords()

    def _on_release(self, event):
        if self._mode == "move":
            self.x = round(self.x / GRID_SNAP) * GRID_SNAP
            self.y = round(self.y / GRID_SNAP) * GRID_SNAP
        elif self._mode == "resize":
            self.w = round(self.w / GRID_SNAP) * GRID_SNAP
            self.h = round(self.h / GRID_SNAP) * GRID_SNAP
        self._redraw_coords()
        self._on_update(self.sec_id, x=self.x, y=self.y, w=self.w, h=self.h)
        self._mode = None

    def _on_double_click(self, event):
        name = simpledialog.askstring(
            "重命名区域", "区域名称:", initialvalue=self.label,
            parent=self.canvas,
        )
        if name:
            self.label = name
            self.canvas.itemconfig(self._lbl, text=name)
            self._on_update(self.sec_id, label=name)

    def _on_right_click(self, event):
        menu = tk.Menu(self.canvas, tearoff=0)
        menu.add_command(label="重命名", command=lambda: self._on_double_click(event))
        menu.add_separator()
        menu.add_command(label="删除分区框", command=self._remove)
        menu.tk_popup(event.x_root, event.y_root)

    def _remove(self):
        self.canvas.delete(self.sec_id)
        self._config.remove_section(self.sec_id)
        self._on_remove(self.sec_id)

    def destroy(self):
        self.canvas.delete(self.sec_id)
