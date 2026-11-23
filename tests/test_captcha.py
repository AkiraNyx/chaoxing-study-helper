"""考试滑块验证码测试：本地合成图定位、JSONP 解析、开始考试参数传递。"""

from __future__ import annotations

import io
import os
import random
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chaoxing  # noqa: E402


def test_jsonp() -> None:
    payload = chaoxing._captcha_jsonp(b'cx_captcha_function({"error":0,"t":12345});')
    assert payload["t"] == 12345, payload
    try:
        chaoxing._captcha_jsonp(b'cx_captcha_function({"error":1,"msg":"blocked"});')
    except chaoxing.ExamBlocked as exc:
        assert "blocked" in str(exc), exc
    else:
        raise AssertionError("错误 JSONP 应当抛出 ExamBlocked")


def test_synthetic_slide() -> None:
    random.seed(7)
    raw = bytes(random.randrange(256) for _ in range(320 * 160 * 3))
    background = Image.frombytes("RGB", (320, 160), raw)
    cutout = background.crop((140, 0, 196, 160))
    shade_buf = io.BytesIO()
    cutout_buf = io.BytesIO()
    background.save(shade_buf, "PNG")
    cutout.save(cutout_buf, "PNG")
    x, score = chaoxing.solve_slide_captcha(shade_buf.getvalue(), cutout_buf.getvalue())
    assert x == 143, (x, score)
    assert score > 0.99, score


def test_start_passes_validate() -> None:
    class FakeClient(chaoxing.Client):
        def __init__(self) -> None:
            super().__init__(timeout=5)
            self.cover_seen = None
            self.params_seen = None

        def exam_cover(self, course, exam):
            return {
                "title": "验证码考试",
                "relation_id": "rel-1",
                "need_face": False,
                "need_captcha": True,
                "need_code": False,
                "captcha_id": "captcha-1",
                "referer": "https://example.test/cover",
            }

        def solve_exam_captcha(self, cover):
            self.cover_seen = cover
            return "validate-1"

        def _exam_no_redirect(self, url, params=None):
            self.params_seen = (url, params)
            return 302, "", {"Location": "https://example.test/paper?enc=enc-1"}

        def _fetch_exam_paper(self, course, exam, cover, enc):
            return "paper", enc

    client = FakeClient()
    course = chaoxing.Course("1001", "2001", "3001", "测试课程")
    exam = chaoxing.Exam("7001", "1001", "2001", "3001", "验证码考试")
    paper = client.start_exam(course, exam)
    assert paper == ("paper", "enc-1"), paper
    assert client.cover_seen["captcha_id"] == "captcha-1", client.cover_seen
    assert client.params_seen[1]["captchavalidate"] == "validate-1", client.params_seen


def test_cover_parser() -> None:
    html = (
        '<input id="testUserRelationId" value="rel-9">'
        '<input id="captchaCheck" value="1">'
        '<input id="captchaCaptchaId" value="cid-9">'
        '<span class="overHidden2">考试标题</span>'
    )
    cover = chaoxing.parse_exam_cover(html)
    assert cover["need_captcha"] is True, cover
    assert cover["captcha_id"] == "cid-9", cover
    assert cover["relation_id"] == "rel-9", cover


def test_captcha_uses_mobile_ua() -> None:
    class Capture(BaseHTTPRequestHandler):
        seen: dict[str, str] = {}

        def do_GET(self) -> None:  # noqa: N802
            type(self).seen = {key: value for key, value in self.headers.items()}
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        client = chaoxing.Client(timeout=5)
        base = f"http://127.0.0.1:{httpd.server_port}"
        assert client._captcha_get(f"{base}/image", {}, "https://example.test/cover") == b"ok"
        seen = {key.lower(): value for key, value in Capture.seen.items()}
        assert "com.chaoxing.mobile" in seen["user-agent"], seen
        assert seen["x-requested-with"] == "com.chaoxing.mobile", seen
        assert seen["referer"] == "https://example.test/cover", seen

        assert client._captcha_get(f"{base}/image", {}) == b"ok"
        seen = {key.lower(): value for key, value in Capture.seen.items()}
        assert "referer" not in seen, seen
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def main() -> None:
    test_jsonp()
    test_synthetic_slide()
    test_start_passes_validate()
    test_cover_parser()
    test_captcha_uses_mobile_ua()
    print("CAPTCHA OK: JSONP | 滑块定位 | 开始参数 | 封面解析 | 手机 UA")


if __name__ == "__main__":
    main()
