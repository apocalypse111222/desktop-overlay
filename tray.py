import os
import threading

import pystray
from PIL import Image, ImageDraw


class TrayIcon:
    """
    System tray icon using pystray, running on a daemon thread.
    toggle_callback and exit_callback are called via root.after(0, ...) for thread safety.
    """

    def __init__(self, root, toggle_callback, exit_callback, icon_path: str | None = None):
        self.root = root
        self.toggle_callback = toggle_callback
        self.exit_callback = exit_callback
        self.icon_path = icon_path
        self._icon: pystray.Icon | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _load_image(self) -> Image.Image:
        if self.icon_path and os.path.exists(self.icon_path):
            try:
                return Image.open(self.icon_path).resize((64, 64), Image.LANCZOS).convert("RGBA")
            except Exception:
                pass
        return self._default_image()

    def _default_image(self) -> Image.Image:
        img = Image.new("RGBA", (64, 64), (20, 20, 40, 255))
        draw = ImageDraw.Draw(img)
        draw.ellipse([6, 6, 58, 58], fill=(70, 130, 210, 255), outline=(220, 220, 255, 200), width=2)
        # Simple grid lines to suggest "desktop"
        for i in range(16, 56, 12):
            draw.line([(16, i), (48, i)], fill=(220, 220, 255, 100), width=1)
        return img

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Show / Hide Overlay", self._on_toggle, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._on_exit),
        )

    def _on_toggle(self, icon, item):
        self.root.after(0, self.toggle_callback)

    def _on_exit(self, icon, item):
        icon.stop()
        self.root.after(0, self.exit_callback)

    def _run(self):
        img = self._load_image()
        self._icon = pystray.Icon(
            name="DesktopOverlay",
            icon=img,
            title="Desktop Overlay",
            menu=self._build_menu(),
        )
        self._icon.run()

    def stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
