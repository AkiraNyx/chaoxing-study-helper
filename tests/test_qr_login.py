"""扫码登录链路测试：客户端状态机 + 本地服务 API。"""

from __future__ import annotations

import json
import os
import sys
import threading
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
    base, mock = mock_chaoxing.serve()
    original_factory = server.CLIENT_FACTORY
    try:
        # 1. 客户端状态机
        client = chaoxing.Client(mooc1=base, mooc2=base, passport=base, sso=base)
        image = client.qr_start()
        assert image[:8] == b"\x89PNG\r\n\x1a\n", image[:8]
        assert client.qr_poll()["state"] == "waiting"
        scanned = client.qr_poll()
        assert scanned["state"] == "scanned", scanned
        confirmed = client.qr_poll()
        assert confirmed["state"] == "confirmed", confirmed
        assert client.uid == "123", client.uid

        # 2. 服务端扫码 API
        mock_chaoxing.Handler.qr_step = 0
        server.CLIENT_FACTORY = lambda: chaoxing.Client(
            mooc1=base, mooc2=base, passport=base, sso=base
        )
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        local = f"http://127.0.0.1:{httpd.server_port}"
        try:
            started = call(local, "POST", "/api/qrcode/start", {})
            assert started["image"], started
            assert started["mime"].startswith("image/"), started
            states = []
            for _ in range(3):
                data = call(local, "GET", "/api/qrcode/status")
                states.append(data["state"])
            assert states == ["waiting", "scanned", "confirmed"], states
            assert server.STATE.client is not None
            assert server.STATE.client.uid == "123"
            courses = call(local, "GET", "/api/courses")
            assert len(courses["courses"]) == 1, courses
            print("QR OK:", states, "| courses:", len(courses["courses"]))
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        server.CLIENT_FACTORY = original_factory
        mock.shutdown()
        mock.server_close()


if __name__ == "__main__":
    main()
