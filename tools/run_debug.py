"""带线程栈转储的开发启动器（Windows / 零依赖）。

用法：
    python tools/run_debug.py                       # 同 python server.py，但可随时打栈
    python tools/run_debug.py --port 8799
    python tools/run_debug.py --watchdog 20         # 每 20 秒自动把全线程栈追加到 logs/stacks.txt
    python tools/run_debug.py --probe               # 只验证转储格式，然后退出

为什么需要它：`ThreadingHTTPServer` 每次请求一个线程，刷课/测验/扫描各一条
worker 线程。卡住时界面只剩轮询，看不出是哪一层在等。按 Ctrl+Break（不是
Ctrl+C）打一次栈，就能看到每条线程停在 chaoxing.py 的哪一行 —— 是在等
socket、在重试，还是死循环里空转。不需要装 py-spy。

Python 3.14 的 faulthandler 没有 register()，所以这里自己用 signal.signal
挂 SIGBREAK；handler 里调 faulthandler.dump_traceback() 是安全的。
"""

from __future__ import annotations

import argparse
import faulthandler
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLED = {"count": 0}


def _handler(signum, frame) -> None:  # noqa: ARG001 - signal 回调签名固定
    _HANDLED["count"] += 1
    sys.stderr.write(
        f"\n===== 线程栈转储 #{_HANDLED['count']} "
        f"({time_text()}) =====\n"
    )
    faulthandler.dump_traceback(file=sys.stderr)
    sys.stderr.write("===== 转储结束 =====\n")
    sys.stderr.flush()


def time_text() -> str:
    import time

    return time.strftime("%H:%M:%S")


def _install_signal() -> str:
    if not hasattr(signal, "SIGBREAK"):
        return "SIGBREAK 不可用（非 Windows 控制台），只能用 --watchdog"
    try:
        signal.signal(signal.SIGBREAK, _handler)
    except (OSError, ValueError) as exc:
        return f"注册 SIGBREAK 失败：{exc}"
    return "Ctrl+Break 已注册，按一次打一次栈"


def _install_watchdog(seconds: int, path: str) -> str:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    handle = open(path, "a", encoding="utf-8", buffering=1)
    handle.write(f"\n### 启动于 {time_text()}，每 {seconds}s 自动转储 ###\n")
    faulthandler.dump_traceback_later(seconds, repeat=True, file=handle)
    return f"watchdog 每 {seconds}s 追加到 {path}"


def main() -> int:
    parser = argparse.ArgumentParser(description="带 faulthandler 的服务启动器")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--watchdog", type=int, default=0, metavar="SECONDS")
    parser.add_argument("--stack-file", default=os.path.join("logs", "stacks.txt"))
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()

    print(f"[run_debug] {_install_signal()}", flush=True)
    if args.watchdog > 0:
        print(f"[run_debug] {_install_watchdog(args.watchdog, args.stack_file)}", flush=True)

    if args.probe:
        print("[run_debug] 直接调用一次 handler，下面是输出样例：", flush=True)
        _handler(signal.SIGBREAK, None)
        if args.watchdog > 0:
            import time

            print("[run_debug] 等 watchdog 触发一次…", flush=True)
            time.sleep(args.watchdog + 1.5)
            faulthandler.cancel_dump_traceback_later()
            with open(args.stack_file, encoding="utf-8") as handle:
                print(handle.read()[-1200:], flush=True)
        return 0

    import server

    print(f"[run_debug] 端口 {args.port}；卡住就按 Ctrl+Break 打栈", flush=True)
    try:
        server.run(args.port, not args.no_browser)
    finally:
        if args.watchdog > 0:
            faulthandler.cancel_dump_traceback_later()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
