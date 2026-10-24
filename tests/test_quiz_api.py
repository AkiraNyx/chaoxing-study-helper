"""测验提交语义、会话失效与请求来源校验的回归测试。

用法：python tests/test_quiz_api.py
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chaoxing  # noqa: E402
import server  # noqa: E402


class FakeQuizClient:
    uid = "900"
    name = "测试同学"

    def __init__(self):
        self.save_flags = []

    def submit_quiz(self, quiz, answers, save_only=False):
        self.save_flags.append(save_only)
        return True, "ok"


class ExpiredClient:
    uid = "900"
    name = ""

    def get_courses(self):
        raise chaoxing.NotLoggedIn("登录已失效，请重新登录")


def call(base: str, method: str, path: str, payload=None,
         content_type: str = "application/json", origin: str = "") -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {}
    if data is not None:
        headers["Content-Type"] = content_type
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body) if body else {}


def raw_status(base: str, path: str, headers: dict) -> int:
    parsed = urllib.parse.urlsplit(base)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=15)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def main() -> None:
    course = chaoxing.Course("c1", "1", "2", "测试课程")
    quiz = chaoxing.Quiz(
        form={},
        questions=[
            chaoxing.Question("q1", "0", "single", "题目一", ["A", "B"]),
            chaoxing.Question("q2", "0", "single", "题目二", ["A", "B"]),
        ],
        completed=False,
        course_id=course.course_id,
        clazz_id=course.clazz_id,
        cpi=course.cpi,
    )
    original_load = server._load_quiz
    server._load_quiz = lambda client, index, point_id, jobid: (course, quiz)

    client = FakeQuizClient()
    server.STATE.client = client
    server.STATE.courses = [course]
    server.STATE.restore_attempted = True
    server.STATE.quiz_done.clear()
    server.STATE.quiz_cache.clear()
    server.STATE.running = False

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    ).start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    try:
        # 1. 有题没答：只暂存，不算提交，也不能进已完成白名单
        status, data = call(base, "POST", "/api/quiz/submit", {
            "course_index": 0,
            "point_id": "p1",
            "jobid": "work-1",
            "answers": {"q1": "A"},
        })
        assert status == 200, (status, data)
        assert data["saved"] is True and data["submitted"] is False, data
        assert data["missing"] == 1, data
        assert client.save_flags[-1] is True, client.save_flags
        done_key = server._quiz_done_key(course, "work-1")
        assert done_key not in server.STATE.quiz_done, server.STATE.quiz_done

        # 2. 全部答完：正式提交，才标记完成
        status, data = call(base, "POST", "/api/quiz/submit", {
            "course_index": 0,
            "point_id": "p1",
            "jobid": "work-1",
            "answers": {"q1": "A", "q2": "B"},
        })
        assert status == 200, (status, data)
        assert data["submitted"] is True and data["saved"] is False, data
        assert data["missing"] == 0, data
        assert client.save_flags[-1] is False, client.save_flags
        assert done_key in server.STATE.quiz_done, server.STATE.quiz_done

        # 3. 已删除的 POST 详情接口应该 404，而不是 500
        status, data = call(base, "POST", "/api/quiz/detail", {})
        assert status == 404, (status, data)

        # 4. 会话失效：401 并清掉内存客户端，避免刷新死循环
        server.STATE.client = ExpiredClient()
        status, data = call(base, "GET", "/api/courses")
        assert status == 401, (status, data)
        assert server.STATE.client is None, server.STATE.client
        status, data = call(base, "GET", "/api/session")
        assert data.get("logged_in") is False, data

        # 5. 跨站表单式的 text/plain POST 应被拒绝
        status, data = call(base, "POST", "/api/stop", {"x": "="}, content_type="text/plain")
        assert status == 403, (status, data)

        # 6. 非本机 Origin 的 JSON POST 也应被拒绝
        status, data = call(base, "POST", "/api/stop", {"x": 1}, origin="http://evil.example")
        assert status == 403, (status, data)

        # 7. 非回环 Host（DNS rebinding）应被拒绝
        assert raw_status(base, "/api/session", {"Host": "evil.example"}) == 403

        # 8. 刷课中登录应在创建客户端之前被拒绝
        server.STATE.running = True
        status, data = call(base, "POST", "/api/login",
                            {"mode": "cookie", "cookie": "x"})
        assert status == 409, (status, data)
        server.STATE.running = False

        print("QUIZ API OK: saved/submitted 语义、401 清理、来源校验通过")
    finally:
        server._load_quiz = original_load
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
