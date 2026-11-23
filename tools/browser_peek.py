"""在真实浏览器里走一遍「课程页 → 章节 → 播放」，看静默抓拍到底会不会触发。

前一版直接开 knowledge/cards 是错的：播放器里 startSilentFaceCapture() 只在
/studentstudy 页面上才会挂载。所以这里走真实入口：
  课程页 studentcourse → 抓章节链接里的 enc → 打开 studentstudy → 播放 → 观察

观察项：
  · 视频 iframe 里 startSilentFaceCapture / videoFaceCaptureEnc 是否存在
  · 父页面是否发起 capture 相关请求
  · 定时截图落盘 logs/browser/

用法：python tools/browser_peek.py [--seconds 90] [--chapter 7.7] [--headless]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chaoxing  # noqa: E402
import session_store  # noqa: E402

OUT = os.path.join("logs", "browser")
INTEREST = ("capture", "face", "camera", "getusermedia", "snapshot", "takephoto")


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
    if not out:
        raise SystemExit("没有可用的登录 Cookie")
    return out


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--chapter", default="7.7")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    os.makedirs(OUT, exist_ok=True)

    client = chaoxing.Client()
    client.set_cookie_string(session_store.load())
    if not client.check_login():
        raise SystemExit("保存的会话已失效")
    course = [c for c in client.get_courses() if "从爱因斯坦到霍金" in c.title][0]
    point = [p for p in client.get_points(course) if p.title.startswith(args.chapter)][0]
    job = [j for j in client.get_jobs(course, point)[0] if j.type == "video"][0]
    print(f"目标：{point.title}（knowledgeid={point.id}）· {job.name}", flush=True)

    entry = ("https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse"
             f"?courseid={course.course_id}&clazzid={course.clazz_id}"
             f"&cpi={course.cpi}&ut=s")

    from playwright.sync_api import sync_playwright

    hits: list[str] = []
    console: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=args.headless)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_cookies(cookies())
        page = context.new_page()
        page.on("request", lambda r: (
            hits.append(r.url) if any(t in r.url.lower() for t in INTEREST) else None,
            print(f"  [抓拍到请求] {r.method} {r.url[:150]}", flush=True)
            if any(t in r.url.lower() for t in INTEREST) else None,
        ))
        page.on("console", lambda m: console.append(m.text[:150]))

        print("① 打开课程页…", flush=True)
        page.goto(entry, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(4000)
        print("   url:", page.url[:120], flush=True)
        page.screenshot(path=os.path.join(OUT, "a-course.png"))

        links = page.evaluate(
            """() => [...document.querySelectorAll('a')].map(a => a.href)
                     .filter(h => h && h.includes('studentstudy'))"""
        )
        print(f"   课程页里 studentstudy 链接 {len(links)} 条", flush=True)
        target = next((h for h in links if str(point.id) in h), None)
        if target is None and links:
            target = links[0]
            print("   没有精确匹配到本章链接，退用第一条", flush=True)
        if target:
            print("   章节链接:", target[:160], flush=True)
            page.goto(target, wait_until="domcontentloaded", timeout=90_000)
        else:
            direct = ("https://mooc1.chaoxing.com/mycourse/studentstudy"
                      f"?chapterId={point.id}&courseId={course.course_id}"
                      f"&clazzid={course.clazz_id}&cpi={course.cpi}&ut=s&mooc2=1")
            print("   直接构造:", direct[:150], flush=True)
            page.goto(direct, wait_until="domcontentloaded", timeout=90_000)

        page.wait_for_timeout(6000)
        print("② 当前页:", page.url[:130], flush=True)
        page.screenshot(path=os.path.join(OUT, "b-study.png"))
        for frame in page.frames:
            print(f"   frame {frame.url[:110]}", flush=True)

        # 在视频帧里探测抓拍函数是否挂载
        for frame in page.frames:
            if "/ananas/modules/video" not in frame.url:
                continue
            probe = frame.evaluate(
                """() => ({
                    silent: typeof startSilentFaceCapture,
                    enc: (window.videoFaceCaptureEnc || '').slice(0, 16),
                    pwindowEnc: (window.parent && window.parent.videoFaceCaptureEnc || '').slice(0, 16),
                    cfg: (typeof player !== 'undefined' && player)
                        ? Object.keys(player).filter(k => /capture|face/i.test(k)) : null,
                })"""
            )
            print("   视频帧探针:", probe, flush=True)

        played = False
        for frame in page.frames:
            try:
                info = frame.evaluate(
                    """() => { const v = document.querySelector('video');
                        if (!v) return null; v.muted = true; v.play();
                        return {t: v.currentTime, paused: v.paused}; }"""
                )
            except Exception:  # noqa: BLE001
                info = None
            if info:
                played = True
                print(f"③ 播放中（帧 {frame.url[:50]}）: {info}", flush=True)

        if not played:
            print("③ 没找到 video，尝试点播放按钮", flush=True)
            for frame in page.frames:
                for sel in (".vjs-big-play-button", ".xgplayer-start", "video"):
                    try:
                        frame.click(sel, timeout=1500)
                        print(f"   clicked {sel}", flush=True)
                        break
                    except Exception:  # noqa: BLE001
                        continue

        started = time.time()
        index = 0
        hints: dict[str, int] = {}
        while time.time() - started < args.seconds:
            index += 1
            page.wait_for_timeout(6000)
            stamp = int(time.time() - started)
            page.screenshot(path=os.path.join(OUT, f"c-{index:02d}-t{stamp:03d}s.png"))
            for frame in page.frames:
                try:
                    data = frame.evaluate(
                        """() => { const v = document.querySelector('video');
                            const t = [...document.querySelectorAll('div,span,p')]
                                .map(n => (n.innerText || '').trim())
                                .filter(x => x && x.length < 40 && /人脸|拍照|采集|识别|抓拍|摄像头|正对/.test(x));
                            return {time: v ? Math.round(v.currentTime) : null,
                                    paused: v ? v.paused : null, hits: t.slice(0, 4)}; }"""
                    )
                except Exception:  # noqa: BLE001
                    continue
                for text in (data or {}).get("hits", []):
                    hints.setdefault(text, stamp)
                    print(f"   [{stamp:>3}s] 提示：{text}", flush=True)
                if data and data.get("time") is not None and index % 5 == 0:
                    print(f"   [{stamp:>3}s] 播放位置 {data['time']}s paused={data['paused']}", flush=True)

        print("\n" + "=" * 72)
        print(f"观察 {args.seconds}s：capture/face 类请求 {len(hits)} 条")
        for item in hits[:15]:
            print("  ·", item[:150])
        print(f"页面抓拍提示：{len(hints)} 种 {list(hints)}")
        print(f"控制台 {len(console)} 条，前 5 条：")
        for line in console[:5]:
            print("  ·", line[:130])
        print(f"截图目录：{os.path.abspath(OUT)}")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
