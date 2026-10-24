"""软件级测试：通过本地 HTTP API 触发刷课，验证状态轮询与收尾。

用法：python tests/test_server_api.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chaoxing  # noqa: E402
import server  # noqa: E402
from tests import mock_chaoxing  # noqa: E402


def call(base: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    mock_base, mock = mock_chaoxing.serve()
    original_ai = server.ai_from_config
    server.ai_from_config = lambda config=None: None
    client = chaoxing.Client(mooc1=mock_base, mooc2=mock_base, passport=mock_base, sso=mock_base)
    client.login_with_password("demo", "demo")

    server.STATE.client = client
    server.STATE.courses = client.get_courses()
    server.STATE.running = False
    server.STATE.summary = None
    server.STATE.stats = {"done": 0, "failed": 0, "skipped": 0}
    server.STATE.logs.clear()
    server.STATE.seq = 0

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    try:
        diagnosis = call(base, "POST", "/api/diagnose", {})
        assert diagnosis["passed"], diagnosis

        started = call(base, "POST", "/api/brush", {
            "indices": [0],
            "options": {"speed": 3.0, "instant": True},
        })
        assert started["ok"], started

        deadline = time.time() + 15
        status = {}
        while time.time() < deadline:
            status = call(base, "GET", "/api/status?since=0")
            if not status["running"] and status["summary"] is not None:
                break
            time.sleep(0.1)

        assert not status["running"], status
        summary = status["summary"] or {}
        assert summary.get("done") == 3, summary
        assert summary.get("failed") == 0, summary
        assert status["stats"]["done"] == 3, status["stats"]
        assert status["percent"] == 100, status["percent"]
        text = "\n".join(entry["text"] for entry in status["logs"])
        assert "✔ 完成" in text, text

        calls = mock_chaoxing.Handler.reports
        assert len(calls) >= 2 and all(item["enc_ok"] for item in calls), calls

        print("SERVER API OK:", summary)
        print("logs:", len(status["logs"]), "| reports:", len(calls), "| percent:", status["percent"])
    finally:
        server.ai_from_config = original_ai
        httpd.shutdown()
        httpd.server_close()
        mock.shutdown()
        mock.server_close()


if __name__ == "__main__":
    main()
