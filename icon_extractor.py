import ctypes
import ctypes.wintypes as wt
import os
import winreg

from PIL import Image, ImageChops, ImageOps

ICON_SIZE = 64        # display size on canvas (larger = crisper at HiDPI)
_JUMBO_SIZE = 256    # extraction size via SHGetImageList SHIL_JUMBO

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32

# SHGetFileInfoW returns DWORD_PTR — must set c_size_t on 64-bit or the check gives wrong results
shell32.SHGetFileInfoW.restype = ctypes.c_size_t

SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wt.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wt.DWORD),
        ("szDisplayName", ctypes.c_wchar * 260),
        ("szTypeName", ctypes.c_wchar * 80),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


# ---------- High-quality icon via SHGetImageList ----------

class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

# IID_IImageList = {46EB5926-582E-4017-9FDF-E8998DAA0950}
_IID_IImageList = _GUID(
    0x46EB5926, 0x582E, 0x4017,
    (ctypes.c_ubyte * 8)(0x9F, 0xDF, 0xE8, 0x99, 0x8D, 0xAA, 0x09, 0x50),
)

shell32.SHGetImageList.restype = ctypes.c_long  # HRESULT


def _get_jumbo_icon(file_path: str) -> "Image.Image | None":
    """
    Get the file's system icon at 256×256 via SHGetImageList(SHIL_JUMBO).
    SHGFI_SYSICONINDEX only reads the icon index — no actual HICON created,
    avoids the COM cross-process issue that plagues elevated processes.
    Result is downscaled to ICON_SIZE for crisp display.
    """
    SHIL_JUMBO = 4
    SHGFI_SYSICONINDEX = 0x4000
    ILD_TRANSPARENT = 0x1

    try:
        shfi = _SHFILEINFOW()
        res = shell32.SHGetFileInfoW(
            file_path, 0, ctypes.byref(shfi), ctypes.sizeof(shfi),
            SHGFI_SYSICONINDEX,
        )
        if not res:
            return None
        icon_idx = shfi.iIcon

        iml_ptr = ctypes.c_void_p()
        hr = shell32.SHGetImageList(SHIL_JUMBO, ctypes.byref(_IID_IImageList), ctypes.byref(iml_ptr))
        if hr != 0 or not iml_ptr.value:
            return None

        # Call IImageList::GetIcon through the vtable.
        # Slot layout (0-based): 0=QI, 1=AddRef, 2=Release, 3=Add, 4=ReplaceIcon,
        # 5=SetOverlayImage, 6=Replace, 7=AddMasked, 8=Draw, 9=Remove, 10=GetIcon
        ptr_sz = ctypes.sizeof(ctypes.c_void_p)
        vtbl = ctypes.cast(iml_ptr, ctypes.POINTER(ctypes.c_void_p))[0]
        fn_addr = ctypes.cast(vtbl + 10 * ptr_sz, ctypes.POINTER(ctypes.c_void_p))[0]

        _GetIcon = ctypes.WINFUNCTYPE(
            ctypes.c_long,                        # HRESULT
            ctypes.c_void_p,                      # this
            ctypes.c_int,                         # i
            ctypes.c_uint,                        # flags
            ctypes.POINTER(ctypes.c_void_p),      # phicon (out)
        )
        hicon = ctypes.c_void_p()
        hr = _GetIcon(fn_addr)(iml_ptr.value, icon_idx, ILD_TRANSPARENT, ctypes.byref(hicon))
        if hr != 0 or not hicon.value:
            return None

        img = _hicon_to_pil(hicon.value, _JUMBO_SIZE)
        user32.DestroyIcon(hicon.value)
        # Downscale from 256 → ICON_SIZE: always sharper than upscaling
        return img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    except Exception:
        return None


# ---------- Thumbnail via IShellItemImageFactory ----------

# IID_IShellItem           = {43826D1E-E718-42EE-BC55-A1E261C37BFE}
# IID_IShellItemImageFactory = {BCC18B79-BA16-442F-80C4-8A59C30C463B}
_IID_IShellItem = _GUID(
    0x43826D1E, 0xE718, 0x42EE,
    (ctypes.c_ubyte * 8)(0xBC, 0x55, 0xA1, 0xE2, 0x61, 0xC3, 0x7B, 0xFE),
)
_IID_IShellItemImageFactory = _GUID(
    0xBCC18B79, 0xBA16, 0x442F,
    (ctypes.c_ubyte * 8)(0x80, 0xC4, 0x8A, 0x59, 0xC3, 0x0C, 0x46, 0x3B),
)


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


_QIProto = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p,
    ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
)
_ReleaseProto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_GetImageProto = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, _SIZE,
    ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
)

shell32.SHCreateItemFromParsingName.restype = ctypes.c_long


def _hbitmap_to_pil(hbm, size: int) -> "Image.Image | None":
    """Convert an HBITMAP to a PIL RGBA Image."""
    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    old_bm = gdi32.SelectObject(hdc_mem, hbm)

    bmi = _BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth = size
    bmi.biHeight = -size
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    buf = (ctypes.c_ubyte * (size * size * 4))()
    gdi32.GetDIBits(hdc_mem, hbm, 0, size, buf, ctypes.byref(bmi), 0)

    gdi32.SelectObject(hdc_mem, old_bm)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    img = Image.frombuffer("RGBA", (size, size), bytes(buf), "raw", "BGRA", 0, 1)
    _, _, _, a = img.split()
    if max(a.getdata()) > 0:
        return img
    # No alpha channel — make fully opaque
    r, g, b, _ = img.split()
    return Image.merge("RGBA", (r, g, b, Image.new("L", (size, size), 255)))


def _get_thumbnail(file_path: str, size: int = ICON_SIZE) -> "Image.Image | None":
    """
    Get the file's thumbnail (actual content preview) via IShellItemImageFactory::GetImage.
    This is the same API Windows Explorer uses for showing PDF page previews,
    image thumbnails, video frames, etc.
    Falls back gracefully (returns None) if no thumbnail handler exists for this file type.
    """
    try:
        import pythoncom
        pythoncom.CoInitialize()

        ptr_sz = ctypes.sizeof(ctypes.c_void_p)

        # Create IShellItem from path
        item = ctypes.c_void_p()
        hr = shell32.SHCreateItemFromParsingName(
            file_path, None,
            ctypes.byref(_IID_IShellItem),
            ctypes.byref(item),
        )
        if hr != 0 or not item.value:
            return None

        vtbl_item = ctypes.cast(item, ctypes.POINTER(ctypes.c_void_p))[0]

        # QI for IShellItemImageFactory (vtable[0] = QueryInterface)
        qi = _QIProto(ctypes.cast(vtbl_item + 0 * ptr_sz, ctypes.POINTER(ctypes.c_void_p))[0])
        factory = ctypes.c_void_p()
        hr_qi = qi(item.value, ctypes.byref(_IID_IShellItemImageFactory), ctypes.byref(factory))

        # Release IShellItem (vtable[2] = Release)
        _ReleaseProto(
            ctypes.cast(vtbl_item + 2 * ptr_sz, ctypes.POINTER(ctypes.c_void_p))[0]
        )(item.value)

        if hr_qi != 0 or not factory.value:
            return None

        vtbl_fac = ctypes.cast(factory, ctypes.POINTER(ctypes.c_void_p))[0]

        # Call GetImage (vtable[3]), flag=0: thumbnail if available, icon otherwise
        get_image = _GetImageProto(
            ctypes.cast(vtbl_fac + 3 * ptr_sz, ctypes.POINTER(ctypes.c_void_p))[0]
        )
        hbm = ctypes.c_void_p()
        hr_img = get_image(factory.value, _SIZE(size, size), 0, ctypes.byref(hbm))

        # Release factory (vtable[2] = Release)
        _ReleaseProto(
            ctypes.cast(vtbl_fac + 2 * ptr_sz, ctypes.POINTER(ctypes.c_void_p))[0]
        )(factory.value)

        if hr_img != 0 or not hbm.value:
            return None

        img = _hbitmap_to_pil(hbm.value, size)
        gdi32.DeleteObject(hbm.value)
        return img
    except Exception:
        return None


# ---------- LNK resolution ----------

def _resolve_lnk(lnk_path: str):
    """
    Resolve a .lnk shortcut via WScript.Shell (in-process COM, works even elevated).
    Returns (target_path, icon_path, icon_index) — all may be empty strings.
    """
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        ws = win32com.client.Dispatch("WScript.Shell")
        sc = ws.CreateShortCut(lnk_path)
        target = (sc.Targetpath or "").strip()
        icon_loc = (sc.IconLocation or "").strip()
        icon_path, icon_idx = "", 0
        if icon_loc:
            if "," in icon_loc:
                parts = icon_loc.rsplit(",", 1)
                icon_path = parts[0].strip()
                try:
                    icon_idx = int(parts[1].strip())
                except ValueError:
                    pass
            else:
                icon_path = icon_loc
        return target, icon_path, icon_idx
    except Exception:
        return "", "", 0


# ---------- Icon extraction ----------

def _extract_via_extracticonex(path: str, idx: int = 0):
    """
    ExtractIconExW reads icon resources directly from the PE file — no COM,
    no shell process dependency, works fine from elevated processes.
    """
    if not path or not os.path.exists(path):
        return None
    try:
        large = (wt.HICON * 1)()
        n = shell32.ExtractIconExW(path, idx, large, None, 1)
        if n >= 1 and large[0]:
            img = _hicon_to_pil(large[0])
            user32.DestroyIcon(large[0])
            return img
    except Exception:
        pass
    return None


def _extract_via_shgetfileinfo(path: str):
    """Fallback: SHGetFileInfoW — may fail when elevated, but worth trying."""
    try:
        shfi = _SHFILEINFOW()
        res = shell32.SHGetFileInfoW(
            path, 0, ctypes.byref(shfi), ctypes.sizeof(shfi),
            SHGFI_ICON | SHGFI_LARGEICON,
        )
        if res and shfi.hIcon:
            img = _hicon_to_pil(shfi.hIcon)
            user32.DestroyIcon(shfi.hIcon)
            return img
    except Exception:
        pass
    return None


def _get_icon_via_registry(file_path: str) -> "Image.Image | None":
    """
    Read HKCR\.ext → ProgID → DefaultIcon to locate the icon source,
    then extract via ExtractIconExW.  Pure registry + file I/O — works elevated.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if not ext:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ext) as k:
            prog_id = winreg.QueryValue(k, "")
        if not prog_id:
            return None
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"{prog_id}\\DefaultIcon") as k:
            icon_str = winreg.QueryValue(k, "").strip()
    except OSError:
        return None

    if not icon_str or icon_str.startswith("%1"):
        # %1 = use the file itself as icon source (e.g. .ico files)
        if ext == ".ico":
            return _extract_via_extracticonex(file_path, 0)
        return None

    if "," in icon_str:
        parts = icon_str.rsplit(",", 1)
        icon_path = parts[0].strip().strip('"')
        try:
            icon_idx = int(parts[1].strip())
        except ValueError:
            icon_idx = 0
    else:
        icon_path = icon_str.strip('"')
        icon_idx = 0

    icon_path = os.path.expandvars(icon_path)
    if not os.path.exists(icon_path):
        return None
    return _extract_via_extracticonex(icon_path, icon_idx)


def _get_folder_icon() -> "Image.Image | None":
    """Get the standard folder icon via SHGetStockIconInfo (Vista+)."""
    try:
        class _SHSTOCKICONINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wt.DWORD),
                ("hIcon", wt.HICON),
                ("iSysImageIndex", ctypes.c_int),
                ("iIcon", ctypes.c_int),
                ("szPath", ctypes.c_wchar * 260),
            ]

        SIID_FOLDER = 3
        SHGSI_ICON = 0x100

        sii = _SHSTOCKICONINFO()
        sii.cbSize = ctypes.sizeof(_SHSTOCKICONINFO)
        hr = shell32.SHGetStockIconInfo(SIID_FOLDER, SHGSI_ICON, ctypes.byref(sii))
        if hr == 0 and sii.hIcon:
            img = _hicon_to_pil(sii.hIcon)
            user32.DestroyIcon(sii.hIcon)
            return img
    except Exception:
        pass
    # Fallback: extract from shell32.dll (index 3 = closed folder)
    shell32_path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "system32", "shell32.dll")
    return _extract_via_extracticonex(shell32_path, 3)


def extract_icon(file_path: str) -> Image.Image:
    file_path = os.path.normpath(file_path)

    # --- Directories ---
    if os.path.isdir(file_path):
        img = _get_thumbnail(file_path, ICON_SIZE)
        if img:
            return img
        img = _get_jumbo_icon(file_path)
        if img:
            return img
        img = _get_folder_icon()
        if img:
            return img
        return _fallback_icon()

    # --- .lnk shortcuts ---
    if file_path.lower().endswith(".lnk"):
        target, icon_path, icon_idx = _resolve_lnk(file_path)
        src = icon_path or target

        if src and os.path.exists(src):
            img = _get_jumbo_icon(src)
            if img:
                return img

        img = _get_jumbo_icon(file_path)
        if img:
            return img

        if src and os.path.exists(src):
            img = _extract_via_extracticonex(src, icon_idx if src == icon_path else 0)
            if img:
                return img
            img = _get_icon_via_registry(src)
            if img:
                return img

        img = _extract_via_shgetfileinfo(file_path)
        if img:
            return img
        return _fallback_icon()

    # --- Regular files ---
    # IShellItemImageFactory gives real thumbnails for images, PDFs, videos
    img = _get_thumbnail(file_path, ICON_SIZE)
    if img:
        return img

    # 256×256 jumbo system icon
    img = _get_jumbo_icon(file_path)
    if img:
        return img

    # ExtractIconExW: works for .exe/.dll/.ico (embedded resources)
    img = _extract_via_extracticonex(file_path, 0)
    if img:
        return img

    # Registry lookup: finds associated app icon for .pdf, .docx, .mp4, etc.
    img = _get_icon_via_registry(file_path)
    if img:
        return img

    # Last resort shell API (may fail elevated)
    img = _extract_via_shgetfileinfo(file_path)
    if img:
        return img

    return _fallback_icon()


# ---------- HICON → PIL ----------

def _hicon_to_pil(hicon, size: int = ICON_SIZE) -> Image.Image:
    """
    Convert HICON to PIL RGBA Image with proper transparency.

    Primary: 32-bit DIB section — GDI preserves the alpha channel for modern
    32-bit ARGB icons (Chrome, Fluent icons, etc.) so transparent areas are
    genuinely transparent, not white.

    Fallback: double-render (black + white) then alpha = 255-(r_white-r_black).
    Works for old-style XOR/AND-mask icons.
    """
    WHITENESS = 0x00FF0062
    BLACKNESS = 0x00000042

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)

    bmi = _BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth = size
    bmi.biHeight = -size   # negative = top-down scan order
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    # 32-bit DIB section carries the alpha channel through DrawIconEx
    gdi32.CreateDIBSection.restype = wt.HBITMAP
    hbm = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0, None, None, 0)
    use_dib = bool(hbm)
    if not use_dib:
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, size, size)

    old_bm = gdi32.SelectObject(hdc_mem, hbm)
    buf = (ctypes.c_ubyte * (size * size * 4))()

    try:
        # Render on black — transparent pixels stay (0,0,0,0)
        gdi32.PatBlt(hdc_mem, 0, 0, size, size, BLACKNESS)
        user32.DrawIconEx(hdc_mem, 0, 0, hicon, size, size, 0, None, 3)
        gdi32.GetDIBits(hdc_mem, hbm, 0, size, buf, ctypes.byref(bmi), 0)
        img_black = Image.frombuffer("RGBA", (size, size), bytes(buf), "raw", "BGRA", 0, 1)

        if use_dib:
            _, _, _, a = img_black.split()
            if max(a.getdata()) > 0:
                # Alpha channel is valid — no white fringe, return directly
                return img_black

        # Fallback: render on white, compute alpha from channel difference
        gdi32.PatBlt(hdc_mem, 0, 0, size, size, WHITENESS)
        user32.DrawIconEx(hdc_mem, 0, 0, hicon, size, size, 0, None, 3)
        gdi32.GetDIBits(hdc_mem, hbm, 0, size, buf, ctypes.byref(bmi), 0)
        img_white = Image.frombuffer("RGBA", (size, size), bytes(buf), "raw", "BGRA", 0, 1)

        # alpha = 255 - (r_white - r_black); subtract clamps negatives to 0
        rb, gb, bb, _ = img_black.split()
        rw, _, _, _ = img_white.split()
        alpha = ImageOps.invert(ImageChops.subtract(rw, rb))
        return Image.merge("RGBA", (rb, gb, bb, alpha))

    finally:
        gdi32.SelectObject(hdc_mem, old_bm)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)


def _fallback_icon() -> Image.Image:
    return Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (80, 80, 100, 255))


def cache_icon(file_path: str, cache_path: str) -> bool:
    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    try:
        img = extract_icon(file_path)
        img.save(cache_path, "PNG")
        return True
    except Exception:
        return False
