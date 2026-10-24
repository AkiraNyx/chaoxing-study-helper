"""本地考试演示：不连学习通、不碰真实账号，启动一个带假数据的界面。

用法：
    python demo_exam.py [端口]

打开后先点一下「考试演示课程」卡片，再切到「考试」→「刷新考试」→「开始」。
"""

from __future__ import annotations

import json
import sys
import time
import webbrowser

import chaoxing
import server


COURSE = chaoxing.Course(
    course_id="demo-course",
    clazz_id="demo-class",
    cpi="demo-cpi",
    title="考试演示课程",
    teacher="本地演示",
    desc="不连学习通",
)

QUESTIONS = [
    chaoxing.ExamQuestion(
        "1", "0", "single", "学习通考试演示：这个界面里交卷需要点几次确认？",
        [("A", "A. 1 次"), ("B", "B. 2 次"), ("C", "C. 3 次")],
    ),
    chaoxing.ExamQuestion(
        "2", "1", "multiple", "多选题：右侧答题卡可以用来做什么？",
        [("A", "A. 跳到指定题目"), ("B", "B. 查看哪些题已答"), ("C", "C. 修改考试剩余时间")],
    ),
    chaoxing.ExamQuestion(
        "3", "3", "judgement", "判断题：交卷之后还可以继续修改答案。",
    ),
    chaoxing.ExamQuestion(
        "4", "2", "completion", "填空题：本场演示考试的倒计时是 ___ 分钟。",
        blanks=["第 1 空"],
    ),
    chaoxing.ExamQuestion(
        "5", "4", "shortanswer", "简答题：说说考试倒计时对答题节奏有什么用。",
    ),
]


class DemoAI:
    """让「AI 填这题」在演示里也能用，不需要配置 Key。"""

    model = "demo"

    def chat(self, messages, temperature: float = 0.2) -> str:
        try:
            payload = json.loads(messages[-1]["content"])
        except (KeyError, IndexError, json.JSONDecodeError):
            return "A"
        kind = payload.get("type")
        options = payload.get("options") or []
        if kind == "judgement":
            return "true"
        if kind == "multiple":
            return "AC" if len(options) >= 3 else "A"
        if kind == "single":
            return "A"
        if kind == "completion":
            return "30"
        return "演示答案：先看剩余时间，再按题目数量分配节奏。"

    def list_models(self) -> list[str]:
        return ["demo"]

    def test(self) -> str:
        return "演示模式"


class DemoClient:
    uid = "demo-user"
    name = "演示同学"

    def __init__(self) -> None:
        self.submitted = False

    def get_courses(self):
        return [COURSE]

    def get_structure(self, course):
        point = chaoxing.Point("demo-point", "演示章节", 1, False, False)
        return 1, [point], 0, 0

    def get_exams(self, course):
        status = "已完成" if self.submitted else "未交"
        return [chaoxing.Exam(
            exam_id="demo-exam",
            course_id=course.course_id,
            clazz_id=course.clazz_id,
            cpi=course.cpi,
            title="考试演示（本地）",
            status=status,
            expire="剩余 30 分钟",
            enc_task="demo-task",
            course_title=course.title,
        )]

    def exam_cover(self, course, exam):
        if self.submitted:
            raise chaoxing.ExamCompleted("演示考试已交卷")
        return {
            "title": "考试演示（本地）",
            "relation_id": "demo-relation",
            "monitor_enc": "",
            "need_face": False,
            "need_captcha": False,
            "need_code": False,
            "exam_id": exam.exam_id,
        }

    def start_exam(self, course, exam, code: str = ""):
        return chaoxing.ExamPaper(
            exam_id=exam.exam_id,
            course_id=course.course_id,
            clazz_id=course.clazz_id,
            cpi=course.cpi,
            title="考试演示（本地）",
            relation_id="demo-relation",
            monitor_enc="",
            questions=QUESTIONS,
            enc="demo-enc",
            remain_time=1800,
            enc_remain_time=1800,
            last_update_time=int(time.time() * 1000),
            fetched_at=time.time(),
        )

    def submit_exam(self, paper, answers):
        self.submitted = True
        saved = sum(1 for value in answers.values() if value)
        return {
            "saved": saved,
            "count": len(paper.questions),
            "submitted": True,
            "message": "演示交卷成功",
        }

    def save_exam_answer(self, paper, index, question, raw):
        paper.saved_ids.add(question.id)
        return {"status": "success", "msg": "演示暂存成功"}

    def finalize_exam(self, paper):
        self.submitted = True
        return {
            "saved": len(paper.saved_ids),
            "count": len(paper.questions),
            "submitted": True,
            "message": "演示交卷成功",
        }


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8877
    demo = DemoClient()
    server.STATE.client = demo
    server.STATE.courses = [COURSE]
    server.STATE.restore_attempted = True
    server.STATE.running = False
    server.STATE.exam_cache.clear()
    server.STATE.exam_papers.clear()
    server.ai_from_config = lambda config=None: DemoAI()

    httpd, chosen = server.create_server(port)
    url = f"http://127.0.0.1:{chosen}/"
    print(f"考试演示已启动：{url}", flush=True)
    print("先点课程卡片，再切考试 → 刷新考试 → 开始。", flush=True)
    webbrowser.open(url)
    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
