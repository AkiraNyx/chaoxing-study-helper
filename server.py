"""学习通刷课助手 · 本地 Web 服务。
启动后浏览器打开 http://127.0.0.1:<port>/ 即可使用。纯标准库实现，无需安装依赖。"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from types import SimpleNamespace
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import chaoxing
import session_store
from ai_client import AIClient, AIError


def _config_path() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    folder = os.path.join(base, "学习通刷课")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.json")


_CONFIG_LOCK = threading.Lock()


def load_config() -> dict:
    try:
        with open(_config_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config: dict) -> None:
    with _CONFIG_LOCK:
        path = _config_path()
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
        os.replace(temp, path)


def _cookie_header(client: chaoxing.Client) -> str:
    return "; ".join(f"{item.name}={item.value}" for item in client.jar)


def _save_login(client: chaoxing.Client) -> bool:
    try:
        return session_store.save(_cookie_header(client))
    except (OSError, ValueError):
        return False


def ai_from_config(config: dict | None = None) -> AIClient | None:
    data = (config or load_config()).get("ai") or {}
    key = (data.get("api_key") or "").strip()
    if not key:
        return None
    return AIClient(
        base_url=data.get("base_url") or "https://api.deepseek.com",
        api_key=key,
        model=data.get("model") or "deepseek-chat",
        reasoning_effort=data.get("reasoning_effort") or "",
    )


def _resource_dir() -> str:
    """开发时用脚本目录，PyInstaller onefile 运行时用解包目录。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _resource_dir()
WEB_DIR = os.path.join(BASE_DIR, "web")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}


class AppState:
    def __init__(self):
        self.lock = threading.RLock()
        self.client: Optional[chaoxing.Client] = None
        self.qr_client: Optional[chaoxing.Client] = None
        self.qr_created = 0.0
        self.courses: list[chaoxing.Course] = []
        self.running = False
        self.stop_event = threading.Event()
        self.logs: deque = deque(maxlen=3000)
        self.seq = 0
        self.percent = 0
        self.current = ""
        self.phase = "idle"
        self.stats = {"done": 0, "failed": 0, "skipped": 0}
        self.summary = None
        self.quiz_cache = {}
        self.quiz_done: set[str] = set()
        self.quiz_running = False
        self.quiz_logs: deque = deque(maxlen=800)
        self.quiz_seq = 0
        self.quiz_percent = 0.0
        self.quiz_current = ""
        self.quiz_summary = None
        self.scan_running = False
        self.scan_logs: deque = deque(maxlen=800)
        self.scan_seq = 0
        self.scan_percent = 0.0
        self.scan_current = ""
        self.scan_result = None
        self.restore_attempted = False
        self.exam_cache = {}
        self.exam_papers: dict[str, chaoxing.ExamPaper] = {}

    # -- 日志 / 状态 -------------------------------------------------------
    def add_quiz_log(self, level: str, text: str) -> None:
        with self.lock:
            self.quiz_seq += 1
            self.quiz_logs.append({
                "seq": self.quiz_seq,
                "level": level,
                "text": text,
                "time": time.strftime("%H:%M:%S"),
            })

    def set_quiz_progress(self, percent: float, text: str = "") -> None:
        with self.lock:
            self.quiz_percent = round(max(0.0, min(100.0, float(percent))), 2)
            if text:
                self.quiz_current = text

    def quiz_snapshot(self, since: int) -> dict:
        with self.lock:
            return {
                "ok": True,
                "running": self.quiz_running,
                "percent": self.quiz_percent,
                "current": self.quiz_current,
                "summary": self.quiz_summary,
                "logs": [item for item in self.quiz_logs if item["seq"] > since],
            }

    def add_scan_log(self, level: str, text: str) -> None:
        with self.lock:
            self.scan_seq += 1
            self.scan_logs.append({
                "seq": self.scan_seq,
                "level": level,
                "text": text,
                "time": time.strftime("%H:%M:%S"),
            })

    def set_scan_progress(self, percent: float, text: str = "") -> None:
        with self.lock:
            self.scan_percent = round(max(0.0, min(100.0, float(percent))), 2)
            if text:
                self.scan_current = text

    def scan_snapshot(self, since: int) -> dict:
        with self.lock:
            return {
                "ok": True,
                "running": self.scan_running,
                "percent": self.scan_percent,
                "current": self.scan_current,
                "result": self.scan_result,
                "logs": [item for item in self.scan_logs if item["seq"] > since],
            }

    def add_log(self, level: str, text: str) -> None:
        with self.lock:
            self.seq += 1
            self.logs.append({
                "seq": self.seq,
                "level": level,
                "text": text,
                "time": time.strftime("%H:%M:%S"),
            })

    def set_progress(self, percent: float, text: str) -> None:
        with self.lock:
            try:
                value = float(percent)
            except (TypeError, ValueError):
                value = 0.0
            self.percent = round(max(0.0, min(100.0, value)), 2)
            if text:
                self.current = text

    def finish(self, summary: Optional[dict] = None) -> None:
        with self.lock:
            self.running = False
            self.phase = "idle"
            if summary is not None:
                self.summary = summary
            self.stop_event.clear()

    def restore_login(self) -> None:
        if self.client is not None or self.restore_attempted:
            return
        self.restore_attempted = True
        cookie = session_store.load()
        if not cookie:
            return
        client = CLIENT_FACTORY()
        client.set_cookie_string(cookie)
        try:
            if not client.check_login():
                session_store.clear()
                return
            with self.lock:
                self.client = client
            self.add_log("ok", "已恢复上次登录状态")
            try:
                courses = client.get_courses()
                with self.lock:
                    self.courses = courses
            except (chaoxing.ChaoxingError, OSError):
                pass
        except (chaoxing.ChaoxingError, OSError):
            session_store.clear()

    def reset_session(self) -> None:
        with self.lock:
            self.courses = []
            self.qr_client = None
            self.logs.clear()
            self.seq = 0
            self.percent = 0
            self.current = ""
            self.phase = "idle"
            self.stats = {"done": 0, "failed": 0, "skipped": 0}
            self.summary = None
            self.exam_cache.clear()
            self.exam_papers.clear()

    def snapshot(self, since: int) -> dict:
        with self.lock:
            logs = [entry for entry in self.logs if entry["seq"] > since]
            return {
                "ok": True,
                "logged_in": bool(self.client and self.client.uid),
                "name": self.client.name if self.client else "",
                "running": self.running,
                "phase": self.phase,
                "percent": self.percent,
                "current": self.current,
                "stats": dict(self.stats),
                "logs": logs,
                "summary": self.summary,
            }


STATE = AppState()
CLIENT_FACTORY = chaoxing.Client
QR_TTL = 150


def diagnose(client: chaoxing.Client) -> dict:
    """只读检查：会话、课程、章节、任务点、视频信息，不产生任何学习记录。"""
    report: list[dict] = []
    passed = True

    def add(step: str, ok: bool, detail: str) -> None:
        nonlocal passed
        report.append({"step": step, "ok": bool(ok), "detail": detail})
        if not ok:
            passed = False

    try:
        logged = client.check_login()
    except chaoxing.ChaoxingError:
        logged = False
    add("会话校验", logged, f"UID {client.uid}" if logged else "Cookie 或会话已失效")
    if not logged:
        return {"passed": False, "report": report}

    try:
        courses = client.get_courses()
    except (chaoxing.ChaoxingError, OSError) as exc:
        add("读取课程", False, str(exc))
        return {"passed": False, "report": report}
    add("读取课程", bool(courses), f"{len(courses)} 门")
    if not courses:
        return {"passed": False, "report": report}

    course = courses[0]
    try:
        points = client.get_points(course)
    except (chaoxing.ChaoxingError, OSError) as exc:
        add(f"章节列表（{course.title}）", False, str(exc))
        return {"passed": False, "report": report}
    add(f"章节列表（{course.title}）", bool(points), f"{len(points)} 节")
    if not points:
        return {"passed": False, "report": report}

    point = next((item for item in points if not item.finished and not item.locked), points[0])
    try:
        jobs, info = client.get_jobs(course, point)
    except (chaoxing.ChaoxingError, OSError) as exc:
        add(f"任务点（{point.title}）", False, str(exc))
        return {"passed": False, "report": report}

    kinds: dict[str, int] = {}
    for job in jobs:
        kinds[job.type] = kinds.get(job.type, 0) + 1
    detail = "、".join(f"{name}×{count}" for name, count in kinds.items()) or "没有任务点"
    add(f"任务点（{point.title}）", bool(jobs), detail)

    work = next((job for job in jobs if job.type in {"workid", "work"}), None)
    if work:
        try:
            completed = client.is_work_completed(course, work, info)
            add("测验页读取", True, f"{point.title}：{'已完成' if completed else '未完成'}")
        except (chaoxing.ChaoxingError, OSError) as exc:
            add("测验页读取", False, str(exc))
    else:
        add("测验页读取", True, "当前任务点没有测验，跳过")

    ai = ai_from_config()
    if ai is None:
        add("AI 接口", True, "未配置（做题 AI 作答不可用）")
    else:
        try:
            models = ai.list_models()
            add("AI 接口", True, f"{ai.model}，可用模型 {len(models)} 个")
        except AIError as exc:
            add("AI 接口", False, str(exc))

    try:
        import font_decoder
        available = font_decoder.TTFont is not None
        count = len(font_decoder._table())
        add("字体解码", available, f"{count} 条字形映射" if available else "fontTools 未安装")
    except Exception as exc:  # noqa: BLE001
        add("字体解码", False, str(exc))

    video = next((job for job in jobs if job.type == "video" and job.objectid), None)
    if video:
        status = client.video_status(video.objectid)
        ok = status.get("status") == "success" and bool(status.get("dtoken"))
        if ok:
            add("视频信息", True, f"{video.name or video.objectid}：{status.get('duration')} 秒")
        else:
            add("视频信息", False, "无法读取视频信息（可能已下架或需要弹题）")

    return {"passed": passed, "report": report}


def _scan_course_quizzes(client: chaoxing.Client, course: chaoxing.Course,
                         course_index: int, progress=None, log=None) -> list[dict]:
    def report(percent: float, text: str) -> None:
        if progress:
            progress(percent, text)

    def note(level: str, text: str) -> None:
        if log:
            log(level, text)

    report(4, "读取章节列表")
    points = client.get_points(course)
    targets = list(points)
    note("info", f"{course.title}：共 {len(points)} 节")
    if not targets:
        return []
    cookie = "; ".join(f"{item.name}={item.value}" for item in client.jar)
    bases = {
        "mooc1": client.mooc1,
        "mooc2": client.mooc2,
        "passport": client.passport,
        "sso": client.sso,
    }
    local = threading.local()

    def worker_client() -> chaoxing.Client:
        worker = getattr(local, "client", None)
        if worker is None:
            worker = chaoxing.Client(**bases)
            worker.set_cookie_string(cookie)
            local.client = worker
        return worker

    def fetch(point: chaoxing.Point):
        try:
            jobs, info = worker_client().get_jobs(course, point)
            return jobs, info
        except (chaoxing.ChaoxingError, OSError):
            return [], {}

    records = []
    report(12, f"扫描任务点（{len(targets)} 节）")
    note("info", "开始扫描任务点…")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for point, (jobs, info) in zip(targets, pool.map(fetch, targets)):
            for job in jobs:
                if job.type in {"workid", "work"}:
                    records.append((point, job, info))
    report(55, f"预检测验完成状态（{len(records)} 条）")
    note("info", f"发现 {len(records)} 条测验，开始预检完成状态…")

    def is_done(record) -> bool:
        point, job, info = record
        if job.is_passed or point.finished:
            return True
        try:
            return worker_client().is_work_completed(course, job, info)
        except (chaoxing.ChaoxingError, OSError):
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        flags = list(pool.map(is_done, records))

    report(96, "整理扫描结果")
    note("info", "扫描完成，整理结果…")

    items = []
    for (point, job, _info), passed in zip(records, flags):
        items.append({
            "course": course.title,
            "chapter": point.title,
            "name": job.name or point.title,
            "course_index": course_index,
            "point_id": point.id,
            "jobid": job.jobid,
            "passed": passed or job.jobid in STATE.quiz_done,
        })
    items.sort(key=lambda item: item.get("passed", False))
    return items


def _normalize_quiz_answer(question, raw) -> str:
    if isinstance(raw, list):
        raw = "".join(str(item) for item in raw)
    raw = str(raw).strip()
    if question.type == "judgement":
        return "true" if raw.lower() in {"true", "t", "对", "正确", "是"} else "false"
    if question.type in {"single", "multiple"}:
        letters = "".join(sorted(set(re.findall(r"[A-Za-z]", raw.upper()))))
        return letters[:1] if question.type == "single" else letters
    return raw


def _ask_question(ai: AIClient, question) -> str:
    reply = ai.chat([
        {
            "role": "system",
            "content": (
                "你是学习通章节测验答题助手。只输出最终答案本身，不要解释。"
                "单选输出选项字母；多选输出按字母排序的字母串；判断输出 true/false。"
                "填空和简答输出文本。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"id": question.id, "type": question.type, "title": question.title,
                 "options": question.options},
                ensure_ascii=False,
            ),
        },
    ], temperature=0)
    return _normalize_quiz_answer(question, reply)


def _exam_payload(paper: chaoxing.ExamPaper) -> dict:
    return {
        "exam_id": paper.exam_id,
        "title": paper.title,
        "remain_time": paper.enc_remain_time or paper.remain_time,
        "fetched_at": paper.fetched_at,
        "saved": sorted(paper.saved_ids),
        "questions": [
            {
                "id": question.id,
                "type": question.type,
                "type_code": question.type_code,
                "title": question.title,
                "options": [{"key": key, "text": text} for key, text in question.options],
                "blanks": question.blanks,
                "answer": question.answer,
            }
            for question in paper.questions
        ],
    }


def _find_exam(client: chaoxing.Client, course: chaoxing.Course, exam_id: str) -> chaoxing.Exam:
    with STATE.lock:
        cached = STATE.exam_cache.get(course.course_id)
    if cached and time.time() - cached[0] < 300:
        exams = cached[1]
    else:
        exams = client.get_exams(course)
        with STATE.lock:
            STATE.exam_cache[course.course_id] = (time.time(), exams)
    exam = next((item for item in exams if item.exam_id == str(exam_id)), None)
    if exam is None:
        raise chaoxing.ExamError("考试不存在或列表已变化，请刷新考试列表")
    return exam


def _load_quiz(client: chaoxing.Client, course_index: int, point_id: str, jobid: str):
    with STATE.lock:
        courses = list(STATE.courses)
    course = courses[int(course_index)]
    points = client.get_points(course)
    point = next((item for item in points if item.id == point_id), None)
    if point is None:
        raise chaoxing.ChaoxingError("章节已变化，请刷新测验列表")
    jobs, info = client.get_jobs(course, point)
    job = next((item for item in jobs if item.jobid == jobid), None)
    if job is None:
        raise chaoxing.ChaoxingError("测验已变化，请刷新测验列表")
    return course, client.get_quiz(course, job, info)


def _quiz_worker(client: chaoxing.Client, course: chaoxing.Course, point_id: str,
                 jobid: str, ai: AIClient) -> None:
    state = STATE
    summary: dict = {}

    def log(level: str, text: str) -> None:
        state.add_quiz_log(level, text)

    try:
        state.set_quiz_progress(3, "读取章节")
        log("info", f"读取章节：{course.title}")
        points = client.get_points(course)
        point = next((item for item in points if item.id == point_id), None)
        if point is None:
            raise chaoxing.ChaoxingError("章节已变化，请刷新测验列表")
        state.set_quiz_progress(10, "读取任务点")
        jobs, info = client.get_jobs(course, point)
        job = next((item for item in jobs if item.jobid == jobid), None)
        if job is None:
            raise chaoxing.ChaoxingError("测验已变化，请刷新测验列表")
        state.set_quiz_progress(18, "打开测验页")
        log("info", "打开测验页…")
        quiz = client.get_quiz(course, job, info)
        if quiz.encrypted:
            if quiz.decoded:
                log("info", "检测到字体加密，已按字形映射解码")
            else:
                log("warn", "检测到字体加密，但字体解码失败，题目可能仍是乱码")
        log("ok", f"读取到 {len(quiz.questions)} 道题")
        state.set_quiz_progress(22, "AI 作答中")
        answers: dict[str, str] = {}
        total = len(quiz.questions)
        for index, question in enumerate(quiz.questions, start=1):
            log("info", f"作答 {index}/{total}：{question.title[:40]}")
            try:
                answers[question.id] = _ask_question(ai, question)
            except AIError as exc:
                log("error", f"  第 {index} 题失败：{exc}")
                answers[question.id] = ""
            log("dim", f"  → {answers[question.id] or '未作答'}")
            state.set_quiz_progress(22 + index / max(1, total) * 68, f"作答 {index}/{total}")
        missing = sum(1 for question in quiz.questions if not answers.get(question.id))
        state.set_quiz_progress(94, "提交答案")
        log("info", "提交答案…")
        save_only = missing > 0
        ok, message = client.submit_quiz(quiz, answers, save_only=save_only)
        summary = {
            "submitted": bool(ok and not save_only),
            "saved": bool(ok and save_only),
            "message": message,
            "count": total,
            "missing": missing,
        }
        if ok:
            log("ok", f"{'已保存' if save_only else '已提交'}：{total} 题" + (f"，{missing} 题未答" if missing else ""))
        else:
            log("error", f"提交失败：{message}")
        state.set_quiz_progress(100, "已完成")
    except (chaoxing.ChaoxingError, AIError, OSError) as exc:
        log("error", str(exc))
        summary = {"submitted": False, "saved": False, "message": str(exc), "count": 0, "missing": 0}
        state.set_quiz_progress(100, "失败")
    finally:
        with state.lock:
            state.quiz_running = False
            state.quiz_summary = summary
            state.quiz_cache.pop(course.course_id, None)


def _scan_worker(client: chaoxing.Client, entries: list[tuple[int, chaoxing.Course]], force: bool) -> None:
    state = STATE
    all_items: list[dict] = []
    try:
        state.add_scan_log("info", "开始扫描章节测验…")
        for position, (course_index, course) in enumerate(entries):
            cached = state.quiz_cache.get(course.course_id)
            if cached and not force and time.time() - cached[0] < 300:
                items = cached[1]
                state.add_scan_log("dim", f"{course.title}：使用缓存")
            else:
                def progress(percent: float, text: str, pos=position, title=course.title) -> None:
                    overall = ((pos + percent / 100) / len(entries)) * 100
                    state.set_scan_progress(overall, f"{title} · {text}")

                items = _scan_course_quizzes(
                    client, course, course_index, progress=progress, log=state.add_scan_log
                )
                state.quiz_cache[course.course_id] = (time.time(), items)
            all_items.extend(items)
        state.scan_result = {"quizzes": all_items}
        state.add_scan_log("ok", f"扫描完成：共 {len(all_items)} 条测验")
        state.set_scan_progress(100, "扫描完成")
    except (chaoxing.ChaoxingError, OSError) as exc:
        state.add_scan_log("error", str(exc))
        state.scan_result = {"quizzes": all_items, "error": str(exc)}
        state.set_scan_progress(100, "扫描失败")
    except Exception as exc:  # noqa: BLE001
        state.add_scan_log("error", f"{type(exc).__name__}: {exc}")
        state.scan_result = {"quizzes": all_items, "error": str(exc)}
        state.set_scan_progress(100, "扫描失败")
    finally:
        with state.lock:
            state.scan_running = False


def _brush_worker(client: chaoxing.Client, courses: list[chaoxing.Course], options: chaoxing.Options) -> None:
    def emit(level: str, text: str) -> None:
        STATE.add_log(level, text)

    def progress(percent: int, text: str) -> None:
        STATE.set_progress(percent, text)

    def on_stats(stats: dict) -> None:
        with STATE.lock:
            STATE.stats = {
                "done": stats.get("done", 0),
                "failed": stats.get("failed", 0),
                "skipped": stats.get("skipped", 0),
            }

    started = time.time()
    runner = chaoxing.Runner(client, options, STATE.stop_event, emit, progress, on_stats)

    def partial(error: Optional[str] = None) -> dict:
        summary = {
            "done": runner.stats["done"],
            "failed": runner.stats["failed"],
            "skipped": runner.stats["skipped"],
            "chapters": runner.stats["chapters"],
            "elapsed": round(time.time() - started),
            "stopped": STATE.stop_event.is_set(),
        }
        if error:
            summary["error"] = error
        return summary

    try:
        summary = runner.run(courses)
    except chaoxing.NotLoggedIn:
        emit("error", "登录已失效，请重新登录")
        summary = partial("登录已失效")
    except Exception as exc:  # noqa: BLE001 - 兜底，保证线程状态复位        emit("error", f"运行异常：{exc}")
        summary = partial(str(exc))
    finally:
        with STATE.lock:
            STATE.stats = {
                "done": summary.get("done", 0),
                "failed": summary.get("failed", 0),
                "skipped": summary.get("skipped", 0),
            }
        STATE.finish(summary)


class Handler(BaseHTTPRequestHandler):
    server_version = "ChaoxingHelper/1.1.3"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静默
        return

    # -- 响应工具 ----------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str,
              extra: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _reject(self, message: str, status: int = 403) -> None:
        """拒绝请求时直接断开连接，避免未读的请求体污染下一个请求。"""
        self.close_connection = True
        self._error(message, status)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    # -- 请求校验 ----------------------------------------------------------
    def _host_allowed(self) -> bool:
        """只接受回环地址，挡住 DNS rebinding。"""
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return True
        if host.startswith("["):
            return host.startswith("[::1]")
        return host.rsplit(":", 1)[0] in {"127.0.0.1", "localhost"}

    def _json_request_allowed(self) -> bool:
        """只接受本机页面发来的 JSON，挡住跨站表单提交。"""
        origin = (self.headers.get("Origin") or "").strip().rstrip("/")
        if origin:
            port = self.server.server_port
            allowed = {
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
                f"http://[::1]:{port}",
            }
            if origin not in allowed:
                return False
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return content_type in {"", "application/json"}

    def _drop_session(self, message: str = "登录已失效，请重新登录") -> None:
        """会话失效时清掉内存里的客户端，避免前端刷新后仍被判为已登录。"""
        with STATE.lock:
            STATE.client = None
            STATE.restore_attempted = True
            if STATE.running:
                STATE.stop_event.set()
        self._error(message, 401)

    # -- 静态文件 ----------------------------------------------------------
    def _static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        relative = urllib.parse.unquote(path).lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, relative))
        if not full.startswith(os.path.normpath(WEB_DIR) + os.sep):
            return self._error("Not found", 404)
        if not os.path.isfile(full):
            return self._error("Not found", 404)
        with open(full, "rb") as handle:
            body = handle.read()
        content_type = MIME.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        self._send(200, body, content_type)

    # -- 路由 --------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if not self._host_allowed():
            return self._reject("非法的 Host")
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return self._static(parsed.path)
        try:
            self._api_get(parsed)
        except chaoxing.NotLoggedIn:
            self._drop_session()
        except chaoxing.ChaoxingError as exc:
            self._error(str(exc), 400)
        except Exception as exc:  # noqa: BLE001
            self._error(f"服务器错误：{exc}", 500)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_allowed():
            return self._reject("非法的 Host")
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return self._error("Not found", 404)
        if not self._json_request_allowed():
            return self._reject("只接受本机页面的 JSON 请求")
        try:
            self._api_post(parsed)
        except chaoxing.LoginError as exc:
            self._error(str(exc), 400)
        except chaoxing.NotLoggedIn:
            self._drop_session()
        except chaoxing.ChaoxingError as exc:
            self._error(str(exc), 400)
        except Exception as exc:  # noqa: BLE001
            self._error(f"服务器错误：{exc}", 500)

    def _api_get(self, parsed) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        route = parsed.path

        if route == "/api/session":
            STATE.restore_login()
            with STATE.lock:
                return self._json({
                    "ok": True,
                    "logged_in": bool(STATE.client and STATE.client.uid),
                    "name": STATE.client.name if STATE.client else "",
                })

        if route == "/api/session/saved":
            return self._json({
                "ok": True,
                "saved": bool(session_store.load()),
                "logged_in": bool(STATE.client and STATE.client.uid),
            })

        if route == "/api/status":
            try:
                since = int((query.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            return self._json(STATE.snapshot(since))

        if route == "/api/qrcode/status":
            with STATE.lock:
                qr_client = STATE.qr_client
                created = STATE.qr_created
            if qr_client is None:
                return self._json({"ok": True, "state": "expired"})
            if time.time() - created > QR_TTL:
                with STATE.lock:
                    STATE.qr_client = None
                return self._json({"ok": True, "state": "expired"})
            result = qr_client.qr_poll()
            if result.get("state") == "confirmed":
                with STATE.lock:
                    STATE.client = qr_client
                    STATE.qr_client = None
                STATE.reset_session()
                remember = (query.get("remember") or ["1"])[0] == "1"
                if remember:
                    _save_login(qr_client)
                STATE.add_log("ok", f"扫码登录成功：{result.get('name') or qr_client.uid}")
            elif result.get("state") in ("cancelled", "error"):
                with STATE.lock:
                    STATE.qr_client = None
            return self._json({"ok": True, **result})

        if route == "/api/ai/config":
            data = load_config().get("ai") or {}
            return self._json({
                "ok": True,
                "base_url": data.get("base_url") or "https://api.deepseek.com",
                "model": data.get("model") or "deepseek-chat",
                "reasoning_effort": data.get("reasoning_effort") or "",
                "has_key": bool(data.get("api_key")),
            })

        if route == "/api/ai/models":
            client = ai_from_config()
            if client is None:
                return self._error("请先保存 API Key", 400)
            try:
                models = client.list_models()
            except AIError as exc:
                return self._error(str(exc), 400)
            return self._json({"ok": True, "models": models})

        if route == "/api/quiz/scan/status":
            try:
                since = int((query.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            return self._json(STATE.scan_snapshot(since))

        if route == "/api/quiz/status":
            try:
                since = int((query.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            return self._json(STATE.quiz_snapshot(since))

        if route == "/api/quiz/detail":
            client = self._require_client()
            try:
                index = int((query.get("course_index") or ["-1"])[0])
                point_id = (query.get("point_id") or [""])[0]
                jobid = (query.get("jobid") or [""])[0]
                _course, quiz = _load_quiz(client, index, point_id, jobid)
            except (chaoxing.ChaoxingError, ValueError, IndexError) as exc:
                return self._error(str(exc), 400)
            if quiz.completed:
                with STATE.lock:
                    STATE.quiz_done.add(jobid)
                    STATE.quiz_cache.pop(_course.course_id, None)
            return self._json({
                "ok": True,
                "completed": quiz.completed,
                "score": quiz.score,
                "encrypted": quiz.encrypted,
                "questions": [
                    {"id": q.id, "type": q.type, "title": q.title, "options": q.options}
                    for q in quiz.questions
                ],
            })

        if route == "/api/exams":
            client = self._require_client()
            with STATE.lock:
                if STATE.running:
                    return self._error("正在刷课，请先停止", 409)
                all_courses = list(STATE.courses)
            raw = (query.get("indices") or [""])[0]
            indices = []
            for part in raw.split(","):
                try:
                    index = int(part)
                except ValueError:
                    continue
                if 0 <= index < len(all_courses):
                    indices.append(index)
            if not indices:
                return self._error("请先在刷课页勾选要读取的课程", 400)
            refresh = (query.get("refresh") or ["0"])[0] == "1"
            items = []
            errors = []
            for index in indices:
                course = all_courses[index]
                with STATE.lock:
                    cached = STATE.exam_cache.get(course.course_id)
                if cached and not refresh and time.time() - cached[0] < 300:
                    exams = cached[1]
                else:
                    try:
                        exams = client.get_exams(course)
                    except (chaoxing.ChaoxingError, OSError) as exc:
                        errors.append(f"{course.title}：{exc}")
                        continue
                    with STATE.lock:
                        STATE.exam_cache[course.course_id] = (time.time(), exams)
                for exam in exams:
                    items.append({
                        "course": course.title,
                        "course_index": index,
                        "exam_id": exam.exam_id,
                        "title": exam.title,
                        "status": exam.status,
                        "expire": exam.expire,
                        "completed": "已完成" in exam.status,
                    })
            items.sort(key=lambda item: (item["completed"], item["course"], item["title"]))
            return self._json({"ok": True, "exams": items, "errors": errors})

        if route == "/api/exam/cover":
            client = self._require_client()
            try:
                index = int((query.get("course_index") or ["-1"])[0])
            except ValueError:
                index = -1
            with STATE.lock:
                if not (0 <= index < len(STATE.courses)):
                    return self._error("课程不存在，请刷新课程列表", 404)
                course = STATE.courses[index]
            exam_id = (query.get("exam_id") or [""])[0]
            try:
                exam = _find_exam(client, course, exam_id)
                cover = client.exam_cover(course, exam)
            except chaoxing.ExamCompleted as exc:
                return self._json({"ok": True, "completed": True, "message": str(exc)})
            except (chaoxing.ChaoxingError, OSError) as exc:
                return self._error(str(exc), 400)
            return self._json({
                "ok": True,
                "completed": False,
                "title": cover["title"],
                "need_face": cover["need_face"],
                "need_captcha": cover["need_captcha"],
                "need_code": cover["need_code"],
            })

        if route == "/api/quizzes":
            client = self._require_client()
            with STATE.lock:
                if STATE.running:
                    return self._error("正在刷课，请先停止", 409)
                all_courses = list(STATE.courses)
            raw = (query.get("indices") or [""])[0]
            courses = []
            for part in raw.split(","):
                try:
                    index = int(part)
                except ValueError:
                    continue
                if 0 <= index < len(all_courses):
                    courses.append(all_courses[index])
            if not courses:
                return self._error("请先在刷课页勾选要读取的课程", 400)
            items = []
            refresh = (query.get("refresh") or ["0"])[0] == "1"
            for course in courses:
                cached = STATE.quiz_cache.get(course.course_id)
                if cached and not refresh and time.time() - cached[0] < 300:
                    items.extend(cached[1])
                    continue
                try:
                    course_items = _scan_course_quizzes(client, course, all_courses.index(course))
                except (chaoxing.ChaoxingError, OSError):
                    continue
                STATE.quiz_cache[course.course_id] = (time.time(), course_items)
                items.extend(course_items)
            return self._json({"ok": True, "quizzes": items})

        if route == "/api/courses":
            client = self._require_client()
            courses = client.get_courses()
            with STATE.lock:
                STATE.courses = courses
            return self._json({
                "ok": True,
                "courses": [
                    {"index": index, "title": course.title, "teacher": course.teacher,
                     "desc": course.desc}
                    for index, course in enumerate(courses)
                ],
            })

        if route == "/api/chapters":
            client = self._require_client()
            if STATE.running:
                return self._error("正在刷课，请先停止", 409)
            try:
                index = int((query.get("index") or ["-1"])[0])
            except ValueError:
                index = -1
            with STATE.lock:
                if not (0 <= index < len(STATE.courses)):
                    return self._error("课程不存在，请刷新课程列表", 404)
                course = STATE.courses[index]
            points = []
            units = done_tasks = total_tasks = 0
            if hasattr(client, "get_structure"):
                units, points, done_tasks, total_tasks = client.get_structure(course)
            else:
                points = client.get_points(course)
            return self._json({
                "ok": True,
                "course": course.title,
                "units": units,
                "done_tasks": done_tasks,
                "total_tasks": total_tasks,
                "chapters": [
                    {"title": point.title, "finished": point.finished,
                     "locked": point.locked, "jobs": point.job_count}
                    for point in points
                ],
            })

        return self._error("Not found", 404)

    def _api_post(self, parsed) -> None:
        route = parsed.path
        payload = self._read_json()

        if route == "/api/session/auto":
            enabled = bool(payload.get("enabled"))
            if enabled:
                client = self._require_client()
                saved = _save_login(client)
                if not saved:
                    return self._error("保存登录信息失败，请检查磁盘权限", 400)
                with STATE.lock:
                    STATE.restore_attempted = False
            else:
                session_store.clear()
                with STATE.lock:
                    STATE.restore_attempted = True
                saved = False
            return self._json({"ok": True, "saved": saved})

        if route == "/api/session/clear":
            session_store.clear()
            with STATE.lock:
                STATE.restore_attempted = True
            return self._json({"ok": True, "saved": False})

        if route == "/api/login":
            with STATE.lock:
                if STATE.running:
                    return self._error("正在刷课，请先停止", 409)
            mode = payload.get("mode") or "password"
            client = CLIENT_FACTORY()
            if mode == "cookie":
                cookie = (payload.get("cookie") or "").strip()
                if not cookie:
                    return self._error("请先粘贴 Cookie")
                client.set_cookie_string(cookie)
                if not client.check_login():
                    return self._error("Cookie 无效或已过期，请重新从浏览器复制")
            else:
                username = (payload.get("username") or "").strip()
                password = payload.get("password") or ""
                if not username or not password:
                    return self._error("请填写账号和密码")
                client.login_with_password(username, password)
            remember = bool(payload.get("remember", True))
            with STATE.lock:
                if STATE.running:
                    return self._error("正在刷课，请先停止", 409)
                STATE.client = client
                if remember:
                    saved = _save_login(client)
                else:
                    session_store.clear()
                    saved = False
            STATE.reset_session()
            STATE.add_log("ok", f"登录成功：{client.name or client.uid}")
            return self._json({"ok": True, "name": client.name or client.uid, "remembered": saved})

        if route == "/api/ai/config":
            config = load_config()
            ai = config.setdefault("ai", {})
            ai["base_url"] = (payload.get("base_url") or "https://api.deepseek.com").strip()
            ai["model"] = (payload.get("model") or "deepseek-chat").strip()
            ai["reasoning_effort"] = (payload.get("reasoning_effort") or "").strip()
            key = (payload.get("api_key") or "").strip()
            if key:
                ai["api_key"] = key
            save_config(config)
            return self._json({"ok": True, "has_key": bool(ai.get("api_key")), "model": ai["model"]})

        if route == "/api/ai/test":
            client = ai_from_config()
            if client is None:
                return self._error("请先填写并保存 API Key", 400)
            try:
                reply = client.test()
            except AIError as exc:
                return self._error(str(exc), 400)
            return self._json({"ok": True, "reply": reply, "model": client.model})

        if route == "/api/quiz/solve":
            client = self._require_client()
            with STATE.lock:
                if STATE.running:
                    return self._error("正在刷课，请先停止", 409)
                if STATE.quiz_running:
                    return self._error("已有测验正在作答", 409)
                courses = list(STATE.courses)
            try:
                index = int(payload.get("course_index"))
                course = courses[index]
            except (TypeError, ValueError, IndexError):
                return self._error("课程索引无效，请刷新测验列表", 400)
            point_id = str(payload.get("point_id") or "")
            jobid = str(payload.get("jobid") or "")
            ai = ai_from_config()
            if ai is None:
                return self._error("请先在 AI 接口页保存 API Key", 400)
            with STATE.lock:
                STATE.quiz_running = True
                STATE.quiz_logs.clear()
                STATE.quiz_seq = 0
                STATE.quiz_percent = 0
                STATE.quiz_current = "准备"
                STATE.quiz_summary = None
                STATE.quiz_cache.pop(course.course_id, None)
            threading.Thread(
                target=_quiz_worker,
                args=(client, course, point_id, jobid, ai),
                daemon=True,
                name="quiz-worker",
            ).start()
            return self._json({"ok": True})

        if route == "/api/quiz/ai":
            ai = ai_from_config()
            if ai is None:
                return self._error("请先在 AI 接口页保存 API Key", 400)
            answers = {}
            for raw in payload.get("questions") or []:
                question = SimpleNamespace(
                    id=str(raw.get("id") or ""),
                    type=str(raw.get("type") or "unknown"),
                    title=str(raw.get("title") or ""),
                    options=list(raw.get("options") or []),
                )
                try:
                    answers[question.id] = _ask_question(ai, question)
                except AIError as exc:
                    return self._error(str(exc), 400)
            return self._json({"ok": True, "answers": answers})

        if route == "/api/quiz/submit":
            client = self._require_client()
            try:
                index = int(payload.get("course_index"))
                _course, quiz = _load_quiz(
                    client,
                    index,
                    str(payload.get("point_id") or ""),
                    str(payload.get("jobid") or ""),
                )
            except (chaoxing.ChaoxingError, ValueError, IndexError) as exc:
                return self._error(str(exc), 400)
            answers = {str(key): str(value) for key, value in (payload.get("answers") or {}).items()}
            missing = sum(1 for q in quiz.questions if not answers.get(q.id))
            save_only = bool(payload.get("save_only")) or missing > 0
            try:
                ok, message = client.submit_quiz(quiz, answers, save_only=save_only)
            except chaoxing.ChaoxingError as exc:
                return self._error(str(exc), 400)
            submitted = bool(ok and not save_only)
            saved = bool(ok and save_only)
            if submitted:
                with STATE.lock:
                    STATE.quiz_done.add(str(payload.get("jobid") or ""))
                    STATE.quiz_cache.pop(_course.course_id, None)
            return self._json({
                "ok": True,
                "submitted": submitted,
                "saved": saved,
                "message": message,
                "count": len(quiz.questions),
                "missing": missing,
            })

        if route == "/api/exam/start":
            client = self._require_client()
            with STATE.lock:
                if STATE.running:
                    return self._error("正在刷课，请先停止", 409)
                courses = list(STATE.courses)
            try:
                index = int(payload.get("course_index"))
                course = courses[index]
            except (TypeError, ValueError, IndexError):
                return self._error("课程索引无效，请刷新考试列表", 400)
            exam_id = str(payload.get("exam_id") or "")
            code = str(payload.get("code") or "")
            with STATE.lock:
                existing = STATE.exam_papers.get(exam_id)
            if existing is not None:
                return self._json({"ok": True, "paper": _exam_payload(existing), "resumed": True})
            try:
                exam = _find_exam(client, course, exam_id)
                paper = client.start_exam(course, exam, code)
            except (chaoxing.ChaoxingError, OSError) as exc:
                return self._error(str(exc), 400)
            with STATE.lock:
                STATE.exam_papers[paper.exam_id] = paper
            return self._json({"ok": True, "paper": _exam_payload(paper), "resumed": False})

        if route == "/api/exam/answer":
            client = self._require_client()
            exam_id = str(payload.get("exam_id") or "")
            question_id = str(payload.get("question_id") or "")
            with STATE.lock:
                paper = STATE.exam_papers.get(exam_id)
            if paper is None:
                return self._error("考试会话已失效，请重新进入考试", 404)
            question = next((item for item in paper.questions if item.id == question_id), None)
            if question is None:
                return self._error("题目已变化，请重新进入考试", 400)
            index = next(i for i, item in enumerate(paper.questions) if item.id == question_id)
            answer = payload.get("answer")
            if answer is None:
                answer = ""
            try:
                client.save_exam_answer(paper, index, question, answer)
            except (chaoxing.ChaoxingError, OSError) as exc:
                return self._error(str(exc), 400)
            return self._json({
                "ok": True,
                "saved": True,
                "saved_ids": sorted(paper.saved_ids),
            })

        if route == "/api/exam/submit":
            client = self._require_client()
            exam_id = str(payload.get("exam_id") or "")
            with STATE.lock:
                paper = STATE.exam_papers.pop(exam_id, None)
            if paper is None:
                return self._error("考试会话已失效，请重新进入考试", 404)
            answers = payload.get("answers") or {}
            if not isinstance(answers, dict):
                with STATE.lock:
                    STATE.exam_papers[exam_id] = paper
                return self._error("答案格式不正确", 400)
            try:
                if payload.get("pre_saved"):
                    result = client.finalize_exam(paper)
                else:
                    result = client.submit_exam(paper, answers)
            except (chaoxing.ChaoxingError, OSError) as exc:
                with STATE.lock:
                    STATE.exam_papers[exam_id] = paper
                return self._error(str(exc), 400)
            with STATE.lock:
                STATE.exam_cache.pop(paper.course_id, None)
            return self._json({"ok": True, **result})

        if route == "/api/quizzes/scan":
            client = self._require_client()
            with STATE.lock:
                if STATE.running or STATE.scan_running:
                    return self._error("已有任务在运行，请稍候", 409)
                all_courses = list(STATE.courses)
            raw = payload.get("indices") or []
            if isinstance(raw, str):
                raw = [item for item in raw.split(",") if item]
            entries = []
            for part in raw:
                try:
                    index = int(part)
                except (TypeError, ValueError):
                    continue
                if 0 <= index < len(all_courses):
                    entries.append((index, all_courses[index]))
            if not entries:
                return self._error("请先在刷课页勾选要读取的课程", 400)
            with STATE.lock:
                STATE.scan_running = True
                STATE.scan_logs.clear()
                STATE.scan_seq = 0
                STATE.scan_percent = 0
                STATE.scan_current = "准备"
                STATE.scan_result = None
            threading.Thread(
                target=_scan_worker,
                args=(client, entries, bool(payload.get("force"))),
                daemon=True,
                name="quiz-scan",
            ).start()
            return self._json({"ok": True})

        if route == "/api/qrcode/start":
            with STATE.lock:
                if STATE.running:
                    return self._error("正在刷课，请先停止", 409)
            client = CLIENT_FACTORY()
            image = client.qr_start()
            with STATE.lock:
                STATE.qr_client = client
                STATE.qr_created = time.time()
            if image[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            elif image[:2] == b"\xff\xd8":
                mime = "image/jpeg"
            else:
                mime = "image/png"
            return self._json({
                "ok": True,
                "image": base64.b64encode(image).decode("ascii"),
                "mime": mime,
                "expires_in": QR_TTL,
            })

        if route == "/api/qrcode/cancel":
            with STATE.lock:
                STATE.qr_client = None
            return self._json({"ok": True})

        if route == "/api/logout":
            with STATE.lock:
                if STATE.running:
                    return self._error("正在刷课，请先停止", 409)
                STATE.client = None
                STATE.restore_attempted = True
            session_store.clear()
            STATE.reset_session()
            return self._json({"ok": True})

        if route == "/api/diagnose":
            client = self._require_client()
            with STATE.lock:
                if STATE.running:
                    return self._error("正在刷课，请先停止", 409)
            result = diagnose(client)
            return self._json({"ok": True, "passed": result["passed"], "report": result["report"]})

        if route == "/api/brush":
            client = self._require_client()
            with STATE.lock:
                if STATE.running:
                    return self._error("已经在刷课了", 409)
                indices = payload.get("indices") or []
                courses = []
                for raw in indices:
                    try:
                        index = int(raw)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= index < len(STATE.courses):
                        courses.append(STATE.courses[index])
                if not courses:
                    return self._error("请先选择要刷的课程")
                if STATE.stop_event.is_set():
                    STATE.stop_event.clear()
                STATE.running = True
                STATE.phase = "running"
                STATE.percent = 0
                STATE.current = "准备中"
                STATE.stats = {"done": 0, "failed": 0, "skipped": 0}
                STATE.summary = None
                stop_event = STATE.stop_event
            options = chaoxing.Options.from_dict(payload.get("options") or {})
            STATE.add_log("info", f"开始任务：{len(courses)} 门课程，{options.speed:g}× 倍速")
            thread = threading.Thread(
                target=_brush_worker, args=(client, courses, options), daemon=True,
                name="brush-worker",
            )
            thread.start()
            return self._json({"ok": True})

        if route == "/api/stop":
            with STATE.lock:
                if not STATE.running:
                    return self._json({"ok": True, "running": False})
                STATE.stop_event.set()
                STATE.phase = "stopping"
            STATE.add_log("warn", "收到停止指令，正在收尾…")
            return self._json({"ok": True})

        return self._error("Not found", 404)

    def _require_client(self) -> chaoxing.Client:
        with STATE.lock:
            client = STATE.client
        if client is None or not client.uid:
            raise chaoxing.NotLoggedIn("请先登录")
        return client


def create_server(port: int = 8765) -> tuple[ThreadingHTTPServer, int]:
    for offset in range(0, 20):
        chosen = port + offset
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", chosen), Handler)
        except OSError:
            continue
        httpd.daemon_threads = True
        return httpd, chosen
    raise SystemExit(f"端口 {port}~{port + 19} 都被占用了，请用 --port 指定其它端口")


def start_background(port: int = 8765) -> tuple[ThreadingHTTPServer, int]:
    """给桌面窗口用：起服务但不阻塞当前线程。"""
    httpd, chosen = create_server(port)
    threading.Thread(
        target=httpd.serve_forever,
        kwargs={"poll_interval": 0.3},
        daemon=True,
        name="http-server",
    ).start()
    return httpd, chosen


def run(port: int = 8765, open_browser: bool = True) -> None:
    httpd, chosen = create_server(port)
    url = f"http://127.0.0.1:{chosen}/"
    print(f"学习通刷课助手已启动：{url}")
    print("按 Ctrl+C 停止服务。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.shutdown()
        httpd.server_close()


def _selftest() -> None:
    import chaoxing as cx

    cx._selftest()
    state = AppState()
    state.add_log("info", "hello")
    snapshot = state.snapshot(0)
    assert snapshot["logs"][0]["text"] == "hello"
    assert state.snapshot(1)["logs"] == []
    print("server selftest OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="学习通刷课助手")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--selftest", action="store_true", help="运行自检")
    args = parser.parse_args()
    if args.selftest:
        _selftest()
    else:
        run(args.port, not args.no_browser)
