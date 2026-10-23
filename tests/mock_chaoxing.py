"""本地模拟学习通服务：用于端到端验证完整刷课链路。

只实现客户端实际用到的接口，校验上报参数与 enc 签名。
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

QR_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAK4AAACuAQAAAACHdwtDAAAA90lEQVR42u1XwQ0DMQizegN0pKzOSBkgErUhaT/3rfxoEYpyeVnGGA55Fwu//TzBuDKuyTP1gbEeuI1vPhPJICLCyELC5IsLCUm4iGfqFDNmJASAc7EioUKekW5OpJMM6XYOq06wMVQ6e6ej2Xg3t7WLqRO5Csv0dCHBAnMQAFPFMiIRG4zgRenz2EUShKENFrbeKWEoIcFUgXx+UkNH1UHzY+udHjernASlXp9O5PPYagmn2x8Yewi6nG17LGpFGW10zrlTZtKaSfcsrvHn5aT3ExFy2se8KfU6LbmakQTK1jLDvbMdn8ew72zdyFswRj/5/41+nl98TT5sVWAsHAAAAABJRU5ErkJggg=="
)

LOGIN_HTML = """
<html><body>
<input type="hidden" value="test-uuid" id="uuid"/>
<input type="hidden" value="test-enc" id="enc"/>
</body></html>
"""

COURSE_HTML = """
<div class="course" id="course_1" info="1|测试课程" roleid="3">
  <div class="course-cover">
    <a href="/mycourse/studentcourse?courseid=1001&amp;clazzid=2001&amp;cpi=3001&amp;ut=s"></a>
  </div>
  <div class="course-info">
    <h3><span class="course-name" title="测试课程">测试课程</span></h3>
    <p class="margint10" title="2026-2027 秋 · 测试学院">2026-2027 秋 · 测试学院</p>
    <p class="color3" title="测试老师">测试老师</p>
  </div>
  <input type="hidden" class="clazzId" value="2001">
  <input type="hidden" class="courseId" value="1001">
</div>
"""

POINT_HTML = """
<div class="chapter_unit">
  <li>
    <div id="cur5001" class="chapter_item">
      <a class="clicktitle" href="#">第一章 视频与文档</a>
      <input type="hidden" class="knowledgeJobCount" value="4">
    </div>
    <div id="cur5002" class="chapter_item">
      <a class="clicktitle" href="#">第二章 已完成章节</a>
      <span class="bntHoverTips">已完成</span>
    </div>
  </li>
</div>
"""

CARD_HTML = """
<html><body><script>
window.mArg = {"defaults":{"knowledgeid":"5001","cpi":"3001","ktoken":"ktok"},
"attachments":[
 {"jobid":"job-1","type":"video","objectId":"v1","otherInfo":"nodeId_5001-cpi_3001",
  "playTime":0,"property":{"name":"第一章 视频","rt":"1"},"isPassed":false},
 {"jobid":"job-2","type":"document","otherInfo":"nodeId_5001-cpi_3001",
  "jtoken":"jtok","isPassed":false},
 {"jobid":"job-3","type":"read","otherInfo":"nodeId_5001-cpi_3001",
  "jtoken":"jtok","isPassed":false},
 {"jobid":"job-4","type":"workid","otherInfo":"nodeId_5001-cpi_3001",
  "jtoken":"jtok","isPassed":false}
]};
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    reports: list[dict] = []
    log_calls: list[str] = []
    qr_step = 0
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        return

    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8") -> None:
        self._send_bytes(code, body.encode("utf-8"), ctype)

    def _send_bytes(self, code: int, raw: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}

        if path == "/apis/login/userLogin4Uname.do":
            return self._json({"result": 1, "msg": {"puid": 123, "name": "测试同学"}})

        if path == "/login":
            return self._send(200, LOGIN_HTML)

        if path == "/createqr":
            return self._send_bytes(200, QR_PNG, "image/png")

        if path == "/mooc2-ans/visit/interaction":
            return self._send(200, "<html><ul class='file-list'></ul></html>")

        if path == "/mooc2-ans/mycourse/studentcourse":
            return self._send(200, POINT_HTML)

        if path == "/mooc-ans/knowledge/cards":
            return self._send(200, CARD_HTML)

        if path.startswith("/ananas/status/"):
            return self._json({"status": "success", "dtoken": "dtok1", "duration": 12})

        if path.startswith("/ananas/job/document") or path.startswith("/ananas/job/readv2"):
            return self._json({"status": True})

        if path.startswith("/mooc-ans/multimedia/log/a/"):
            return self._report(query)

        return self._send(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if parsed.path == "/fanyalogin":
            form = urllib.parse.parse_qs(raw.decode("utf-8", "replace"))
            if not form.get("uname") or not form.get("password"):
                return self._json({"status": False, "msg2": "缺少参数"})
            return self._json({"status": True})
        if parsed.path == "/getauthstatus/v2":
            Handler.qr_step += 1
            if Handler.qr_step == 1:
                return self._json({"mes": "未登录", "type": "3", "status": False})
            if Handler.qr_step == 2:
                return self._json({"type": "4", "nickname": "扫码用户", "uid": 123, "status": False})
            return self._json({"status": True, "type": "1"})
        if parsed.path == "/mooc2-ans/visit/courselistdata":
            return self._send(200, COURSE_HTML)
        return self._send(404, "not found")

    def _report(self, query: dict) -> None:
        playing = int(query.get("playingTime") or 0)
        duration = int(query.get("duration") or 0)
        expected = hashlib.md5(
            f"[{query.get('clazzId')}][{query.get('userid')}][{query.get('jobid')}]"
            f"[{query.get('objectId')}][{playing * 1000}][d_yHJ!$pdA~5]"
            f"[{duration * 1000}][0_{duration}]".encode()
        ).hexdigest()
        record = {
            "playing": playing,
            "duration": duration,
            "enc_ok": query.get("enc") == expected,
            "clipTime": query.get("clipTime"),
            "isdrag": query.get("isdrag"),
            "dtype": query.get("dtype"),
            "otherInfo": query.get("otherInfo"),
        }
        Handler.reports.append(record)
        if not record["enc_ok"]:
            return self._json({"isPassed": False, "error": "bad enc"}, 403)
        return self._json({"isPassed": playing >= duration})


def serve(port: int = 0) -> tuple[str, ThreadingHTTPServer]:
    Handler.reports = []
    Handler.qr_step = 0
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}", server
