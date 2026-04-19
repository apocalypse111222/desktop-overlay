import ctypes
import ctypes.wintypes as wt

SW_HIDE = 0
SW_SHOW = 5

user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def _find_desktop_listview():
    """
    Finds the SysListView32 HWND that renders desktop icons.
    Tries Progman path first; falls back to enumerating WorkerW windows.
    Returns HWND (int) or None.
    """
    # Path A: Progman -> SHELLDLL_DefView -> SysListView32
    progman = user32.FindWindowW("Progman", None)
    if progman:
        shelldll = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
        if shelldll:
            listview = user32.FindWindowExW(shelldll, None, "SysListView32", None)
            if listview:
                return listview

    # Path B: enumerate WorkerW windows (Win10/11 with custom shell/wallpaper)
    result = ctypes.c_int(0)

    def _enum_cb(hwnd, _lParam):
        classname = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, classname, 64)
        if classname.value == "WorkerW":
            shelldll = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
            if shelldll:
                lv = user32.FindWindowExW(shelldll, None, "SysListView32", None)
                if lv:
                    result.value = lv
                    return False  # stop enumeration
        return True

    proc = EnumWindowsProc(_enum_cb)
    user32.EnumWindows(proc, 0)
    return result.value or None


def hide_desktop_icons() -> bool:
    hwnd = _find_desktop_listview()
    if hwnd:
        user32.ShowWindow(hwnd, SW_HIDE)
        return True
    return False


def show_desktop_icons() -> bool:
    hwnd = _find_desktop_listview()
    if hwnd:
        user32.ShowWindow(hwnd, SW_SHOW)
        return True
    return False


def are_desktop_icons_visible() -> bool:
    hwnd = _find_desktop_listview()
    if hwnd:
        return bool(user32.IsWindowVisible(hwnd))
    return True
