"""批量解套：把「曾被跳到片尾」的视频任务点重新走成有效序列。

实测依据（2026-11-23 同一门课）：
  · 服务端不认「一步报满」，isPassed 会一直是 false，playTime 却记满
  · 带上真实的 60 秒间隔后，服务端才开始接受并覆盖 playTime
  · 判定通过的比例约在时长的 10%~14%：
      11.2（434s）0s 不过 → 60s（13.8%）通过
      7.7（1280s）120s（9.4%）不过 → 180s（14.1%）通过
所以对每个待解套视频：0(isdrag=3) → 每 60 秒 +60s(isdrag=0)，最多走到 duration 或通过为止。

用法：
    python tools/unstick.py --limit 3            # 先试 3 段
    python tools/unstick.py                      # 全部待解套视频
    python tools/unstick.py --chapters 9.2,9.7   # 指定章节
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

LOG_DIR = "logs"


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


class Unstick:
    def __init__(self, client: chaoxing.Client, course: chaoxing.Course, interval: int = 60,
                 max_steps: int = 6) -> None:
        self.client = client
        self.course = course
        self.interval = interval
        self.max_steps = max_steps

    def _report(self, job: chaoxing.Job, dtoken: str, duration: int,
                playing: int, isdrag: str) -> dict:
        enc = hashlib.md5(
            f"[{self.course.clazz_id}][{self.client.uid}][{job.jobid}][{job.objectid}]"
            f"[{playing * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]".encode()
        ).hexdigest()
        params = {
            "clazzId": self.course.clazz_id, "playingTime": playing, "duration": duration,
            "clipTime": f"0_{duration}", "objectId": job.objectid, "otherInfo": job.otherinfo,
            "courseId": self.course.course_id, "jobid": job.jobid, "userid": self.client.uid,
            "isdrag": isdrag, "view": "pc", "enc": enc, "dtype": "Video",
            "videoFaceCaptureEnc": job.face_enc, "attDuration": job.att_duration,
            "attDurationEnc": job.att_duration_enc, "rt": "0.9",
            "courseEngineInfo": "false", "_t": str(int(time.time() * 1000)),
        }
        code, body = self.client.request(
            "GET", f"{self.client.mooc1}/mooc-ans/multimedia/log/a/{self.course.cpi}/{dtoken}",
            params=params,
        )
        try:
            return {"code": code, **json.loads(body)}
        except json.JSONDecodeError:
            return {"code": code, "raw": body[:100]}

    def run_point(self, point: chaoxing.Point) -> tuple[bool, str]:
        jobs, _info = self.client.get_jobs(self.course, point)
        video = next((j for j in jobs if j.type == "video"), None)
        if video is None:
            return False, "没有视频任务点"
        if video.is_passed:
            return True, "本来就已通过"

        status = self.client.video_status(video.objectid)
        dtoken = str(status.get("dtoken") or "")
        duration = int(float(status.get("duration") or 0))
        if not dtoken or duration <= 0:
            return False, f"拿不到 dtoken/duration（{status.get('status')}）"

        print(f"\n▶ {point.title} · {video.name or video.objectid} · {duration}s"
              f"（原 isPassed={video.is_passed} playTime={video.playtime_ms // 1000}s）", flush=True)
        playing = 0
        first = True
        for _step in range(self.max_steps):
            result = self._report(video, dtoken, duration, playing, "3" if first else "0")
            print(f"   [{int(playing / duration * 100):>3}%] playingTime={playing:>5} "
                  f"isdrag={'3' if first else '0'} → isPassed={result.get('isPassed')}", flush=True)
            first = False
            if result.get("isPassed"):
                fresh = next((j for j in self.client.get_jobs(self.course, point)[0]
                              if j.type == "video"), None)
                ok = bool(fresh and fresh.is_passed)
                return ok, f"{'通过' if ok else '响应说通过但复核失败'} @ {playing}s"
            if playing >= duration:
                break
            time.sleep(self.interval)
            playing = min(duration, playing + self.interval)

        fresh = next((j for j in self.client.get_jobs(self.course, point)[0]
                      if j.type == "video"), None)
        if fresh and fresh.is_passed:
            return True, "复核通过"
        return False, "走满步数仍未通过"


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--chapters", default="")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--max-steps", type=int, default=6)
    args = parser.parse_args()

    client = chaoxing.Client()
    client.set_cookie_string(session_store.load())
    if not client.check_login():
        raise SystemExit("会话失效")
    course = [c for c in client.get_courses() if "从爱因斯坦到霍金" in c.title][0]
    _, points, done, total = client.get_structure(course)
    print(f"起始：任务点 {done}/{total}", flush=True)

    pending = [p for p in points if not p.finished and not p.locked]
    if args.chapters:
        wanted = [s.strip() for s in args.chapters.split(",") if s.strip()]
        pending = [p for p in pending if any(p.title.startswith(w) for w in wanted)]
    if args.limit:
        pending = pending[: args.limit]
    print(f"待处理 {len(pending)} 节：" + "、".join(p.title for p in pending), flush=True)

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "unstick.log")
    worker = Unstick(client, course, interval=args.interval, max_steps=args.max_steps)
    results: list[tuple[str, bool, str]] = []
    started = time.time()
    for index, point in enumerate(pending, 1):
        print(f"\n[{index}/{len(pending)}] {point.title}", flush=True)
        try:
            ok, message = worker.run_point(point)
        except chaoxing.NotLoggedIn:
            print("会话失效，停止", flush=True)
            results.append((point.title, False, "会话失效"))
            break
        except (chaoxing.ChaoxingError, OSError) as exc:
            ok, message = False, f"错误：{exc}"
        results.append((point.title, ok, message))
        print(f"   → {'✅' if ok else '❌'} {message}", flush=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%F %T')}\t{point.title}\t{'OK' if ok else 'FAIL'}\t{message}\n")

    _, after_points, done_after, total_after = client.get_structure(course)
    passed = [title for title, ok, _ in results if ok]
    failed = [(title, msg) for title, ok, msg in results if not ok]
    print("\n" + "=" * 70)
    print(f"用时 {int(time.time() - started)}s")
    print(f"任务点 {done}/{total} → {done_after}/{total_after}（+{done_after - done}）")
    print(f"解套成功 {len(passed)}/{len(results)}")
    for title in passed:
        print(f"  ✅ {title}")
    for title, msg in failed:
        print(f"  ❌ {title}：{msg}")
    print(f"日志：{os.path.abspath(log_path)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
