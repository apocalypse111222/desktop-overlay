"""
Version info and GitHub update checker.
"""
import json
import threading
import urllib.request

APP_VERSION  = "1.1.0"
GITHUB_REPO  = "apocalypse111222/desktop-overlay"
_API_URL     = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse(v: str):
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except Exception:
        return (0,)


def check_for_updates(on_update_found):
    """
    Non-blocking. Calls on_update_found(new_version_str, release_url)
    on the calling thread (via the caller's after() or directly) if a
    newer release exists on GitHub.
    """
    threading.Thread(target=_worker, args=(on_update_found,), daemon=True).start()


def _worker(callback):
    try:
        req = urllib.request.Request(
            _API_URL,
            headers={"User-Agent": "DesktopOverlay-UpdateChecker"},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())

        tag     = data.get("tag_name", "")
        html_url = data.get("html_url", "")

        if tag and _parse(tag) > _parse(APP_VERSION):
            callback(tag.lstrip("v"), html_url)
    except Exception:
        pass
