"""按浏览器真实节奏重播一章，验证「节奏假设」。

实测结论（tools/report_diff.py）：本地构造的上报参数与浏览器逐字段一致
（含 enc），差异只在节奏 —— 浏览器 0 → 59 → 119…约每 60 秒一次，
isdrag 首次为 3、之后为 0；而极速模式直接 0 → duration，服务端因此
只记 playTime、不结算 isPassed。

本脚本照浏览器的方式重放一遍，用服务端每次响应里的 isPassed 判定。

用法：
    python tools/paced_replay.py --chapter 11.2 --speed 1.0
    python tools/paced_replay.py --chapter 11.2 --speed 1.0 --interval 60
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chaoxing  # noqa: E402
import session_store  # noqa: E402


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser()
    parser.add_argument("--chapter", default="11.2")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--resume", action="store_true",
                        help="从服务端记录的当前位置继续，而不是从 0 重来")
    parser.add_argument("--max-reports", type=int, default=999)
    args = parser.parse_args()

    client = chaoxing.Client()
    client.set_cookie_string(session_store.load())
    if not client.check_login():
        raise SystemExit("会话失效")
    course = [c for c in client.get_courses() if "从爱因斯坦到霍金" in c.title][0]
    point = [p for p in client.get_points(course) if p.title.startswith(args.chapter)][0]
    job = [j for j in client.get_jobs(course, point)[0] if j.type == "video"][0]
    status = client.video_status(job.objectid)
    dtoken = str(status.get("dtoken") or "")
    duration = int(float(status.get("duration") or 0))
    print(f"{point.title} · {job.name} · {duration}s · 起点 isPassed={job.is_passed}"
          f" playTime={job.playtime_ms // 1000}s", flush=True)
    if job.is_passed:
        print("已通过，无需重播")
        return 0

    def report(playing: int, isdrag: str) -> dict:
        enc = hashlib.md5(
            f"[{course.clazz_id}][{client.uid}][{job.jobid}][{job.objectid}]"
            f"[{playing * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]".encode()
        ).hexdigest()
        params = {
            "clazzId": course.clazz_id, "playingTime": playing, "duration": duration,
            "clipTime": f"0_{duration}", "objectId": job.objectid, "otherInfo": job.otherinfo,
            "courseId": course.course_id, "jobid": job.jobid, "userid": client.uid,
            "isdrag": isdrag, "view": "pc", "enc": enc, "dtype": "Video",
            "videoFaceCaptureEnc": job.face_enc, "attDuration": job.att_duration,
            "attDurationEnc": job.att_duration_enc, "rt": "0.9",
            "courseEngineInfo": "false", "_t": str(int(time.time() * 1000)),
        }
        code, body = client.request(
            "GET", f"{client.mooc1}/mooc-ans/multimedia/log/a/{course.cpi}/{dtoken}",
            params=params,
        )
        try:
            return {"code": code, **json.loads(body)}
        except json.JSONDecodeError:
            return {"code": code, "raw": body[:120]}

    playing = 0
    if args.resume:
        # 从服务端记下的位置继续（向上取整到下一个节拍点）
        recorded = job.playtime_ms // 1000
        playing = (recorded // args.interval) * args.interval
        print(f"resume：从 {playing}s 继续（服务端记录 {recorded}s）", flush=True)
    first = playing == 0
    started = time.time()
    sent = 0
    while playing <= duration and sent < args.max_reports:
        result = report(playing, "3" if first else "0")
        sent += 1
        elapsed = int(time.time() - started)
        print(f"[{elapsed:>4}s] playingTime={playing:>5} isdrag={'3' if first else '0'} "
              f"→ {result}", flush=True)
        first = False
        if result.get("isPassed"):
            print(f"\n✅ 任务点通过：{point.title}（用时 {elapsed}s）")
            fresh = [x for x in client.get_jobs(course, point)[0] if x.type == "video"][0]
            print(f"   复核 isPassed={fresh.is_passed} playTime={fresh.playtime_ms // 1000}s")
            return 0
        if playing >= duration:
            break
        step = min(args.interval * args.speed, duration - playing)
        time.sleep(args.interval)
        playing = int(playing + max(1, step))

    print("\n❌ 按节奏重播到最后仍未通过。")
    fresh = [x for x in client.get_jobs(course, point)[0] if x.type == "video"][0]
    print(f"   复核 isPassed={fresh.is_passed} playTime={fresh.playtime_ms // 1000}s"
          f"（原记录 {job.playtime_ms // 1000}s）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
