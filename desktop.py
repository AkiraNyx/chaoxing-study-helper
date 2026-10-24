"""学习通刷课助手 · 桌面窗口入口。

用系统 WebView2 承载本地页面，关闭窗口即停止服务，不显示命令行窗口。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import webbrowser

import server

MUTEX_NAME = "ChaoxingHelperDesktopMutex"
_window = None
_mutex_handle = None


class WindowApi:
    """暴露给前端的最小化 / 最大化 / 关闭。"""

    def __init__(self):
        self._window = None

    def minimize(self) -> None:
        if self._window:
            self._window.minimize()

    def maximize(self) -> None:
        if not self._window:
            return
        try:
            # 无边框窗口在 WinForms 里默认会连任务栏一起盖住；把最大化边界
            # 限制到当前显示器的工作区，行为才和普通窗口的“最大化”一致。
            from System.Windows.Forms import Screen

            self._window.native.MaximizedBounds = Screen.FromControl(
                self._window.native
            ).WorkingArea
        except Exception:  # noqa: BLE001
            pass
        self._window.maximize()

    def restore(self) -> None:
        if self._window:
            self._window.restore()

    def close(self) -> None:
        if self._window:
            self._window.destroy()

    def open_external(self, url: str) -> None:
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            webbrowser.open(url)


def _alert(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "学习通刷课", 0x10)
    except Exception:  # noqa: BLE001
        pass


def _icon_path() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "icon.ico")
    return path if os.path.isfile(path) else ""


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


_user32 = ctypes.windll.user32
_user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
_user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
_user32.MonitorFromWindow.restype = ctypes.c_void_p
_user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
_user32.SetWindowPos.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_uint,
]


def _center_window(window) -> None:
    try:
        hwnd = ctypes.c_void_p(int(window.native.Handle.ToInt64()))
        rect = _RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        monitor = _user32.MonitorFromWindow(hwnd, 2)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        work = info.rcWork
        x = work.left + (work.right - work.left - width) // 2
        y = work.top + (work.bottom - work.top - height) // 2
        _user32.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    global _mutex_handle
    try:
        import webview
    except ImportError:
        _alert("缺少 pywebview，请先运行：python -m pip install pywebview")
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        _alert("学习通刷课已经在运行了。")
        return

    httpd, port = server.start_background(8765)
    hidden = "--test-hidden" in sys.argv
    test_mode = "--test" in sys.argv or hidden
    api = WindowApi()
    storage = os.path.join(
        os.environ.get("APPDATA") or os.path.expanduser("~"), "学习通刷课"
    )
    os.makedirs(storage, exist_ok=True)
    x = y = None
    screens = webview.screens
    if screens:
        screen = screens[0]
        x = max(0, (screen.width - 1280) // 2)
        y = max(0, (screen.height - 880) // 2)
    try:
        window = webview.create_window(
            "学习通刷课",
            f"http://127.0.0.1:{port}/",
            width=1280,
            height=880,
            x=x,
            y=y,
            min_size=(1024, 720),
            background_color="#F0EEE6",
            frameless=True,
            easy_drag=False,
            js_api=api,
            hidden=hidden,
        )
        api._window = window
        if test_mode:
            threading.Timer(4.0, window.destroy).start()
        threading.Timer(0.8, lambda: _center_window(window)).start()
        webview.start(
            icon=_icon_path() or None,
            private_mode=False,
            storage_path=storage,
        )
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        _alert(
            f"启动失败：{exc}\n\n"
            "请确认系统已安装 Microsoft Edge WebView2 运行时。"
        )
