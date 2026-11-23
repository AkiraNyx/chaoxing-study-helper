"""只读课程探测：定位卡点，不产生任何学习记录。

只发网页端浏览时本来就会发的 GET：
  courselistdata / studentcourse / knowledge/cards / ananas/status
不会调用 multimedia/log（视频上报）、job/document、job/readv2、work/addStudentWorkNew。

用法：
    python tools/course_probe.py --match "从爱因斯坦到霍金"
    python tools/course_probe.py --match 关键词 --max-points 40
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chaoxing  # noqa: E402
import session_store  # noqa: E402

TYPE_NAMES = {
    "video": "视频", "document": "文档", "read": "阅读",
    "workid": "测验", "work": "测验", "live": "直播", "audio": "音频",
}


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _client() -> chaoxing.Client:
    cookie = session_store.load()
    if not cookie:
        raise SystemExit("没有保存的登录会话，先在软件里登录并勾选「记住登录状态」")
    client = chaoxing.Client()
    client.set_cookie_string(cookie)
    if not client.check_login():
        raise SystemExit("保存的会话已失效，请重新登录")
    return client


def pick_course(courses: list[chaoxing.Course], needle: str) -> tuple[int, chaoxing.Course]:
    hits = [(i, c) for i, c in enumerate(courses) if needle in c.title]
    if not hits:
        print("没有匹配的课程，当前列表：")
        for i, course in enumerate(courses):
            print(f"  [{i}] {course.title}  ({course.teacher})")
        raise SystemExit(1)
    if len(hits) > 1:
        print("匹配到多门课程，按最接近的处理：")
        for i, course in hits:
            print(f"  [{i}] {course.title}")
    return hits[0]


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser(description="只读课程探测")
    parser.add_argument("--match", required=True)
    parser.add_argument("--max-points", type=int, default=12)
    parser.add_argument("--max-videos", type=int, default=40)
    args = parser.parse_args()

    client = _client()
    print(f"会话正常：uid={client.uid} name={client.name}", flush=True)

    started = time.time()
    courses = client.get_courses()
    print(f"课程 {len(courses)} 门（{time.time() - started:.1f}s）", flush=True)
    index, course = pick_course(courses, args.match)
    print(f"\n目标：[{index}] {course.title}")
    print(f"      course={course.course_id} clazz={course.clazz_id} cpi={course.cpi}")
    print(f"      {course.teacher} · {course.desc}", flush=True)

    started = time.time()
    units, points, done_tasks, total_tasks = client.get_structure(course)
    print(
        f"\n结构：{units} 章 · {len(points)} 节 · 任务点 {done_tasks}/{total_tasks}"
        f"（{time.time() - started:.1f}s）"
    )

    print("\n{:<4}{:<40}{:<8}{:<8}{:<8}".format("idx", "章节", "标记", "任务点", "待完成"))
    for i, point in enumerate(points):
        marks = []
        if point.finished:
            marks.append("已完成")
        if point.locked:
            marks.append("未解锁")
        print("{:<4}{:<40}{:<8}{:<8}{}".format(
            i, point.title[:38], "/".join(marks) or "-", point.job_count, ""))

    flags: list[str] = []
    pending = [p for p in points if not p.finished and not p.locked]
    print(f"\n待处理章节 {len(pending)} / {len(points)}，下面看前 {args.max_points} 节的任务点：")

    videos_seen = 0
    for point in pending[: args.max_points]:
        try:
            jobs, info = client.get_jobs(course, point)
        except chaoxing.NotLoggedIn:
            raise SystemExit("会话中途失效，请重新登录")
        except (chaoxing.ChaoxingError, OSError) as exc:
            print(f"\n● {point.title}\n   读取任务点失败：{exc}")
            flags.append(f"{point.title}：读取任务点失败（{exc}）")
            continue
        if info.get("notOpen"):
            print(f"\n● {point.title}\n   章节未开放")
            flags.append(f"{point.title}：章节未开放（接口返回「章节未开放」）")
            continue
        todo = [job for job in jobs if not job.is_passed]
        print(f"\n● {point.title}   任务点 {len(jobs)}，待完成 {len(todo)}")
        if not jobs:
            flags.append(f"{point.title}：0 个任务点（Runner 会直接记 ✔ 已完成，进度不动）")
        for job in jobs:
            kind = TYPE_NAMES.get(job.type, job.type)
            line = (
                f"   - {kind:<4} passed={str(job.is_passed):<5} "
                f"play={job.playtime_ms // 1000}s  {job.name[:34] or job.jobid}"
            )
            if job.type == "video" and not job.is_passed and videos_seen < args.max_videos:
                videos_seen += 1
                status = client.video_status(job.objectid) if job.objectid else {}
                state = status.get("status")
                try:
                    duration = int(float(status.get("duration") or 0))
                except (TypeError, ValueError):
                    duration = 0
                played = job.playtime_ms / 1000.0
                line += f"  | status={state} duration={duration}s"
                if state != "success" or not status.get("dtoken"):
                    line += "  ⟵ 视频信息取不到"
                    flags.append(
                        f"{point.title} · {job.name}：video_status 非 success（{state}），"
                        "Runner 会直接记失败"
                    )
                elif duration <= 0:
                    line += "  ⟵ 时长为 0"
                    flags.append(f"{point.title} · {job.name}：时长为 0，Runner 直接跳过")
                elif played >= duration:
                    line += f"  ⟵ 续播已到片尾（{played:.0f}/{duration}s）且未通过"
                    flags.append(
                        f"{point.title} · {job.name}：续播位置已到片尾但 isPassed=false → "
                        "正是「待核验」，Runner 会停机（chaoxing.py _study_video 返回 None）"
                    )
                elif job.face_enc:
                    flags.append(f"{point.title} · {job.name}：带 videoFaceCaptureEnc（人脸抓拍）")
            elif job.type in {"workid", "work"}:
                line += "  → 被排序到最后，Runner 只提示手动完成"
            elif job.type not in TYPE_NAMES:
                line += "  ⟵ 未知类型，Runner 会跳过"
                flags.append(f"{point.title} · {job.name}：未知任务类型 {job.type}")
            print(line)

    print("\n" + "=" * 78)
    if not flags:
        print("没有发现结构性卡点：任务点类型、视频时长、通过状态都正常。")
        print("那么「卡住」更可能是视频上报静默窗（30–90s 无日志），或某个请求在重试。")
    else:
        print(f"发现 {len(flags)} 处可疑点（按影响排序，越靠前越可能就是你看到的卡住）：")
        for item in flags:
            print(f"  ! {item}")
    print("=" * 78)
    print(f"总耗时 {time.time() - started:.1f}s；本次探测未写入任何学习记录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
