"""考试链路测试：列表 → 封面 → 开始 → 答题 → 交卷，以及服务端 API。

用法：python tests/test_exam.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chaoxing  # noqa: E402
import server  # noqa: E402
from tests import mock_chaoxing  # noqa: E402


def call(base: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body) if body else {}


def main() -> None:
    base, mock = mock_chaoxing.serve()
    try:
        client = chaoxing.Client(
            mooc1=base, mooc2=base, passport=base, sso=base,
            exam_api=base, exam_submit=base,
        )
        client.uid = "123"
        client.name = "测试同学"
        course = chaoxing.Course("1001", "2001", "3001", "测试课程")

        # 1. 考试列表
        exams = client.get_exams(course)
        assert len(exams) == 2, exams
        assert exams[0].title == "期中考试" and exams[0].status == "未交", exams[0]
        assert exams[0].enc_task == "etok1", exams[0]
        assert exams[0].expire == "剩余 3 天", exams[0]

        # 2. 封面：普通考试 / 人脸考试
        cover = client.exam_cover(course, exams[0])
        assert cover["title"] == "期中考试", cover
        assert cover["relation_id"] == "8801", cover
        assert cover["need_face"] is False and cover["need_code"] is False, cover

        face_cover = client.exam_cover(course, exams[1])
        assert face_cover["need_face"] is True, face_cover
        try:
            client.start_exam(course, exams[1])
        except chaoxing.ExamBlocked:
            pass
        else:
            raise AssertionError("人脸考试应当被拒绝")

        # 3. 开始考试 + 整卷解析
        paper = client.start_exam(course, exams[0])
        assert paper.title == "期中考试", paper
        assert paper.enc == "exam-enc-1", paper
        assert paper.enc_remain_time == 3600, paper
        assert len(paper.questions) == 4, paper.questions
        kinds = [question.type for question in paper.questions]
        assert kinds == ["single", "multiple", "judgement", "completion"], kinds
        assert paper.questions[1].options[2] == ("C", "C. 选项三"), paper.questions[1].options
        assert paper.questions[3].blanks == ["第1空", "第2空"], paper.questions[3].blanks

        # 4. 逐题保存 + 交卷
        result = client.submit_exam(paper, {
            "9001": "A",
            "9002": ["A", "C"],
            "9003": "true",
            "9004": ["答案一", "答案二"],
        })
        assert result["submitted"] is True and result["saved"] == 4, result
        submits = mock_chaoxing.Handler.exam_submits
        assert len(submits) == 5, submits
        assert submits[0]["answer9001"] == "A", submits[0]
        assert submits[1]["answers9002"] == "AC", submits[1]
        assert submits[2]["answer9003"] == "true", submits[2]
        assert submits[3]["answer90041"] == "答案一", submits[3]
        assert submits[3]["blankNum9004"] == "1,2,", submits[3]
        assert submits[-1]["tempSave"] == "false", submits[-1]
        assert submits[-1]["enc"] == "enc-next", submits[-1]
        assert mock_chaoxing.Handler.exam_final is not None

        # 5. 服务端 API：列表 / 封面 / 开始 / 交卷
        server.STATE.client = client
        server.STATE.courses = [course]
        server.STATE.restore_attempted = True
        server.STATE.running = False
        server.STATE.exam_cache.clear()
        server.STATE.exam_papers.clear()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.daemon_threads = True
        threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
        ).start()
        local = f"http://127.0.0.1:{httpd.server_port}"
        try:
            status, data = call(local, "GET", "/api/exams?indices=0")
            assert status == 200 and len(data["exams"]) == 2, (status, data)

            status, data = call(local, "GET", "/api/exam/cover?course_index=0&exam_id=7002")
            assert status == 200 and data["need_face"] is True, (status, data)

            status, data = call(local, "POST", "/api/exam/start",
                                {"course_index": 0, "exam_id": "7001"})
            assert status == 200 and len(data["paper"]["questions"]) == 4, (status, data)
            assert data["paper"]["remain_time"] == 3600, data

            status, data = call(local, "POST", "/api/exam/answer", {
                "exam_id": "7001",
                "question_id": "9001",
                "answer": "B",
            })
            assert status == 200 and data["saved"] is True, (status, data)

            # 清空已保存的答案，应该照样发一次空值，而不是被跳过
            status, data = call(local, "POST", "/api/exam/answer", {
                "exam_id": "7001",
                "question_id": "9001",
                "answer": "",
            })
            assert status == 200 and data["saved"] is True, (status, data)
            assert mock_chaoxing.Handler.exam_submits[-1]["answer9001"] == "", \
                mock_chaoxing.Handler.exam_submits[-1]

            status, data = call(local, "POST", "/api/exam/submit", {
                "exam_id": "7001",
                "pre_saved": True,
            })
            assert status == 200 and data["submitted"] is True, (status, data)
            assert data["saved"] == 1, data
            assert "7001" not in server.STATE.exam_papers, server.STATE.exam_papers

            # 已经交卷后再次提交，应该 404，而不是重复交卷
            status, data = call(local, "POST", "/api/exam/submit", {
                "exam_id": "7001",
                "pre_saved": True,
            })
            assert status == 404, (status, data)
        finally:
            httpd.shutdown()
            httpd.server_close()

        print("EXAM OK:", len(exams), "场考试 |", len(paper.questions), "题 | 交卷成功")
    finally:
        mock.shutdown()
        mock.server_close()


if __name__ == "__main__":
    main()
