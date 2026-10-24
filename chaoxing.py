"""超星学习通接口客户端与自动刷课执行器（纯标准库，无第三方依赖）。

接口来源为学习通网页端行为：课程列表 / 章节 / 任务点卡片 / 视频进度上报。
仅供个人账号学习进度整理使用。
"""

from __future__ import annotations

import gzip
import hashlib
import http.cookiejar
import json
import math
import random
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Optional

import font_decoder

from crypto import encrypt_login

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MOOC1 = "https://mooc1.chaoxing.com"
MOOC2 = "https://mooc2-ans.chaoxing.com"
PASSPORT = "https://passport2.chaoxing.com"
SSO = "https://sso.chaoxing.com"
VIDEO_REFERER = "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2025-0725-1842"

# 考试走手机客户端协议：mooc1-api 负责封面/开始/整卷，mooc1 负责交卷
EXAM_API = "https://mooc1-api.chaoxing.com"
EXAM_SUBMIT = "https://mooc1.chaoxing.com"
EXAM_LIST_PATH = "/exam/phone/task-list"
EXAM_COVER_PATH = "/exam-ans/exam/phone/task-exam"
EXAM_START_PATH = "/exam-ans/exam/phone/start"
EXAM_PREVIEW_PATH = "/exam-ans/exam/phone/preview"
EXAM_SUBMIT_PATH = "/exam-ans/exam/test/reVersionSubmitTestNew"

EXAM_ANDROID = "Android 12"
EXAM_MODEL = "MI11"
EXAM_LOCALE = "zh_CN"
EXAM_VERSION = "6.3.9"
EXAM_BUILD = "10824_250"


def _mobile_ua(imei: str) -> str:
    """构造学习通手机客户端 UA（含 schild 签名）。"""
    sign = hashlib.md5(" ".join((
        "(schild:ipL$TkeiEmfy1gTXb2XHrdLN0a@7c^vu)",
        f"(device:{EXAM_MODEL})",
        f"Language/{EXAM_LOCALE}",
        f"com.chaoxing.mobile/ChaoXingStudy_3_{EXAM_VERSION}_android_phone_{EXAM_BUILD}",
        f"(@Kalimdor)_{imei}",
    )).encode()).hexdigest()
    return " ".join((
        f"Dalvik/2.1.0 (Linux; U; {EXAM_ANDROID}; {EXAM_MODEL} Build/SKQ1.211006.001)",
        f"(schild:{sign})",
        f"(device:{EXAM_MODEL})",
        f"Language/{EXAM_LOCALE}",
        f"com.chaoxing.mobile/ChaoXingStudy_3_{EXAM_VERSION}_android_phone_{EXAM_BUILD}",
        f"(@Kalimdor)_{imei}",
    ))


def exam_signature(uid: str, qid: str, x: int, y: int) -> dict:
    """计算考试提交接口的 pos/rd/value/_edt 签名参数。"""
    ts = f"{round(time.time() * 1000)}"
    r1 = random.randrange(0, 9)
    r2 = random.randrange(0, 9)
    a = f"{secrets.token_hex(16)}{ts[4:]}{r1}{r2}{qid or ''}"
    temp = 0
    for ch in a:
        temp = (temp << 5) - temp + ord(ch)
    salt = f"{r1}{r2}{(0x7FFFFFFF & temp) % 10}"
    enc_val = f"{uid}" + (f"_{qid}" if qid else "") + f"|{salt}"
    enc_val2 = "".join(str(ord(c)) for c in enc_val)
    b = len(enc_val2) // 5
    c = int(enc_val2[b] + enc_val2[2 * b] + enc_val2[3 * b] + enc_val2[4 * b])
    d = len(enc_val) // 2 + 1
    e = (c * int(enc_val2[:10]) + d) % 0x7FFFFFFF
    pos = f"({x}|{y})"
    result = ""
    for ch in pos:
        temp = ord(ch) ^ math.floor(e / 0x7FFFFFFF * 0xFF)
        result += f"{temp:02x}"
        e = (c * e + d) % 0x7FFFFFFF
    return {
        "pos": f"{result}{secrets.token_hex(4)}",
        "rd": random.random(),
        "value": pos,
        "_edt": f"{ts}{salt}",
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁用自动重定向，用来识别 302：进考试成功 / 考试已完成。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class ChaoxingError(Exception):
    """接口或解析错误。"""


class LoginError(ChaoxingError):
    """登录失败。"""


class NotLoggedIn(ChaoxingError):
    """会话失效。"""


class ExamError(ChaoxingError):
    """考试环节错误。"""


class ExamBlocked(ExamError):
    """考试有人脸识别 / 人机验证，本地无法继续。"""


class ExamCodeRequired(ExamError):
    """需要考试码。"""


class ExamCompleted(ExamError):
    """考试已完成，不能重复作答。"""


# ---------------------------------------------------------------------------
# 极简 HTML 树，够用就行
# ---------------------------------------------------------------------------
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: Optional[dict] = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list = []

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        return self.attrs.get(name, default)

    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def has_class(self, name: str) -> bool:
        return name in self.classes()

    def text(self) -> str:
        parts = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.text())
        return "".join(parts)

    def iter(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.iter()

    def find_all(self, tag: Optional[str] = None, cls: Optional[str] = None) -> list["Node"]:
        result = []
        for node in self.iter():
            if tag and node.tag != tag:
                continue
            if cls and not node.has_class(cls):
                continue
            result.append(node)
        return result

    def find(self, tag: Optional[str] = None, cls: Optional[str] = None) -> Optional["Node"]:
        for node in self.iter():
            if tag and node.tag != tag:
                continue
            if cls and not node.has_class(cls):
                continue
            return node
        return None


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs})
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html or "")
    builder.close()
    return builder.root


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Course:
    course_id: str
    clazz_id: str
    cpi: str
    title: str
    teacher: str = ""
    desc: str = ""


@dataclass
class Point:
    id: str
    title: str
    job_count: int = 1
    finished: bool = False
    locked: bool = False


@dataclass
class Job:
    jobid: str
    type: str
    name: str = ""
    objectid: str = ""
    otherinfo: str = ""
    jtoken: str = ""
    playtime_ms: int = 0
    rt: str = ""
    att_duration: str = ""
    att_duration_enc: str = ""
    face_enc: str = ""
    is_passed: bool = False
    enc: str = ""


@dataclass
class Question:
    id: str
    type_code: str
    type: str
    title: str
    options: list[str]


@dataclass
class Quiz:
    form: dict
    questions: list[Question]
    encrypted: bool = False
    decoded: bool = False
    completed: bool = False
    score: float | None = None
    course_id: str = ""
    clazz_id: str = ""
    cpi: str = ""


@dataclass
class Exam:
    exam_id: str
    course_id: str
    clazz_id: str
    cpi: str
    title: str
    status: str = "未交"
    expire: str = ""
    enc_task: str = ""
    course_title: str = ""


@dataclass
class ExamQuestion:
    id: str
    type_code: str
    type: str
    title: str
    options: list[tuple[str, str]] = field(default_factory=list)
    blanks: list[str] = field(default_factory=list)
    answer: str = ""


@dataclass
class ExamPaper:
    exam_id: str
    course_id: str
    clazz_id: str
    cpi: str
    title: str
    relation_id: str
    monitor_enc: str
    questions: list[ExamQuestion]
    enc: str = ""
    remain_time: int = 0
    enc_remain_time: int = 0
    last_update_time: int = 0
    need_face: bool = False
    need_captcha: bool = False
    need_code: bool = False
    saved_ids: set[str] = field(default_factory=set)
    fetched_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------
def parse_courses(html: str) -> list[Course]:
    root = parse_html(html)
    courses: list[Course] = []
    for div in root.find_all("div"):
        if "course" not in div.classes():
            continue
        if not (div.get("id") or div.get("info")):
            continue
        clazz_input = div.find("input", "clazzId")
        course_input = div.find("input", "courseId")
        if not clazz_input or not course_input:
            continue
        cpi = ""
        for a in div.find_all("a"):
            match = re.search(r"cpi=(\d+)", a.get("href") or "")
            if match:
                cpi = match.group(1)
                break
        title_el = div.find("span", "course-name")
        title = (title_el.get("title") if title_el else "") or (title_el.text().strip() if title_el else "")
        teacher_el = div.find("p", "color3")
        teacher = (teacher_el.get("title") if teacher_el else "") or (teacher_el.text().strip() if teacher_el else "")
        desc_el = div.find("p", "margint10")
        desc = desc_el.get("title", "") if desc_el else ""
        if title and cpi:
            courses.append(Course(
                course_id=course_input.get("value", ""),
                clazz_id=clazz_input.get("value", ""),
                cpi=cpi,
                title=title.strip(),
                teacher=teacher.strip(),
                desc=(desc or "").strip(),
            ))
    seen = set()
    unique = []
    for course in courses:
        key = (course.course_id, course.clazz_id, course.cpi)
        if key in seen:
            continue
        seen.add(key)
        unique.append(course)
    return unique


def parse_folders(html: str) -> list[tuple[str, str]]:
    root = parse_html(html)
    folders = []
    for li in root.find_all("li"):
        fileid = li.get("fileid")
        if not fileid:
            continue
        rename = li.find("input", "rename-input")
        folders.append((fileid, (rename.get("value", "") if rename else "")))
    return folders


def parse_structure(html: str) -> tuple[int, list[Point], int, int]:
    root = parse_html(html)
    units = 0
    for div in root.find_all("div"):
        if not div.has_class("chapter_unit"):
            continue
        for node in div.iter():
            if node.tag == "div" and re.fullmatch(r"cur\d+", node.get("id") or ""):
                units += 1
                break
    done = total = 0
    match = re.search(
        r"已完成任务点.*?<span[^>]*>\s*(\d+)\s*</span>\s*/\s*(\d+)",
        html,
        re.S,
    )
    if not match:
        plain = re.sub(r"<[^>]+>", " ", html)
        match = re.search(r"已完成任务点\s*[:：]?\s*(\d+)\s*/\s*(\d+)", plain)
    if match:
        done, total = int(match.group(1)), int(match.group(2))
    return units, parse_points(html), done, total


def parse_points(html: str) -> list[Point]:
    root = parse_html(html)
    points: list[Point] = []
    for div in root.find_all("div"):
        match = re.fullmatch(r"cur(\d+)", div.get("id") or "")
        if not match:
            continue
        title_el = div.find("a", "clicktitle")
        title = title_el.text().strip() if title_el else ""
        count_el = div.find("input", "knowledgeJobCount")
        try:
            job_count = int(count_el.get("value", "1")) if count_el else 1
        except (TypeError, ValueError):
            job_count = 1
        tip_el = div.find("span", "bntHoverTips")
        tip = tip_el.text().strip() if tip_el else ""
        points.append(Point(
            id=match.group(1),
            title=title or f"章节 {match.group(1)}",
            job_count=job_count,
            finished="已完成" in tip,
            locked="解锁" in tip,
        ))
    return points


def extract_marg(html: str) -> Optional[dict]:
    for match in re.finditer(r"mArg\s*=\s*\{", html or ""):
        start = html.index("{", match.start())
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(html)):
            char = html[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            else:
                if char == '"':
                    in_string = True
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(html[start:index + 1])
                        except json.JSONDecodeError:
                            break
                        if isinstance(data, dict):
                            return data
                        break
    return None


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _job_from_card(card: dict) -> Optional[Job]:
    jobid = str(card.get("jobid") or "")
    if not jobid:
        return None
    prop = card.get("property") or {}
    raw_type = (card.get("type") or "").lower()
    if "live" in raw_type or "live" in str(prop.get("type", "")).lower():
        job_type = "live"
    elif raw_type in {"video", "document", "workid", "read", "audio"}:
        job_type = raw_type
    elif prop.get("read"):
        job_type = "read"
    else:
        job_type = raw_type or "unknown"
    try:
        playtime = int(card.get("playTime") or 0)
    except (TypeError, ValueError):
        playtime = 0
    passed = card.get("isPassed")
    if not _as_bool(passed):
        nested = card.get("job")
        if isinstance(nested, dict) and _as_bool(nested.get("isPassed")):
            passed = nested.get("isPassed")
        elif _as_bool(prop.get("isPassed")):
            passed = prop.get("isPassed")
    return Job(
        jobid=jobid,
        type=job_type,
        name=str(prop.get("name") or prop.get("title") or ""),
        objectid=str(card.get("objectId") or prop.get("objectid") or ""),
        otherinfo=str(card.get("otherInfo") or "").split("&")[0],
        jtoken=str(card.get("jtoken") or ""),
        playtime_ms=playtime,
        rt=str(prop.get("rt") or ""),
        att_duration=str(card.get("attDuration") or ""),
        att_duration_enc=str(card.get("attDurationEnc") or ""),
        face_enc=str(card.get("videoFaceCaptureEnc") or ""),
        is_passed=_as_bool(passed),
        enc=str(card.get("enc") or ""),
    )


def parse_quiz_html(html: str, decoder=None) -> tuple[dict, list[Question]]:
    root = parse_html(html)
    if decoder is None:
        decoder = font_decoder.make_decoder(html)
    form_node = root.find("form") or root
    form: dict = {}
    for item in form_node.find_all("input"):
        name = item.get("name")
        if not name or "answer" in name:
            continue
        form[name] = item.get("value", "")
    type_map = {
        "0": "single", "1": "multiple", "2": "completion",
        "3": "judgement", "4": "shortanswer",
    }
    questions: list[Question] = []
    for div in root.find_all("div"):
        if not div.has_class("singleQuesId"):
            continue
        qid = div.get("data", "")
        timu = div.find("div", "TiMu")
        code = timu.get("data", "0") if timu else "0"
        title_el = div.find("div", "Zy_TItle")
        title = re.sub(r"\s+", " ", title_el.text()).strip() if title_el else ""
        if decoder:
            title = decoder(title)
        options = []
        ul = div.find("ul")
        if ul:
            for li in ul.find_all("li"):
                text = li.get("aria-label") or li.text()
                text = re.sub(r"\s+", " ", text).strip()
                if decoder:
                    text = decoder(text)
                if text:
                    options.append(text)
        if qid:
            questions.append(Question(qid, code, type_map.get(code, "unknown"), title, options))
    return form, questions


# ---------------------------------------------------------------------------
# 考试解析
# ---------------------------------------------------------------------------
EXAM_TYPE_MAP = {
    "0": "single", "1": "multiple", "2": "completion", "3": "judgement",
    "4": "shortanswer", "5": "shortanswer", "6": "shortanswer",
    "7": "shortanswer", "8": "shortanswer", "9": "shortanswer",
    "10": "shortanswer", "11": "shortanswer", "13": "shortanswer",
    "14": "shortanswer", "15": "shortanswer", "18": "shortanswer",
    "19": "shortanswer", "20": "shortanswer", "21": "shortanswer",
}

EXAM_TYPE_NAMES = {
    "0": "单选题", "1": "多选题", "2": "填空题", "3": "判断题",
    "4": "简答题", "5": "名词解释", "6": "论述题", "7": "计算题",
    "8": "其它", "9": "分录题", "10": "资料题", "11": "连线题",
    "13": "排序题", "14": "完型填空", "15": "阅读理解", "18": "口语题",
    "19": "听力题", "20": "共用选项题", "21": "测评题",
}


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _input_value(root: Node, input_id: str) -> str:
    for node in root.find_all("input"):
        if node.get("id") == input_id:
            return node.get("value", "") or ""
    return ""


def _input_by_name(root: Node, name: str, prefix: bool = False) -> str:
    for node in root.find_all("input"):
        value = node.get("name", "") or ""
        if (value.startswith(name) if prefix else value == name):
            return node.get("value", "") or ""
    return ""


def _clean_exam_title(tit: Optional[Node]) -> str:
    if tit is None:
        return ""
    text = tit.text()
    heading = tit.find("h3")
    if heading is not None:
        text = text.replace(heading.text(), "", 1)
    text = re.sub(r"^\s*\d+\s*[.、．]\s*", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def parse_exam_list(html: str, course: Course) -> list[Exam]:
    root = parse_html(html)
    exams: list[Exam] = []
    seen = set()
    for ul in root.find_all("ul"):
        if "nav" not in ul.classes():
            continue
        for li in ul.find_all("li"):
            data = li.get("data") or ""
            if not data:
                continue
            query = urllib.parse.parse_qs(urllib.parse.urlparse(data).query or data)
            exam_id = (query.get("taskrefId") or query.get("examId") or [""])[0]
            if not exam_id or exam_id in seen:
                continue
            seen.add(exam_id)
            title_el = li.find("p")
            status_el = next((span for span in li.find_all("span") if not span.has_class("fr")), None)
            expire_el = li.find("span", "fr")
            exams.append(Exam(
                exam_id=str(exam_id),
                course_id=course.course_id,
                clazz_id=course.clazz_id,
                cpi=course.cpi,
                title=(title_el.text().strip() if title_el else f"考试 {exam_id}"),
                status=(status_el.text().strip() if status_el else "未交"),
                expire=(expire_el.text().strip() if expire_el else ""),
                enc_task=(query.get("enc_task") or [""])[0],
                course_title=course.title,
            ))
    return exams


def parse_exam_cover(html: str) -> dict:
    root = parse_html(html)
    title_el = root.find("span", "overHidden2")
    needcode = re.search(r"var\s+needcode\s*=\s*(\d+)", html or "")
    return {
        "title": title_el.text().strip() if title_el else "",
        "relation_id": _input_value(root, "testUserRelationId"),
        "monitor_enc": _input_value(root, "monitorEnc"),
        "need_face": _truthy(_input_value(root, "faceRecognitionCompare")),
        "need_captcha": _truthy(_input_value(root, "captchaCheck")),
        "need_code": bool(needcode and needcode.group(1) != "0"),
    }


def _parse_exam_question(node: Node) -> Optional[ExamQuestion]:
    qid = _input_by_name(node, "questionId")
    if not qid:
        return None
    type_code = ""
    for item in node.find_all("input"):
        name = item.get("name") or ""
        if name.startswith("type") and not name.startswith("typeName"):
            type_code = item.get("value", "") or ""
            break
    code = str(type_code or "0")
    title = _clean_exam_title(node.find("div", "tit"))
    options: list[tuple[str, str]] = []
    for opt in node.find_all("div"):
        if not (opt.has_class("answerList") and opt.has_class("radioList")):
            continue
        key = opt.get("name") or ""
        cc = opt.find("cc")
        text = cc.text() if cc else opt.text()
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            options.append((key or chr(65 + len(options)), text))
    blanks: list[str] = []
    for blank in node.find_all("div"):
        if not (blank.has_class("completionList") and blank.has_class("objectAuswerList")):
            continue
        label = blank.find("span", "grayTit")
        blanks.append(label.text().strip() if label else f"第{len(blanks) + 1}空")
    answer = ""
    for item in node.find_all("input"):
        input_id = item.get("id") or ""
        if input_id.startswith("answer"):
            answer = item.get("value", "") or ""
            break
    return ExamQuestion(qid, code, EXAM_TYPE_MAP.get(code, "shortanswer"),
                        title, options, blanks, answer)


def parse_exam_questions(html: str) -> tuple[dict, list[ExamQuestion]]:
    root = parse_html(html)
    form = {
        "enc": _input_value(root, "enc"),
        "enc_remain_time": _input_value(root, "encRemainTime"),
        "remain_time": _input_value(root, "remainTime"),
        "last_update_time": _input_value(root, "encLastUpdateTime"),
    }
    questions: list[ExamQuestion] = []
    for div in root.find_all("div"):
        if not (div.has_class("questionWrap") and div.has_class("singleQuesId")
                and div.has_class("ans-cc-exam")):
            continue
        question = _parse_exam_question(div)
        if question:
            questions.append(question)
    return form, questions


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


def _exam_error_text(html: str) -> str:
    """从封面 / 开始考试 / 试卷页里提取错误提示。"""
    root = parse_html(html)
    node = root.find("p", "blankTips") or root.find("li", "msg")
    if node is not None:
        return re.sub(r"\s+", " ", node.text()).strip()
    heading = root.find("h2")
    if heading is None:
        return ""
    text = re.sub(r"\s+", " ", heading.text()).strip()
    known = (
        "考试尚未开始", "章节任务点未完成", "指定的IP", "只允许在电脑", "无权限",
        "账号发生异常", "班级发生异常", "已经提交", "已提交", "时间已用完",
        "分钟内不允许", "验证码错误", "人脸识别", "考试已结束", "考试已完成",
    )
    return text if any(word in text for word in known) else ""


def _exam_answer_fields(question: ExamQuestion, raw) -> dict:
    """按题型拼出 reVersionSubmitTestNew 的答题表单。"""
    qid = question.id
    fields = {
        f"type{qid}": question.type_code,
        "questionId": qid,
        f"typeName{qid}": EXAM_TYPE_NAMES.get(question.type_code, "简答题"),
        "hidetext": "",
    }
    if question.type == "multiple":
        values = raw if isinstance(raw, (list, tuple)) else list(str(raw))
        fields[f"answers{qid}"] = "".join(str(value) for value in values)
    elif question.type == "completion":
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        blank_num = ""
        for index, value in enumerate(values, 1):
            fields[f"answer{qid}{index}"] = str(value)
            blank_num += f"{index},"
        fields[f"blankNum{qid}"] = blank_num
    elif question.type == "judgement":
        raw_text = str(raw).strip().lower()
        fields[f"answer{qid}"] = (
            "" if not raw_text
            else "true" if raw_text in {"true", "对", "正确", "1", "√"} else "false"
        )
    else:
        fields[f"answer{qid}"] = str(raw)
    return fields


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class Client:
    def __init__(self, timeout: int = 30, mooc1: str = MOOC1, mooc2: str = MOOC2,
                 passport: str = PASSPORT, sso: str = SSO,
                 exam_api: str = EXAM_API, exam_submit: str = EXAM_SUBMIT):
        self.timeout = timeout
        self.mooc1 = mooc1.rstrip("/")
        self.mooc2 = mooc2.rstrip("/")
        self.passport = passport.rstrip("/")
        self.sso = sso.rstrip("/")
        self.exam_api = exam_api.rstrip("/")
        self.exam_submit = exam_submit.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.uid = ""
        self.name = ""
        self.fid = "-1"
        self.imei = secrets.token_hex(16)

    # -- 基础 --------------------------------------------------------------
    def set_cookie_string(self, text: str) -> None:
        text = (text or "").strip()
        if text.lower().startswith("cookie:"):
            text = text[7:].strip()
        for chunk in re.split(r"[;\n]", text):
            chunk = chunk.strip()
            if not chunk or "=" not in chunk:
                continue
            name, value = chunk.split("=", 1)
            name, value = name.strip(), value.strip()
            if not name:
                continue
            cookie = http.cookiejar.Cookie(
                version=0, name=name, value=value, port=None, port_specified=False,
                domain=".chaoxing.com", domain_specified=True, domain_initial_dot=True,
                path="/", path_specified=True, secure=False, expires=None,
                discard=True, comment=None, comment_url=None, rest={}, rfc2109=False,
            )
            self.jar.set_cookie(cookie)

    def _sync_cookies(self) -> None:
        for cookie in self.jar:
            if cookie.name == "fid" and cookie.value:
                self.fid = cookie.value

    @staticmethod
    def _is_login_page(html: str) -> bool:
        return any(marker in (html or "") for marker in (
            "passport2.chaoxing.com", "用户登录", "请输入密码", "登录超时", "请先登录",
        ))

    def request_raw(self, method: str, url: str, params: Optional[dict] = None,
                    data: Optional[dict] = None, headers: Optional[dict] = None,
                    timeout: Optional[int] = None) -> tuple[int, bytes]:
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        body = urllib.parse.urlencode(data, doseq=True).encode("utf-8") if data is not None else None
        merged = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }
        if headers:
            merged.update(headers)
        req = urllib.request.Request(url, data=body, headers=merged, method=method)
        raw = b""
        code = 0
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = self.opener.open(req, timeout=timeout or self.timeout)
                raw = resp.read()
                code = resp.getcode() or 200
                break
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                code = exc.code
                if code in (502, 503, 504) and attempt < 2:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                break
            except urllib.error.URLError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise ChaoxingError(f"网络请求失败：{exc.reason}") from exc
        if last_error and code == 0:
            raise ChaoxingError(f"网络请求失败：{last_error.reason}")
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        self._sync_cookies()
        return code, raw

    def request(self, method: str, url: str, params: Optional[dict] = None,
                data: Optional[dict] = None, headers: Optional[dict] = None,
                timeout: Optional[int] = None) -> tuple[int, str]:
        code, raw = self.request_raw(method, url, params=params, data=data,
                                     headers=headers, timeout=timeout)
        return code, raw.decode("utf-8", "replace")

    # -- 扫码登录 ----------------------------------------------------------
    @staticmethod
    def _hidden_value(html: str, input_id: str) -> str:
        for pattern in (
            rf'id="{input_id}"[^>]*value="([^"]+)"',
            rf'value="([^"]+)"[^>]*id="{input_id}"',
        ):
            match = re.search(pattern, html or "")
            if match:
                return match.group(1)
        return ""

    def qr_start(self) -> bytes:
        """抓取登录页的 uuid/enc 并取回二维码图片（PNG/JPEG 原样返回）。"""
        code, html = self.request(
            "GET", f"{self.passport}/login?fid=-1&refer=https%3A%2F%2Fi.chaoxing.com"
        )
        if code != 200:
            raise ChaoxingError(f"登录页请求失败（HTTP {code}）")
        uuid = self._hidden_value(html, "uuid")
        enc = self._hidden_value(html, "enc")
        if not uuid or not enc:
            raise ChaoxingError("二维码参数解析失败，学习通登录页可能已更新")
        code, image = self.request_raw(
            "GET", f"{self.passport}/createqr", params={"uuid": uuid, "fid": "-1"}
        )
        if code != 200 or not image:
            raise ChaoxingError(f"二维码获取失败（HTTP {code}）")
        self.qr = {"uuid": uuid, "enc": enc, "created": time.time()}
        return image

    def qr_poll(self) -> dict:
        """轮询扫码状态：waiting / scanned / confirmed / cancelled / error。"""
        qr = getattr(self, "qr", None)
        if not qr:
            raise ChaoxingError("二维码会话已失效，请刷新")
        code, body = self.request(
            "POST", f"{self.passport}/getauthstatus/v2",
            data={
                "enc": qr["enc"],
                "uuid": qr["uuid"],
                "doubleFactorLogin": "0",
                "forbidotherlogin": "0",
            },
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.passport}/login?fid=-1&refer=https%3A%2F%2Fi.chaoxing.com",
                "Origin": self.passport,
            },
        )
        if code != 200:
            return {"state": "error", "message": f"状态请求失败（HTTP {code}）"}
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return {"state": "error", "message": "状态解析失败"}
        if data.get("status"):
            if self.check_login():
                return {"state": "confirmed", "name": self.name or self.uid}
            return {"state": "error", "message": "扫码成功但会话校验失败，请重试"}
        kind = str(data.get("type") or "")
        if kind == "4":
            return {"state": "scanned", "nickname": data.get("nickname") or ""}
        if kind == "6":
            return {"state": "cancelled", "message": "已在手机上取消登录"}
        if kind == "7":
            return {"state": "error", "message": data.get("mes") or "扫码登录失败"}
        return {"state": "waiting"}

    # -- 登录 --------------------------------------------------------------
    def login_with_password(self, username: str, password: str) -> None:
        data = {
            "fid": "-1",
            "uname": encrypt_login(username),
            "password": encrypt_login(password),
            "refer": "https%3A%2F%2Fi.chaoxing.com",
            "t": "true",
            "forbidotherlogin": "0",
            "validate": "",
            "doubleFactorLogin": "0",
            "independentId": "0",
        }
        code, body = self.request(
            "POST", f"{self.passport}/fanyalogin", data=data,
            headers={
                "Referer": f"{self.passport}/login?fid=&refer=https%3A%2F%2Fi.chaoxing.com",
                "Origin": self.passport,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LoginError(f"登录接口返回异常（HTTP {code}）") from exc
        if result.get("status") is not True:
            message = result.get("msg2") or result.get("msg") or "账号或密码错误"
            raise LoginError(str(message))
        if not self.check_login():
            raise LoginError("登录返回成功但会话校验失败，请改用 Cookie 登录")

    def check_login(self) -> bool:
        try:
            code, body = self.request("GET", f"{self.sso}/apis/login/userLogin4Uname.do")
            result = json.loads(body)
        except (ChaoxingError, json.JSONDecodeError):
            return False
        if result.get("result") != 1:
            return False
        msg = result.get("msg") or {}
        if not isinstance(msg, dict):
            return False
        self.uid = str(msg.get("puid") or "")
        self.name = str(msg.get("name") or "")
        return bool(self.uid)

    # -- 考试 --------------------------------------------------------------
    def _exam_headers(self) -> dict:
        return {
            "User-Agent": _mobile_ua(self.imei),
            "X-Requested-With": "com.chaoxing.mobile",
            "Referer": f"{self.mooc1}/",
            "Accept": "text/html,application/json,text/plain,*/*",
        }

    def _exam_no_redirect(self, url: str, params: Optional[dict] = None) -> tuple[int, str, dict]:
        """考试入口必须保留 302：进考试成功和“考试已完成”都靠它区分。"""
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        request = urllib.request.Request(url, headers=self._exam_headers(), method="GET")
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()
        )
        try:
            response = opener.open(request, timeout=self.timeout)
            code, raw, headers = response.getcode(), response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            code, raw, headers = exc.code, exc.read(), dict(exc.headers)
        except urllib.error.URLError as exc:
            raise ChaoxingError(f"网络请求失败：{exc.reason}") from exc
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        self._sync_cookies()
        return code, raw.decode("utf-8", "replace"), headers

    def get_exams(self, course: Course) -> list[Exam]:
        code, body = self.request(
            "GET", f"{self.exam_api}{EXAM_LIST_PATH}",
            params={
                "courseId": course.course_id,
                "classId": course.clazz_id,
                "cpi": course.cpi,
            },
            headers=self._exam_headers(),
        )
        if code != 200:
            raise ChaoxingError(f"考试列表请求失败（HTTP {code}）")
        if self._is_login_page(body):
            raise NotLoggedIn("登录已失效，请重新登录")
        return parse_exam_list(body, course)

    def exam_cover(self, course: Course, exam: Exam) -> dict:
        status, body, headers = self._exam_no_redirect(
            f"{self.exam_api}{EXAM_COVER_PATH}",
            {
                "redo": 1,
                "taskrefId": exam.exam_id,
                "courseId": course.course_id,
                "classId": course.clazz_id,
                "userId": self.uid,
                "role": "",
                "source": 0,
                "enc_task": exam.enc_task,
                "cpi": course.cpi,
                "vx": 0,
                "examsignal": 1,
            },
        )
        location = headers.get("Location") or headers.get("location") or ""
        if status in (301, 302, 303, 307, 308):
            if "/exam-ans/exam/phone/look" in location:
                raise ExamCompleted(f"考试已完成：{exam.title}")
            if "passport" in location:
                raise NotLoggedIn("登录已失效，请重新登录")
            raise ExamError(f"考试入口跳转异常：{location[:120]}")
        if status != 200:
            raise ChaoxingError(f"考试封面请求失败（HTTP {status}）")
        if self._is_login_page(body):
            raise NotLoggedIn("登录已失效，请重新登录")
        error = _exam_error_text(body)
        if error:
            raise ExamError(error)
        cover = parse_exam_cover(body)
        if not cover["relation_id"]:
            raise ExamError("考试封面解析失败（页面结构可能已更新）")
        cover["exam_id"] = exam.exam_id
        cover["title"] = cover["title"] or exam.title
        return cover

    def start_exam(self, course: Course, exam: Exam, code: str = "") -> ExamPaper:
        cover = self.exam_cover(course, exam)
        if cover["need_face"]:
            raise ExamBlocked("该考试要求人脸识别，本地无法处理，请在学习通客户端完成")
        if cover["need_captcha"]:
            raise ExamBlocked("该考试要求人机验证码，本地无法处理，请在学习通客户端完成")
        if cover["need_code"] and not code:
            raise ExamCodeRequired("该考试需要考试码，请填写后重试")
        status, body, headers = self._exam_no_redirect(
            f"{self.exam_api}{EXAM_START_PATH}",
            {
                "courseId": course.course_id,
                "classId": course.clazz_id,
                "examId": exam.exam_id,
                "source": 0,
                "examAnswerId": cover["relation_id"],
                "cpi": course.cpi,
                "keyboardDisplayRequiresUserAction": 1,
                "imei": self.imei,
                "faceDetection": 0,
                "facekey": "",
                "faceDetectionResult": "",
                "captchavalidate": "",
                "jt": 0,
                "code": code,
            },
        )
        if status in (301, 302, 303, 307, 308):
            location = headers.get("Location") or headers.get("location") or ""
            enc = (urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("enc") or [""])[0]
            if not enc:
                raise ExamError("开始考试成功但未拿到 enc 参数")
            return self._fetch_exam_paper(course, exam, cover, enc)
        if status == 200:
            if self._is_login_page(body):
                raise NotLoggedIn("登录已失效，请重新登录")
            error = _exam_error_text(body)
            if "验证码错误" in error:
                raise ExamCodeRequired("考试码错误，请重新输入")
            if "人脸" in error:
                raise ExamBlocked(error)
            raise ExamError(error or f"开始考试失败（HTTP {status}）")
        raise ChaoxingError(f"开始考试请求失败（HTTP {status}）")

    def _fetch_exam_paper(self, course: Course, exam: Exam, cover: dict, enc: str) -> ExamPaper:
        code, body = self.request(
            "GET", f"{self.exam_api}{EXAM_PREVIEW_PATH}",
            params={
                "courseId": course.course_id,
                "classId": course.clazz_id,
                "source": 0,
                "imei": self.imei,
                "start": 0,
                "cpi": course.cpi,
                "examRelationId": exam.exam_id,
                "examRelationAnswerId": cover["relation_id"],
                "monitorStatus": 0,
                "monitorOp": -1,
                "remainTimeParam": 0,
                "relationAnswerLastUpdateTime": 0,
                "enc": enc,
            },
            headers=self._exam_headers(),
        )
        if code != 200:
            raise ChaoxingError(f"试卷请求失败（HTTP {code}）")
        if self._is_login_page(body):
            raise NotLoggedIn("登录已失效，请重新登录")
        error = _exam_error_text(body)
        if error:
            raise ExamError(error)
        form, questions = parse_exam_questions(body)
        if not questions:
            raise ExamError("没有解析到考试题目，页面结构可能已更新")
        return ExamPaper(
            exam_id=exam.exam_id,
            course_id=course.course_id,
            clazz_id=course.clazz_id,
            cpi=course.cpi,
            title=cover["title"] or exam.title,
            relation_id=cover["relation_id"],
            monitor_enc=cover["monitor_enc"],
            questions=questions,
            enc=form["enc"] or enc,
            remain_time=_to_int(form["remain_time"]),
            enc_remain_time=_to_int(form["enc_remain_time"]),
            last_update_time=_to_int(form["last_update_time"]),
            need_face=cover["need_face"],
            need_captcha=cover["need_captcha"],
            need_code=cover["need_code"],
        )

    def _submit_exam_one(self, paper: ExamPaper, index: int, question: Optional[ExamQuestion],
                         raw, final: bool) -> dict:
        qid = question.id if question else ""
        uid = self.uid if str(self.uid).isdigit() else "0"
        signature = exam_signature(uid, qid, random.randint(100, 1000), random.randint(100, 1000))
        params = {
            "classId": paper.clazz_id,
            "courseId": paper.course_id,
            "cpi": paper.cpi,
            "testPaperId": paper.exam_id,
            "testUserRelationId": paper.relation_id,
            "tempSave": "false" if final else "true",
            **signature,
            "qid": qid,
            "version": 1,
        }
        data = {
            "courseId": paper.course_id,
            "testPaperId": paper.exam_id,
            "testUserRelationId": paper.relation_id,
            "classId": paper.clazz_id,
            "type": 0,
            "isphone": "true",
            "imei": self.imei,
            "subCount": "",
            "remainTime": paper.remain_time,
            "tempSave": "false" if final else "true",
            "timeOver": "false",
            "encRemainTime": paper.enc_remain_time,
            "encLastUpdateTime": paper.last_update_time,
            "enc": paper.enc,
            "userId": uid,
            "source": 0,
            "start": index,
            "enterPageTime": paper.last_update_time,
            "monitorforcesubmit": 0,
            "answeredView": 0,
            "exitdtime": 0,
        }
        if question is not None:
            data.update(_exam_answer_fields(question, raw))
        code, body = self.request(
            "POST", f"{self.exam_submit}{EXAM_SUBMIT_PATH}",
            params=params, data=data, headers=self._exam_headers(),
        )
        if code != 200:
            raise ChaoxingError(f"交卷请求失败（HTTP {code}）")
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ExamError(f"交卷响应解析失败：{body[:120]}") from exc
        if str(result.get("status")) != "success":
            raise ExamError(str(result.get("msg") or "交卷失败"))
        if not final:
            parts = str(result.get("data") or "").split("|")
            if len(parts) >= 3:
                paper.last_update_time = _to_int(parts[0])
                paper.enc_remain_time = _to_int(parts[1])
                paper.enc = parts[2]
        if question is not None:
            paper.saved_ids.add(question.id)
        return result

    def save_exam_answer(self, paper: ExamPaper, index: int,
                         question: ExamQuestion, raw) -> dict:
        """单题暂存，翻题/清空答案/自动保存都走这里。"""
        return self._submit_exam_one(paper, index, question, raw, final=False)

    def finalize_exam(self, paper: ExamPaper) -> dict:
        """只交卷，不重复提交每道题。"""
        result = self._submit_exam_one(paper, 0, None, None, final=True)
        return {
            "saved": len(paper.saved_ids),
            "count": len(paper.questions),
            "submitted": True,
            "message": str(result.get("msg") or "已交卷"),
        }

    def submit_exam(self, paper: ExamPaper, answers: dict) -> dict:
        for index, question in enumerate(paper.questions):
            raw = answers.get(question.id)
            empty = raw is None or raw == "" or raw == []
            if empty and question.id not in paper.saved_ids:
                continue
            self._submit_exam_one(paper, index, question, raw, final=False)
        return self.finalize_exam(paper)

    # -- 课程 / 章节 / 任务点 ----------------------------------------------
    def get_courses(self) -> list[Course]:
        code, body = self.request(
            "POST", f"{self.mooc2}/mooc2-ans/visit/courselistdata",
            data={"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0},
            headers={"Referer": f"{self.mooc2}/mooc2-ans/visit/interaction"
                                "?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans"},
        )
        if code != 200:
            raise ChaoxingError(f"课程列表请求失败（HTTP {code}）")
        courses = parse_courses(body)
        if not courses:
            if self._is_login_page(body):
                raise NotLoggedIn("登录已失效，请重新登录")
            raise ChaoxingError("课程列表为空：可能是 Cookie 失效或当前学期没有课程")
        try:
            code2, body2 = self.request("GET", f"{self.mooc2}/mooc2-ans/visit/interaction")
            if code2 == 200:
                for folder_id, _name in parse_folders(body2):
                    code3, body3 = self.request(
                        "POST", f"{self.mooc2}/mooc2-ans/visit/courselistdata",
                        data={"courseType": 1, "courseFolderId": folder_id, "query": "", "superstarClass": 0},
                        headers={"Referer": f"{self.mooc2}/mooc2-ans/visit/interaction"},
                    )
                    if code3 == 200:
                        courses.extend(parse_courses(body3))
        except ChaoxingError:
            pass
        seen = set()
        unique = []
        for course in courses:
            key = (course.course_id, course.clazz_id, course.cpi)
            if key in seen:
                continue
            seen.add(key)
            unique.append(course)
        return unique

    def get_structure(self, course: Course) -> tuple[int, list[Point], int, int]:
        url = (f"{self.mooc2}/mooc2-ans/mycourse/studentcourse"
               f"?courseid={course.course_id}&clazzid={course.clazz_id}"
               f"&cpi={course.cpi}&ut=s")
        code, body = self.request("GET", url)
        if code != 200:
            raise ChaoxingError(f"章节列表请求失败（HTTP {code}）")
        if self._is_login_page(body):
            raise NotLoggedIn("登录已失效，请重新登录")
        return parse_structure(body)

    def get_points(self, course: Course) -> list[Point]:
        return self.get_structure(course)[1]
    def get_jobs(self, course: Course, point: Point) -> tuple[list[Job], dict]:
        jobs: list[Job] = []
        info: dict = {}
        seen = set()
        for num in range(0, 10):
            code, body = self.request(
                "GET", f"{self.mooc1}/mooc-ans/knowledge/cards",
                params={
                    "clazzid": course.clazz_id,
                    "courseid": course.course_id,
                    "knowledgeid": point.id,
                    "ut": "s",
                    "cpi": course.cpi,
                    "v": "2025-0424-1038-3",
                    "mooc2": 1,
                    "num": num,
                },
            )
            if code != 200:
                raise ChaoxingError(f"任务点请求失败（HTTP {code}）")
            if "章节未开放" in body:
                return [], {"notOpen": True}
            data = extract_marg(body)
            if not data:
                if num == 0:
                    if self._is_login_page(body):
                        raise NotLoggedIn("登录已失效，请重新登录")
                    raise ChaoxingError("任务点数据解析失败（页面结构可能已更新）")
                break
            info.update(data.get("defaults") or {})
            attachments = data.get("attachments") or []
            if not attachments:
                break
            added = 0
            for card in attachments:
                job = _job_from_card(card)
                if not job or job.jobid in seen:
                    continue
                seen.add(job.jobid)
                jobs.append(job)
                added += 1
            if added == 0 and num > 0:
                break
        return jobs, info

    # -- 视频 --------------------------------------------------------------
    def video_status(self, objectid: str) -> dict:
        try:
            code, body = self.request(
                "GET", f"{self.mooc1}/ananas/status/{objectid}",
                params={"k": self.fid or "-1", "flag": "normal"},
                headers={"Referer": VIDEO_REFERER},
            )
            return json.loads(body) if code == 200 else {}
        except (ChaoxingError, json.JSONDecodeError):
            return {}

    def report_video(self, course: Course, job: Job, dtoken: str, duration: int,
                     playing: float, dtype: str = "Video") -> tuple[bool, int]:
        playing_int = int(max(0, playing))
        duration_int = int(max(0, duration))
        enc = hashlib.md5(
            f"[{course.clazz_id}][{self.uid}][{job.jobid}][{job.objectid}]"
            f"[{playing_int * 1000}][d_yHJ!$pdA~5][{duration_int * 1000}][0_{duration_int}]".encode()
        ).hexdigest()
        params = {
            "clazzId": course.clazz_id,
            "playingTime": playing_int,
            "duration": duration_int,
            "clipTime": f"0_{duration_int}",
            "objectId": job.objectid,
            "otherInfo": job.otherinfo,
            "courseId": course.course_id,
            "jobid": job.jobid,
            "userid": self.uid,
            "isdrag": "3",
            "view": "pc",
            "enc": enc,
            "dtype": dtype,
        }
        if job.face_enc:
            params["videoFaceCaptureEnc"] = job.face_enc
        if job.att_duration:
            params["attDuration"] = job.att_duration
        if job.att_duration_enc:
            params["attDurationEnc"] = job.att_duration_enc

        rt = job.rt
        if not rt:
            match = re.search(r"-rt_([1d])", job.otherinfo)
            rt = "0.9" if match and match.group(1) == "d" else "1"
        params["rt"] = rt
        params["_t"] = str(int(time.time() * 1000))

        code, body = self.request(
            "GET", f"{self.mooc1}/mooc-ans/multimedia/log/a/{course.cpi}/{dtoken}",
            params=params, headers={"Referer": VIDEO_REFERER},
        )
        if code == 200:
            try:
                result = json.loads(body)
                return bool(result.get("isPassed")), code
            except json.JSONDecodeError:
                return False, code
        return False, code

    # -- 文档 / 阅读 -------------------------------------------------------
    def _study_ok(self, code: int, body: str) -> bool:
        """文档/阅读接口不能只看 HTTP 200：登录页或 status:false 也要算失败。"""
        if code != 200 or self._is_login_page(body):
            return False
        text = (body or "").strip()
        if not text:
            return True
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return False
        if not isinstance(data, dict):
            return False
        for key in ("status", "isPassed", "success"):
            if key in data:
                return _as_bool(data[key])
        return True

    def study_document(self, course: Course, job: Job, info: dict) -> bool:
        knowledgeid = str(info.get("knowledgeid") or "")
        if not knowledgeid:
            match = re.search(r"nodeId_(\d+)", job.otherinfo)
            knowledgeid = match.group(1) if match else ""
        code, body = self.request(
            "GET", f"{self.mooc1}/ananas/job/document",
            params={
                "jobid": job.jobid,
                "knowledgeid": knowledgeid,
                "courseid": course.course_id,
                "clazzid": course.clazz_id,
                "jtoken": job.jtoken,
                "_dc": str(int(time.time() * 1000)),
            },
        )
        return self._study_ok(code, body)

    def study_read(self, course: Course, job: Job, info: dict) -> bool:
        knowledgeid = str(info.get("knowledgeid") or "")
        if not knowledgeid:
            match = re.search(r"nodeId_(\d+)", job.otherinfo)
            knowledgeid = match.group(1) if match else ""
        code, body = self.request(
            "GET", f"{self.mooc1}/ananas/job/readv2",
            params={
                "jobid": job.jobid,
                "knowledgeid": knowledgeid,
                "jtoken": job.jtoken,
                "courseid": course.course_id,
                "clazzid": course.clazz_id,
            },
        )
        return self._study_ok(code, body)

    def get_quiz(self, course: Course, job: Job, info: dict) -> Quiz:
        params = {
            "api": "1",
            "workId": job.jobid.replace("work-", ""),
            "jobid": job.jobid,
            "originJobId": job.jobid,
            "needRedirect": "true",
            "skipHeader": "true",
            "knowledgeid": str(info.get("knowledgeid") or ""),
            "ktoken": info.get("ktoken", ""),
            "cpi": info.get("cpi", ""),
            "ut": "s",
            "clazzId": course.clazz_id,
            "type": "",
            "enc": job.enc,
            "mooc2": "1",
            "courseid": course.course_id,
        }
        code, html = self.request(
            "GET", f"{self.mooc1}/mooc-ans/api/work", params=params,
            headers={"Referer": f"{self.mooc1}/"},
        )
        if code != 200:
            raise ChaoxingError(f"测验页请求失败（HTTP {code}）")
        if "教师未创建完成该测验" in html:
            raise ChaoxingError("教师未创建完成该测验")
        decoder = font_decoder.make_decoder(html)
        form, questions = parse_quiz_html(html, decoder)
        if not questions:
            raise ChaoxingError("没有解析到题目，页面结构可能已更新")
        scores = re.findall(r'class="scoreNum"[^>]*>\s*([0-9.]+)', html)
        score = round(sum(float(item) for item in scores), 1) if scores else None
        return Quiz(
            form, questions,
            encrypted=font_decoder.has_encryption(html),
            decoded=bool(decoder),
            completed="testTit_status_complete" in html,
            score=score,
            course_id=course.course_id,
            clazz_id=course.clazz_id,
            cpi=str(info.get("cpi") or ""),
        )

    def is_work_completed(self, course: Course, job: Job, info: dict) -> bool:
        params = {
            "api": "1",
            "workId": job.jobid.replace("work-", ""),
            "jobid": job.jobid,
            "originJobId": job.jobid,
            "needRedirect": "true",
            "skipHeader": "true",
            "knowledgeid": str(info.get("knowledgeid") or ""),
            "ktoken": info.get("ktoken", ""),
            "cpi": info.get("cpi", ""),
            "ut": "s",
            "clazzId": course.clazz_id,
            "type": "",
            "enc": job.enc,
            "mooc2": "1",
            "courseid": course.course_id,
        }
        code, html = self.request(
            "GET", f"{self.mooc1}/mooc-ans/api/work", params=params,
            headers={"Referer": f"{self.mooc1}/"},
        )
        return code == 200 and "testTit_status_complete" in html

    def submit_quiz(self, quiz: Quiz, answers: dict[str, str], save_only: bool = False) -> tuple[bool, str]:
        if quiz.completed:
            return False, "该测验已完成，不能重复提交"
        data = dict(quiz.form)
        data["courseId"] = quiz.course_id
        data["courseid"] = quiz.course_id
        data["classId"] = quiz.clazz_id
        data["clazzid"] = quiz.clazz_id
        if quiz.cpi:
            data["cpi"] = quiz.cpi
        for question in quiz.questions:
            data[f"answer{question.id}"] = answers.get(question.id, "")
            data[f"answertype{question.id}"] = question.type_code
        data["answerwqbid"] = ",".join(question.id for question in quiz.questions) + ","
        data["pyFlag"] = "1" if save_only else ""
        code, body = self.request(
            "POST", f"{self.mooc1}/mooc-ans/work/addStudentWorkNew", data=data,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Origin": self.mooc1,
                "Referer": f"{self.mooc1}/",
            },
        )
        try:
            result = json.loads(body)
            return bool(result.get("status")), str(result.get("msg") or "")
        except json.JSONDecodeError:
            return False, body[:200]


# ---------------------------------------------------------------------------
# 刷课执行器
# ---------------------------------------------------------------------------
@dataclass
class Options:
    speed: float = 1.5
    skip_finished: bool = True
    do_document: bool = True
    do_read: bool = True
    instant: bool = True
    max_retry: int = 10

    @classmethod
    def from_dict(cls, data: dict) -> "Options":
        def num(key, default, low, high):
            try:
                value = float(data.get(key, default))
            except (TypeError, ValueError):
                value = default
            return max(low, min(high, value))

        return cls(
            speed=num("speed", 1.5, 1.0, 5.0),
            skip_finished=bool(data.get("skip_finished", True)),
            do_document=bool(data.get("do_document", True)),
            do_read=bool(data.get("do_read", True)),
            instant=bool(data.get("instant", True)),
        )


class Runner:
    """按章节顺序执行任务点，视频进度按真实节奏上报。"""

    def __init__(self, client: Client, options: Options, stop_event: threading.Event,
                 emit: Callable[[str, str], None],
                 progress: Callable[[int, str], None],
                 on_stats: Optional[Callable[[dict], None]] = None):
        self.client = client
        self.options = options
        self.stop_event = stop_event
        self.emit = emit
        self.progress = progress
        self.on_stats = on_stats
        self.last_percent = 0
        self.stats = {"done": 0, "failed": 0, "skipped": 0, "chapters": 0}
        self.deferred: list[tuple[Course, Point, Job, dict]] = []

    # -- 工具 --------------------------------------------------------------
    def _log(self, level: str, text: str) -> None:
        self.emit(level, text)

    def _report_progress(self, percent: float, text: str) -> None:
        value = round(float(percent), 2)
        self.last_percent = value
        self.progress(value, text)

    def _notify_stats(self) -> None:
        if self.on_stats:
            self.on_stats(dict(self.stats))

    def _skip(self) -> None:
        self.stats["skipped"] += 1
        self._notify_stats()

    def _pause(self, seconds: float) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            if self.stop_event.is_set():
                return False
            time.sleep(min(0.2, max(0.0, end - time.time())))
        return True

    # -- 主流程 ------------------------------------------------------------
    def run(self, courses: list[Course]) -> dict:
        started = time.time()
        self._log("info", f"准备刷 {len(courses)} 门课程，倍速 {self.options.speed:g}×")
        plans: list[tuple[Course, list[Point]]] = []
        for course in courses:
            if self.stop_event.is_set():
                break
            try:
                points = self.client.get_points(course)
            except NotLoggedIn:
                raise
            except ChaoxingError as exc:
                self._log("error", f"读取章节失败：{course.title} — {exc}")
                continue
            plans.append((course, points))
            self._log("info", f"读取章节：{course.title}（{len(points)} 节）")

        total = sum(len(points) for _, points in plans) or 1
        completed = 0
        for course, points in plans:
            if self.stop_event.is_set():
                break
            self._log("info", f"━━ 课程：{course.title}")
            for point in points:
                if self.stop_event.is_set():
                    break
                self._report_progress(round(completed / total * 100, 2), f"{course.title} · {point.title}")
                completed += 1
                base_percent = (completed - 1) / total * 100
                if self.options.skip_finished and point.finished:
                    self._skip()
                    self._log("dim", f"  跳过已完成：{point.title}")
                    continue
                if point.locked:
                    self._log("warn", f"  章节未解锁：{point.title}")
                    continue
                try:
                    jobs, info = self.client.get_jobs(course, point)
                except NotLoggedIn:
                    raise
                except ChaoxingError as exc:
                    self._log("error", f"  读取任务点失败：{point.title} — {exc}")
                    self.stats["failed"] += 1
                    self._notify_stats()
                    continue
                if info.get("notOpen"):
                    self._log("warn", f"  章节未开放：{point.title}")
                    continue
                pending = [job for job in jobs if not job.is_passed]
                if not pending:
                    self._log("ok", f"  ✔ 已完成：{point.title}")
                    continue
                self._log("info", f"  章节：{point.title}（待完成 {len(pending)}）")
                for index, job in enumerate(pending):
                    if self.stop_event.is_set():
                        break
                    step = base_percent + (index / max(1, len(pending))) * (100 / total)
                    self._report_progress(round(step, 2), f"{course.title} · {point.title}")
                    self._handle_job(course, point, job, info)
                self.stats["chapters"] += 1

        if self.deferred:
            self._log("info", f"开始处理排队的 {len(self.deferred)} 个章节测验")
            for course, point, job, _info in self.deferred:
                if self.stop_event.is_set():
                    break
                self._log("warn", f"  章节测验暂未自动作答，请稍后手动完成：{course.title} · {point.title}")
                self._skip()

        summary = {
            "done": self.stats["done"],
            "failed": self.stats["failed"],
            "skipped": self.stats["skipped"],
            "chapters": self.stats["chapters"],
            "elapsed": round(time.time() - started),
            "stopped": self.stop_event.is_set(),
        }
        stopped = self.stop_event.is_set()
        self._report_progress(100.0 if not stopped else self.last_percent,
                              "已停止" if stopped else "已结束")
        self._log(
            "info",
            f"结束：完成 {summary['done']}，失败 {summary['failed']}，"
            f"跳过章节 {summary['skipped']}，用时 {summary['elapsed']} 秒",
        )
        return summary

    def _handle_job(self, course: Course, point: Point, job: Job, info: dict) -> None:
        label = job.name or job.jobid
        if job.type == "video":
            ok = self._study_video(course, job)
            self._count(ok)
        elif job.type == "document":
            if not self.options.do_document:
                self._log("dim", f"  - 跳过文档：{label}")
                self._skip()
                return
            self._log("info", f"  ▶ 文档：{label}")
            ok = self.client.study_document(course, job, info)
            self._log("ok" if ok else "error", f"     {'✔ 完成' if ok else '✘ 失败'}：{label}")
            self._count(ok)
        elif job.type == "read":
            if not self.options.do_read:
                self._log("dim", f"  - 跳过阅读：{label}")
                self._skip()
                return
            self._log("info", f"  ▶ 阅读：{label}")
            ok = self.client.study_read(course, job, info)
            self._log("ok" if ok else "error", f"     {'✔ 完成' if ok else '✘ 失败'}：{label}")
            self._count(ok)
        elif job.type in {"workid", "work"}:
            self.deferred.append((course, point, job, info))
            self._log("warn", f"  - 章节测验已排队，放到最后统一处理：{label}")
        elif job.type == "live":
            self._log("warn", f"  - 跳过直播任务：{label}")
            self._skip()
        elif job.type == "audio":
            self._log("warn", f"  - 暂不支持纯音频任务：{label}")
            self._skip()
        else:
            self._log("warn", f"  - 未知任务类型 {job.type}：{label}")
            self._skip()

    def _count(self, ok: bool) -> None:
        if ok:
            self.stats["done"] += 1
        else:
            self.stats["failed"] += 1
        self._notify_stats()

    def _study_video(self, course: Course, job: Job) -> bool:
        name = job.name or job.objectid
        self._log("info", f"  ▶ 视频：{name}")
        status = self.client.video_status(job.objectid)
        if status.get("status") != "success" or not status.get("dtoken"):
            self._log("error", f"     视频信息获取失败（可能已下架）：{name}")
            return False
        dtoken = str(status["dtoken"])
        try:
            duration = int(float(status.get("duration") or 0))
        except (TypeError, ValueError):
            duration = 0
        if duration <= 0:
            self._log("error", f"     视频时长为 0，跳过：{name}")
            return False

        play = min(float(job.playtime_ms) / 1000.0, float(duration))
        if play > 0:
            self._log("dim", f"     已看 {int(play)}s / {duration}s，继续")

        if self.options.instant:
            self.client.report_video(course, job, dtoken, duration, play)
            passed, code = self.client.report_video(course, job, dtoken, duration, duration)
            if passed:
                self._log("ok", f"     ⚡ 极速完成：{name}")
                return True
            if code == 403:
                self._log("dim", "     极速上报被拒，走正常节奏")

        last_tick = time.time()
        last_report = play
        wait = random.randint(30, 90)
        end_retries = 0
        forbidden = 0
        last_bucket = -1
        while not self.stop_event.is_set():
            now = time.time()
            play = min(float(duration), play + (now - last_tick) * self.options.speed)
            last_tick = now
            bucket = min(9, int(play / duration * 10))
            if bucket != last_bucket and play < duration:
                last_bucket = bucket
                self._log("dim", f"     {name} {int(play / duration * 100)}%（{int(play)}/{duration}s）")

            if play - last_report >= wait or play >= duration:
                if play >= duration:
                    end_retries += 1
                    if end_retries > self.options.max_retry:
                        self._log("warn", f"     服务器未确认完成，跳过（可能带弹题/人脸校验）：{name}")
                        return False
                    if not self._pause(random.uniform(2.5, 6.0)):
                        return False
                    if end_retries % 5 == 0:
                        refreshed = self.client.video_status(job.objectid)
                        if refreshed.get("dtoken"):
                            dtoken = str(refreshed["dtoken"])
                passed, code = self.client.report_video(course, job, dtoken, duration, play)
                if code == 403:
                    forbidden += 1
                    if forbidden > 2:
                        self._log("error", f"     上报被拒（403），放弃：{name}")
                        return False
                    self._pause(2.0)
                    refreshed = self.client.video_status(job.objectid)
                    if refreshed.get("dtoken"):
                        dtoken = str(refreshed["dtoken"])
                    continue
                last_report = play
                wait = random.randint(30, 90)
                if passed:
                    self._log("ok", f"     ✔ 完成：{name}")
                    return True
            if not self._pause(1.0):
                return False
        return False


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def _selftest() -> None:
    import crypto

    crypto._selftest()

    course_html = """
    <div class="course" id="course_1" info="x" roleid="3">
      <div class="course-cover">
        <a href="/mycourse/studentcourse?courseid=111&clazzid=222&cpi=333&ut=s"><img src="x.png"></a>
      </div>
      <div class="course-info">
        <h3><span class="course-name" title="高等数学">高等数学</span></h3>
        <p class="margint10" title="2024-2025-1">2024-2025-1</p>
        <p class="color3" title="张老师">张老师</p>
      </div>
      <input type="hidden" class="clazzId" value="222">
      <input type="hidden" class="courseId" value="111">
    </div>
    """
    courses = parse_courses(course_html)
    assert len(courses) == 1, courses
    assert courses[0].title == "高等数学"
    assert courses[0].teacher == "张老师"
    assert courses[0].cpi == "333"
    assert courses[0].clazz_id == "222"
    assert courses[0].course_id == "111"

    point_html = """
    <div class="chapter_unit">
      <li>
        <div id="cur1001" class="chapter_item">
          <a class="clicktitle" href="#">第一章 函数</a>
          <input type="hidden" class="knowledgeJobCount" value="3">
          <span class="bntHoverTips">已完成</span>
        </div>
        <div id="cur1002" class="chapter_item">
          <a class="clicktitle" href="#">第二章 极限</a>
          <span class="bntHoverTips">解锁</span>
        </div>
      </li>
    </div>
    """
    points = parse_points(point_html)
    assert len(points) == 2, points
    assert points[0].id == "1001" and points[0].finished and points[0].job_count == 3
    assert points[1].id == "1002" and points[1].locked and not points[1].finished

    marg_html = """
    <script>
      window.mArg = {"defaults":{"knowledgeid":"1001","cpi":"333"},
        "attachments":[{"jobid":"job-1","type":"video","objectId":"abc",
        "otherInfo":"nodeId_1001-cpi_333","playTime":0,
        "property":{"name":"a};b 视频","rt":"0.9"},"isPassed":false}]};
    </script>
    """
    data = extract_marg(marg_html)
    assert data and data["defaults"]["knowledgeid"] == "1001"
    job = _job_from_card(data["attachments"][0])
    assert job and job.jobid == "job-1" and job.type == "video"
    assert job.name == "a};b 视频" and job.objectid == "abc"

    exam_html = """
    <ul class="nav">
      <li data="/exam?taskrefId=7001&amp;enc_task=etok">
        <p>期末考试</p><span>未交</span><span class="fr">剩余 1 天</span>
      </li>
    </ul>
    """
    course = Course("c1", "cl1", "cpi1", "测试课程")
    exams = parse_exam_list(exam_html, course)
    assert len(exams) == 1, exams
    assert exams[0].exam_id == "7001" and exams[0].enc_task == "etok"
    assert exams[0].status == "未交" and exams[0].expire == "剩余 1 天"

    cover = parse_exam_cover(
        '<input id="testUserRelationId" value="88">'
        '<input id="faceRecognitionCompare" value="1">'
        '<script>var needcode = 1;</script>'
    )
    assert cover["relation_id"] == "88" and cover["need_face"] and cover["need_code"]

    paper_html = """
    <form id="submitTest">
      <input id="enc" value="e1"><input id="encRemainTime" value="60">
      <input id="remainTime" value="60"><input id="encLastUpdateTime" value="1">
      <div class="questionWrap singleQuesId ans-cc-exam allAnswerList">
        <input name="questionId" value="9001"><input name="type9001" value="0">
        <div class="tit"><h3>单选题（5.0分）</h3>1. 测试题干</div>
        <div class="answerList radioList" name="A"><cc>A. 甲</cc></div>
      </div>
      <div class="questionWrap singleQuesId ans-cc-exam allAnswerList">
        <input name="questionId" value="9002"><input name="type9002" value="2">
        <div class="tit"><h3>填空题（5.0分）</h3>2. 填空题干</div>
        <div class="completionList objectAuswerList">
          <span class="grayTit">第1空</span><textarea class="blanktextarea"></textarea>
        </div>
      </div>
    </form>
    """
    form, questions = parse_exam_questions(paper_html)
    assert form["enc"] == "e1" and form["enc_remain_time"] == "60"
    assert len(questions) == 2, questions
    assert questions[0].type == "single" and questions[0].options[0] == ("A", "A. 甲")
    assert questions[1].type == "completion" and questions[1].blanks == ["第1空"]

    print("chaoxing selftest OK")


if __name__ == "__main__":
    _selftest()
