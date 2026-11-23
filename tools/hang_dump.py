"""刷课卡住诊断：抓取实时状态、日志尾部和"是否真的在动"。

用法：
    python tools/hang_dump.py                 # 抓一次快照（默认 8765）
    python tools/hang_dump.py --watch 10      # 连抓 10 次，每 2 秒一次，看有没有变化
    python tools/hang_dump.py --port 8799

判读规则见文件末尾 VERDICTS，以及 README 里"刷课是怎么刷的"。
纯标准库，不依赖项目内任何模块。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

TAIL = 6
PORT_RANGE = range(8765, 8785)


def _utf8_console() -> None:
    """Windows 控制台默认 GBK，中文会花屏；管道输出时也强制 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _get(port: int, path: str, timeout: float = 8.0) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"__error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"__error": f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"}
    except json.JSONDecodeError:
        return {"__error": "响应不是 JSON"}


def _age(clock: str) -> float | None:
    """日志只有 HH:MM:SS，跨零点会得到负数：按 24 小时回绕。"""
    if not clock or clock.count(":") != 2:
        return None
    try:
        hour, minute, second = (int(part) for part in clock.split(":"))
    except ValueError:
        return None
    now = time.localtime()
    seconds = hour * 3600 + minute * 60 + second
    current = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
    delta = current - seconds
    return float(delta if delta >= 0 else delta + 86400)


def snapshot(port: int) -> dict:
    # /api/session 会触发一次 restore_login（前端启动时也走这个），
    # 先打它拿真实登录态；这次调用慢本身就是线索。
    session = _get(port, "/api/session", timeout=15.0)
    status = _get(port, "/api/status?since=0")
    quiz = _get(port, "/api/quiz/status?since=0")
    scan = _get(port, "/api/quiz/scan/status?since=0")
    saved = _get(port, "/api/session/saved")
    return {"session": session, "status": status, "quiz": quiz, "scan": scan, "saved": saved}


def find_ports(exclude: int) -> list[int]:
    """端口被占时软件会顺延到 8765–8784，卡住常伴随‘连错端口’。"""
    found = []
    for port in PORT_RANGE:
        if port == exclude:
            continue
        probe = _get(port, "/api/session/saved", timeout=1.5)
        if "__error" not in probe:
            found.append(port)
    return found


def describe(snap: dict) -> dict:
    status = snap["status"]
    quiz = snap["quiz"]
    scan = snap["scan"]
    session = snap["session"]
    logs = status.get("logs") or []
    last = logs[-1] if logs else {}
    age = _age(last.get("time", ""))
    return {
        "server": "OK" if "__error" not in status else status["__error"],
        "session_probe": "OK" if "__error" not in session else session["__error"],
        "logged_in": session.get("logged_in"),
        "running": status.get("running"),
        "phase": status.get("phase"),
        "percent": status.get("percent"),
        "current": status.get("current"),
        "stats": status.get("stats"),
        "summary": status.get("summary"),
        "log_seq": logs[-1]["seq"] if logs else 0,
        "log_age": age,
        "last_log": f"[{last.get('time', '')}] {last.get('level', '')}: {last.get('text', '')}"
        if last
        else "(无日志)",
        "quiz_running": quiz.get("running"),
        "quiz_percent": quiz.get("percent"),
        "scan_running": scan.get("running"),
        "scan_percent": scan.get("percent"),
        "saved_session": snap["saved"].get("saved"),
    }


def verdict(row: dict) -> list[str]:
    """把状态映射到 chaoxing.py / server.py 里具体的等待点。"""
    notes: list[str] = []
    if row["session_probe"] != "OK":
        notes.append(
            f"/api/session 超过 15s 没回（{row['session_probe']}）→ restore_login 在做网络校验，"
            "主线程以外的某个 HTTP 线程被 check_login/get_courses 拖住。"
        )
    if row["server"] != "OK":
        error = row["server"]
        if any(token in error for token in ("10061", "refused", "拒绝", "actively")):
            return [
                f"端口上没有服务在听（{error}）→ 不是卡住，是实例没起或端口不对。"
                "软件从 8765 起顺延，用 --port 指到界面地址栏那个端口。"
            ]
        return [
            f"/api/status 无响应（{error}）→ HTTP 线程被同步长任务占用，"
            "或 STATE.lock 被 worker 持有；看 server.py 里 _api_* 内联调用 _scan_course_quizzes。"
        ]
    if row["saved_session"] and not row["logged_in"]:
        notes.append(
            "磁盘上有保存的登录但当前进程未登录 → 可能开在两个端口上的两个实例，或会话已被清；"
            "先确认界面地址栏端口和这里 --port 一致。"
        )
    if row["running"]:
        age = row["log_age"]
        if age is not None and age >= 100:
            notes.append(
                f"正在运行但已 {int(age)}s 没有新日志 → 命中最可能的静默窗口："
                "(a) 视频上报间隔随机 30–90s + 进度只在每 10% 打点（chaoxing.py _study_video）；"
                "(b) 一个请求正卡在 3 次重试里，单请求最长 ~93s（Client.request）；"
                "(c) get_jobs 的 num 0..9 分页整章连打 10 次。"
            )
        elif age is not None and age >= 45:
            notes.append(f"已 {int(age)}s 无日志，可能刚进一个上报窗口，再等 60s 看是否继续。")
        if row["log_seq"] == 0:
            notes.append("运行中但没有日志 → 线程刚启动或 add_log 前就阻塞了。")
    else:
        notes.append("刷课线程没有在运行。")
    if row["quiz_running"]:
        notes.append(
            "quiz worker 仍在跑 → task_active() 为真，界面上点开始/登录都会 409「已有任务在运行」；"
            "常见于 AI 请求 90s 超时未返回（ai_client.py timeout=90）。"
        )
    if row["scan_running"]:
        notes.append("扫描线程仍在跑 → 8+8 线程池拉任务点，章节多的课程会持续数分钟。")
    if row["summary"]:
        summary = row["summary"]
        if summary.get("stopped"):
            notes.append("上次运行是被 stopped 收尾的（片尾待核验或手动停止）。")
        if summary.get("error"):
            notes.append(f"上次运行以错误收尾：{summary['error']}")
    return notes


def show(row: dict, width: int = 78) -> None:
    print("─" * width)
    print(f"服务状态      : {row['server']}")
    print(f"已登录        : {row['logged_in']}  (保存会话: {row['saved_session']})")
    print(f"刷课 running  : {row['running']}  phase={row['phase']}")
    print(f"进度          : {row['percent']}%  {row['current'] or ''}")
    print(f"统计          : {row['stats']}")
    print(f"最近日志      : seq={row['log_seq']}  距今 {row['log_age']}")
    print(f"              : {row['last_log']}")
    print(f"测验 worker   : running={row['quiz_running']} {row['quiz_percent']}%")
    print(f"扫描 worker   : running={row['scan_running']} {row['scan_percent']}%")
    if row["summary"]:
        print(f"上次小结      : {row['summary']}")
    for note in verdict(row):
        print(f"  → {note}")


def main() -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(description="刷课卡住诊断")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--watch", type=int, default=0, help="连抓 N 次，每次间隔 2 秒")
    args = parser.parse_args()

    if args.watch <= 1:
        row = describe(snapshot(args.port))
        show(row)
        if row["server"] != "OK":
            others = find_ports(args.port)
            if others:
                print(f"\n端口 {args.port} 没服务，但这些端口有实例：{others}")
                print("软件从 8765 起顺延选端口；改用 --port 上面那个再抓一次。")
            else:
                print(f"\n8765–8784 都没有实例：服务没起来，或端口被别的进程占了。")
        return 0 if row["server"] == "OK" else 1

    rows = []
    previous = None
    for index in range(args.watch):
        row = describe(snapshot(args.port))
        if index == 0 and row["server"] != "OK":
            show(row)
            return 1
        rows.append(row)
        if previous is None:
            print(f"\n[{index + 1}/{args.watch}] 起始")
            show(row)
        else:
            moved = (
                row["percent"] != previous["percent"]
                or row["log_seq"] != previous["log_seq"]
                or row["current"] != previous["current"]
                or row["running"] != previous["running"]
            )
            print(
                f"[{index + 1}/{args.watch}] "
                f"{'有变化' if moved else '无变化'}  "
                f"percent={row['percent']} seq={row['log_seq']} "
                f"running={row['running']} 日志距今={row['log_age']}"
            )
            if moved:
                print(f"        {row['last_log']}")
        previous = row
        if index + 1 < args.watch:
            time.sleep(2)

    frozen = all(
        row["percent"] == rows[0]["percent"] and row["log_seq"] == rows[0]["log_seq"]
        for row in rows
    )
    print("─" * 78)
    if frozen and rows[0]["running"]:
        print("结论：整段观察窗口内进度和日志都没有动 —— 真卡住，请按上面 → 提示定位。")
        for note in verdict(rows[-1])[1:]:
            print(f"  → {note}")
        return 2
    if frozen:
        print("结论：没有任务在跑（卡在‘点不动’而不是‘跑不动’），先看是不是 quiz/scan 残留占用。")
        return 3
    print("结论：仍在推进，只是日志稀疏；刷课没有卡住。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
