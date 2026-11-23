"""对比「浏览器真实上报」与「软件上报」的字段差异。

在真实 Edge 里打开发票页播放视频，抓取 /multimedia/log/a/ 的完整查询串，
解析成字段表；再按 chaoxing.Client.report_video 的构造逻辑生成软件侧参数，
逐字段 diff。为什么要这么做：isPassed 一直为 false，最可能的原因就是
两边参数不同（例如 isdrag / rt / view / attDurationEnc 之类）。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chaoxing  # noqa: E402
import session_store  # noqa: E402

OUT = os.path.join("logs", "browser")


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def cookies() -> list[dict]:
    raw = session_store.load() or ""
    out = []
    for chunk in re.split(r"[;\n]", raw):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        if name.strip():
            out.append({"name": name.strip(), "value": value.strip(),
                        "domain": ".chaoxing.com", "path": "/"})
    return out


def app_params(client, course, job, dtoken, playing: int, duration: int) -> dict:
    """照抄 report_video 的构造，得到软件侧会发出的参数。"""
    enc = hashlib.md5(
        f"[{course.clazz_id}][{client.uid}][{job.jobid}][{job.objectid}]"
        f"[{playing * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]".encode()
    ).hexdigest()
    params = {
        "clazzId": course.clazz_id,
        "playingTime": playing,
        "duration": duration,
        "clipTime": f"0_{duration}",
        "objectId": job.objectid,
        "otherInfo": job.otherinfo,
        "courseId": course.course_id,
        "jobid": job.jobid,
        "userid": client.uid,
        "isdrag": "3",
        "view": "pc",
        "enc": enc,
        "dtype": "Video",
    }
    if job.face_enc:
        params["videoFaceCaptureEnc"] = job.face_enc
    if job.att_duration:
        params["attDuration"] = job.att_duration
    if job.att_duration_enc:
        params["attDurationEnc"] = job.att_duration_enc
    params["rt"] = job.rt or ("0.9" if re.search(r"-rt_d", job.otherinfo) else "1")
    return params


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=75)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--chapter", default="7.7")
    args = parser.parse_args()
    os.makedirs(OUT, exist_ok=True)

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
    print(f"{point.title} · duration={duration}s · dtoken={dtoken[:8]}…", flush=True)

    url = ("https://mooc1.chaoxing.com/mooc-ans/knowledge/cards"
           f"?clazzid={course.clazz_id}&courseid={course.course_id}"
           f"&knowledgeid={point.id}&num=0&ut=s&cpi={course.cpi}"
           "&v=2025-0424-1038-3&mooc2=1")

    seen: list[dict] = []

    def on_request(request) -> None:
        if "/multimedia/log/a/" not in request.url:
            return
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.url).query)
        flat = {k: (v[0] if v else "") for k, v in query.items()}
        flat["__path"] = urllib.parse.urlparse(request.url).path
        seen.append(flat)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=args.headless)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_cookies(cookies())
        page = context.new_page()
        page.on("request", on_request)
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(4000)
        for frame in page.frames:
            try:
                frame.evaluate("""() => { const v = document.querySelector('video');
                    if (v) { v.muted = true; v.play(); } }""")
            except Exception:  # noqa: BLE001
                pass
        deadline = time.time() + args.seconds
        while time.time() < deadline:
            page.wait_for_timeout(3000)
            if len(seen) >= 4:
                break
        browser.close()

    print(f"\n浏览器实际发出了 {len(seen)} 次上报", flush=True)
    if not seen:
        print("没抓到上报，可能视频没起来")
        return 1

    first = seen[0]
    print("\n=== 浏览器第一次上报（去掉 enc 等长值）===")
    for key in sorted(first):
        if key == "__path":
            continue
        value = first[key]
        print(f"  {key:<22} = {value if len(value) <= 46 else value[:46] + '…'}")

    playing = int(first.get("playingTime") or 0)
    mine = app_params(client, course, job, dtoken, playing, duration)
    print(f"\n=== 同样 playingTime={playing} 时，软件会发出的参数 ===")
    for key in sorted(mine):
        value = str(mine[key])
        print(f"  {key:<22} = {value if len(value) <= 46 else value[:46] + '…'}")

    print("\n=== 差异（浏览器有/无 vs 软件有/无）===")
    only_browser = sorted(set(first) - set(mine) - {"__path"})
    only_app = sorted(set(mine) - set(first))
    print(f"  仅浏览器有: {only_browser}")
    print(f"  仅软件有  : {only_app}")
    for key in sorted(set(first) & set(mine)):
        if str(first[key]) != str(mine[key]):
            print(f"  值不同 {key}: 浏览器={str(first[key])[:40]!r}  软件={str(mine[key])[:40]!r}")

    print("\n=== 浏览器后续上报的关键字段 ===")
    for index, item in enumerate(seen[:5], 1):
        print(f"  #{index} playingTime={item.get('playingTime')} isdrag={item.get('isdrag')} "
              f"view={item.get('view')} rt={item.get('rt')} dtype={item.get('dtype')} "
              f"face={'-' if not item.get('videoFaceCaptureEnc') else 'yes'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
