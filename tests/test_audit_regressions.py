"""审查回归：状态隔离、答案语义、缓存身份与失败回滚（全程本地）。"""

from __future__ import annotations

import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chaoxing  # noqa: E402
import server  # noqa: E402


class DummyHandler(server.Handler):
    def __init__(self):
        self.status = None
        self.payload = None

    def _error(self, message, status=400):
        self.status = status
        self.payload = {"ok": False, "error": message}


def test_judgement_empty_is_unknown():
    q = type("Q", (), {"type": "judgement"})()
    assert server._normalize_quiz_answer(q, "") == ""
    assert server._normalize_quiz_answer(q, "不知道") == ""
    assert server._normalize_quiz_answer(q, "对") == "true"
    assert server._normalize_quiz_answer(q, "错") == "false"


def test_missing_saved_answer_is_not_sent_as_none():
    paper = chaoxing.ExamPaper(
        exam_id="E", course_id="c", clazz_id="cl", cpi="cp", title="t",
        relation_id="r", monitor_enc="", questions=[
            chaoxing.ExamQuestion("q1", "0", "single", "题", [("A", "A")]),
        ],
    )
    paper.saved_ids.add("q1")
    seen = []

    class Fake(chaoxing.Client):
        def request(self, method, url, params=None, data=None, headers=None, timeout=None):
            seen.append(dict(data or {}))
            return 200, json.dumps({"status": "success", "msg": "ok", "data": ""})

    Fake().submit_exam(paper, {})
    assert all(row.get("answerq1") != "None" for row in seen), seen
    assert all(row.get("tempSave") == "false" for row in seen), seen


def test_non_object_exam_response_restores_paper():
    paper = chaoxing.ExamPaper(
        exam_id="E", course_id="c", clazz_id="cl", cpi="cp", title="t",
        relation_id="r", monitor_enc="", questions=[
            chaoxing.ExamQuestion("q1", "0", "single", "题", [("A", "A")]),
        ],
    )

    class Fake(chaoxing.Client):
        def request(self, method, url, params=None, data=None, headers=None, timeout=None):
            return 200, "null"

    server.STATE.client = Fake()
    server.STATE.client.uid = "123"
    server.STATE.courses = [chaoxing.Course("c", "cl", "cp", "t")]
    server.STATE.exam_papers = {"E": paper}
    server.STATE.exam_starting = False
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    try:
        import urllib.request
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_port}/api/exam/submit",
            data=json.dumps({"exam_id": "E", "pre_saved": True}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.read()
        assert status >= 400, status
        assert "E" in server.STATE.exam_papers, server.STATE.exam_papers
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_exam_save_and_finalize_are_serialized():
    paper = chaoxing.ExamPaper(
        exam_id="E", course_id="c", clazz_id="cl", cpi="cp", title="t",
        relation_id="r", monitor_enc="", questions=[
            chaoxing.ExamQuestion("q1", "0", "single", "题", [("A", "A")]),
        ],
    )
    entered = threading.Event()
    release = threading.Event()
    order = []

    class Fake(chaoxing.Client):
        def request(self, method, url, params=None, data=None, headers=None, timeout=None):
            order.append("final" if (data or {}).get("tempSave") == "false" else "save")
            if len(order) == 1:
                entered.set()
                release.wait(5)
            return 200, json.dumps({
                "status": "success", "msg": "ok",
                "data": "1000|60|enc-next" if len(order) == 1 else "",
            })

    client = Fake()
    save = threading.Thread(target=client.save_exam_answer, args=(paper, 0, paper.questions[0], "A"))
    save.start()
    assert entered.wait(5)
    final = threading.Thread(target=client.finalize_exam, args=(paper,))
    final.start()
    threading.Event().wait(0.1)
    assert order == ["save"], order
    release.set()
    save.join(5)
    final.join(5)
    assert order == ["save", "final"], order
    assert paper.finalized


def test_graphics_setting_round_trip():
    data = {}
    original_load = server.load_config
    original_save = server.save_config
    server.load_config = lambda: data
    server.save_config = lambda config: data.update(config)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    try:
        import urllib.request
        base = f"http://127.0.0.1:{httpd.server_port}"

        def request(method, payload=None):
            body = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(
                base + "/api/graphics", data=body, method=method,
                headers={"Content-Type": "application/json"} if body else {},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode())

        assert request("GET")["safe"] is False
        assert request("POST", {"safe": True})["safe"] is True
        assert request("GET")["safe"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()
        server.load_config = original_load
        server.save_config = original_save


def test_stale_quiz_worker_cannot_submit_after_session_switch():
    class FakeClient:
        uid = "old"
        name = "旧账号"
        submitted = []

        def get_points(self, course):
            return [chaoxing.Point("p", "章", 1, False, False)]

        def get_jobs(self, course, point):
            return [chaoxing.Job("j", "workid", "测验")], {}

        def get_quiz(self, course, job, info):
            return chaoxing.Quiz(
                form={}, completed=False,
                questions=[chaoxing.Question("q", "0", "single", "题", ["A", "B"])],
            )

        def submit_quiz(self, quiz, answers, save_only=False):
            self.submitted.append((answers, save_only))
            return True, "ok"

    class BlockingAI:
        entered = threading.Event()
        release = threading.Event()

        def chat(self, messages, temperature=0.2):
            self.entered.set()
            self.release.wait(5)
            return "A"

    course = chaoxing.Course("c", "cl", "cp", "t")
    client = FakeClient()
    ai = BlockingAI()
    with server.STATE.lock:
        server.STATE.session_id = 1
        server.STATE.client = client
        server.STATE.quiz_running = True
    worker = threading.Thread(
        target=server._quiz_worker,
        args=(client, course, "p", "j", ai, 1),
        daemon=True,
    )
    worker.start()
    assert ai.entered.wait(5)
    with server.STATE.lock:
        server.STATE.session_id = 2
        server.STATE.client = None
        server.STATE.quiz_running = False
    ai.release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert not client.submitted, client.submitted


def test_invalid_options_keeps_brush_idle():
    class Fake:
        uid = "123"
        name = "测试"

    course = chaoxing.Course("c", "cl", "cp", "t")
    server.STATE.client = Fake()
    server.STATE.courses = [course]
    server.STATE.running = False
    server.STATE.quiz_running = False
    server.STATE.scan_running = False
    server.STATE.exam_starting = False
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    try:
        import urllib.error
        import urllib.request
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_port}/api/brush",
            data=json.dumps({"indices": [0], "options": "invalid"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.read()
        assert status == 400, status
        assert server.STATE.running is False
    finally:
        httpd.shutdown()
        httpd.server_close()
        server.STATE.client = None
        server.STATE.courses = []


def test_qr_scan_without_remember_clears_old_session():
    class FakeQR:
        uid = "qr-user"
        name = "扫码用户"

        def qr_poll(self):
            return {"state": "confirmed", "name": self.name}

    cleared = []
    original_clear = server.session_store.clear
    server.session_store.clear = lambda: cleared.append(True)
    server.STATE.client = None
    server.STATE.qr_client = FakeQR()
    server.STATE.qr_created = server.time.time()
    server.STATE.session_id = 0
    server.STATE.running = False
    server.STATE.quiz_running = False
    server.STATE.scan_running = False
    server.STATE.exam_starting = False
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    try:
        import urllib.request
        with urllib.request.urlopen(
            f"http://127.0.0.1:{httpd.server_port}/api/qrcode/status?remember=0", timeout=10
        ) as response:
            data = json.loads(response.read().decode())
        assert data["state"] == "confirmed", data
        assert data["remembered"] is False, data
        assert cleared == [True], cleared
        assert server.STATE.client is not None
        assert server.STATE.client.uid == "qr-user"
    finally:
        httpd.shutdown()
        httpd.server_close()
        server.session_store.clear = original_clear
        server.STATE.client = None
        server.STATE.qr_client = None


def test_stale_401_does_not_clear_new_session():
    old = type("Old", (), {"uid": "old"})()
    new = type("New", (), {"uid": "new"})()
    with server.STATE.lock:
        server.STATE.client = new
        server.STATE.session_id = 10
    handler = DummyHandler()
    handler._drop_session(old, 9)
    assert handler.status == 409, (handler.status, handler.payload)
    with server.STATE.lock:
        assert server.STATE.client is new
        server.STATE.client = None


def test_cache_keys_are_scoped_and_reset():
    course = chaoxing.Course("c", "cl", "cp", "t")
    assert server._course_key(course) == ("c", "cl", "cp")
    assert server._quiz_done_key(course, "j") != server._quiz_done_key(
        chaoxing.Course("c", "cl2", "cp", "t"), "j"
    )
    with server.STATE.lock:
        server.STATE.quiz_done.add(server._quiz_done_key(course, "j"))
    server.STATE.reset_session()
    assert not server.STATE.quiz_done
    assert not server.STATE.quiz_cache
    assert not server.STATE.exam_cache


def main() -> None:
    test_judgement_empty_is_unknown()
    test_missing_saved_answer_is_not_sent_as_none()
    test_non_object_exam_response_restores_paper()
    test_exam_save_and_finalize_are_serialized()
    test_graphics_setting_round_trip()
    test_stale_quiz_worker_cannot_submit_after_session_switch()
    test_invalid_options_keeps_brush_idle()
    test_qr_scan_without_remember_clears_old_session()
    test_stale_401_does_not_clear_new_session()
    test_cache_keys_are_scoped_and_reset()
    print("AUDIT REGRESSION OK")


if __name__ == "__main__":
    main()
