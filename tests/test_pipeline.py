"""完整链路测试：登录 → 课程 → 章节 → 任务点 → 上报 → 完成。

用法：python tests/test_pipeline.py
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chaoxing  # noqa: E402
import crypto  # noqa: E402
import server  # noqa: E402
from tests import mock_chaoxing  # noqa: E402


def main() -> None:
    base, mock = mock_chaoxing.serve()
    try:
        client = chaoxing.Client(mooc1=base, mooc2=base, passport=base, sso=base)
        client.login_with_password("test-user", "test-pass")
        assert client.uid == "123", client.uid
        assert client.name == "测试同学", client.name

        courses = client.get_courses()
        assert len(courses) == 1, courses
        assert courses[0].title == "测试课程", courses[0]
        assert courses[0].cpi == "3001"

        points = client.get_points(courses[0])
        assert len(points) == 2, points
        assert points[0].finished is False
        assert points[1].finished is True

        jobs, info = client.get_jobs(courses[0], points[0])
        assert len(jobs) == 4, jobs
        assert info.get("knowledgeid") == "5001", info
        kinds = sorted(job.type for job in jobs)
        assert kinds == ["document", "read", "video", "workid"], kinds

        diagnosis = server.diagnose(client)
        assert diagnosis["passed"], diagnosis
        assert len(diagnosis["report"]) >= 4, diagnosis
        assert all(item["ok"] for item in diagnosis["report"]), diagnosis

        logs: list[str] = []
        progress: list[int] = []
        runner = chaoxing.Runner(
            client,
            chaoxing.Options(speed=3.0, instant=True),
            threading.Event(),
            lambda level, text: logs.append(f"{level}:{text}"),
            lambda percent, text: progress.append(percent),
        )
        summary = runner.run(courses)

        assert summary["done"] == 3, summary
        assert summary["failed"] == 0, summary
        assert summary["skipped"] == 2, summary
        assert progress and progress[-1] == 100, progress

        reports = mock_chaoxing.Handler.reports
        assert len(reports) >= 2, reports
        for report in reports:
            assert report["enc_ok"], report
            assert report["clipTime"] == "0_12", report
            assert report["isdrag"] == "3", report
            assert report["dtype"] == "Video", report
        assert reports[-1]["playing"] == 12, reports[-1]
        assert any("✔ 完成" in line for line in logs), logs

        print("E2E OK:", summary)
        print("reports:", len(reports), "| enc all ok | progress:", progress[:3], "...", progress[-1])
    finally:
        mock.shutdown()
        mock.server_close()


if __name__ == "__main__":
    main()
