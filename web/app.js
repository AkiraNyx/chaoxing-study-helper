import { animate, stagger, createTimeline } from "./vendor/anime.esm.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const PREF_KEY = "chaoxing-helper-prefs-v1";
const THEME_KEY = "cx-theme";

const state = {
  courses: [],
  selected: new Set(),
  user: "",
  running: false,
  since: 0,
  speed: 1.5,
  percentShown: 0,
  stats: { done: 0, failed: 0 },
  mode: "password",
  pollTimer: null,
  qrActive: false,
  qrTimer: null,
  maximized: false,
  theme: "auto",
  view: "brush",
  quizItems: [],
  quizFilter: "all",
  quizQuery: "",
  aiEffort: "",
  chapterCache: new Map(),
  chapterCounts: new Map(),
  chapterUnits: new Map(),
  chapterDone: new Map(),
  chapterTotal: new Map(),
  courseGeneration: 0,
  quizLoaded: false,
};

const ICONS = { ok: "check", warn: "triangle-alert", error: "x", info: "info" };

/* ------------------------------------------------------------------ 深浅色 */
function restoreTheme() {
  let saved = "auto";
  try {
    saved = localStorage.getItem(THEME_KEY) || "auto";
  } catch {
    saved = "auto";
  }
  state.theme = ["auto", "light", "dark"].includes(saved) ? saved : "auto";
  applyTheme();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (state.theme === "auto") applyTheme();
  });
}

function applyTheme() {
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const actual = state.theme === "auto" ? (dark ? "dark" : "light") : state.theme;
  document.documentElement.dataset.theme = actual;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", actual === "dark" ? "#1a1917" : "#f0eee6");
  const icon = state.theme === "auto" ? "monitor" : state.theme === "dark" ? "moon" : "sun";
  const label = state.theme === "auto" ? "跟随系统" : state.theme === "dark" ? "深色" : "浅色";
  const button = $("#themeBtn");
  if (button) {
    button.innerHTML = `<i data-lucide="${icon}"></i>`;
    button.title = `外观：${label}（点击切换）`;
    button.setAttribute("aria-label", `外观：${label}`);
    renderIcons();
  }
}

function cycleTheme() {
  state.theme = state.theme === "auto" ? "light" : state.theme === "light" ? "dark" : "auto";
  try {
    localStorage.setItem(THEME_KEY, state.theme);
  } catch {
    /* 忽略存储异常 */
  }
  applyTheme();
}

/* ------------------------------------------------------------------ 分页 / AI 设置 */
function setView(name) {
  state.view = name;
  $$(".side-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
  ["brush", "quiz", "ai", "settings", "about"].forEach((view) => {
    const section = $(`#view-${view}`);
    if (section) section.hidden = view !== name;
  });
  if (name === "quiz" && !state.quizLoaded) loadQuizzes();
  if (name === "settings") loadAutoLogin();
}

function renderQuizList(quizzes) {
  const host = $("#quizList");
  state.quizItems = quizzes;
  host.innerHTML = "";
  if (!quizzes.length) {
    host.innerHTML = '<div class="chapter-note">没有读取到章节测验。</div>';
    return;
  }
  quizzes.forEach((quiz) => {
    const row = document.createElement("div");
    row.className = "chapter-item" + (quiz.passed ? " finished" : "");
    row.dataset.jobid = quiz.jobid;
    const dot = document.createElement("span");
    dot.className = "dot";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = `${quiz.course} · ${quiz.chapter} · ${quiz.name}`;
    const pill = document.createElement("span");
    pill.className = "pill";
    pill.textContent = quiz.passed ? "已完成" : "待作答";
    row.append(dot, name, pill);
    const action = document.createElement("button");
    action.type = "button";
    action.className = "btn tiny ghost quiz-solve";
    action.textContent = quiz.passed ? "回顾" : "作答";
    action.addEventListener("click", () => openQuiz(quiz));
    row.appendChild(action);
    row.dataset.passed = quiz.passed ? "1" : "0";
    row.dataset.search = `${quiz.course} ${quiz.chapter} ${quiz.name}`.toLowerCase();
    host.appendChild(row);
  });
  applyQuizFilter();
}

function applyQuizFilter() {
  const query = state.quizQuery.toLowerCase();
  $$("#quizList .chapter-item").forEach((row) => {
    const passed = row.dataset.passed === "1";
    const filterOk = state.quizFilter === "all"
      || (state.quizFilter === "done" && passed)
      || (state.quizFilter === "pending" && !passed);
    const queryOk = !query || (row.dataset.search || "").includes(query);
    row.hidden = !(filterOk && queryOk);
  });
  const visible = $$("#quizList .chapter-item:not([hidden])");
  if (visible.length && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    animate(visible, {
      opacity: [0, 1],
      y: [6, 0],
      duration: 240,
      delay: stagger(16),
      ease: "out(2)",
    });
  }
}

let quizScanSince = 0;

async function loadQuizzes(force = false) {
  const host = $("#quizList");
  if (!state.selected.size) {
    state.quizLoaded = false;
    $("#quizScan").hidden = true;
    host.hidden = false;
    host.innerHTML = '<div class="chapter-note">先在「刷课」页勾选课程，再回到这里刷新测验。</div>';
    return;
  }
  $("#quizScan").hidden = false;
  host.hidden = true;
  $("#quizConsole").innerHTML = '<div class="console-empty" id="quizConsoleEmpty">等待扫描日志…</div>';
  try {
    await api("/api/quizzes/scan", postJson({
      indices: [...state.selected],
      force: Boolean(force),
    }));
    quizScanSince = 0;
    pollQuizScan();
  } catch (error) {
    $("#quizScan").hidden = true;
    host.hidden = false;
    state.quizLoaded = false;
    toast(error.message, "error");
  }
}

async function pollQuizScan() {
  try {
    const data = await api(`/api/quiz/scan/status?since=${quizScanSince}`);
    if (data.logs && data.logs.length) {
      quizScanSince = data.logs[data.logs.length - 1].seq;
      appendQuizLogs(data.logs);
    }
    const percent = Math.max(0, Math.min(100, Number(data.percent) || 0));
    $("#quizScanPct").textContent = `${percent.toFixed(2)}%`;
    $("#quizScanBar").style.width = `${percent}%`;
    $("#quizScanCurrent").textContent = data.current || "";
    if (data.running) {
      window.setTimeout(pollQuizScan, 600);
      return;
    }
    $("#quizScan").hidden = true;
    $("#quizList").hidden = false;
    const result = data.result || {};
    state.quizLoaded = true;
    renderQuizList(result.quizzes || []);
    updateOverallProgress();
    if (result.error) toast(result.error, "error");
  } catch (error) {
    $("#quizScan").hidden = true;
    $("#quizList").hidden = false;
    toast(error.message, "error");
  }
}

let quizSince = 0;
let quizPollTimer = null;
let quizWasRunning = false;

function setQuizProgress(percent, text) {
  const value = Math.round(Math.max(0, Math.min(100, Number(percent) || 0)) * 100) / 100;
  $("#quizPct").textContent = `${value.toFixed(2)}%`;
  $("#quizBar").style.width = `${value}%`;
  if (text) {
    $("#quizPhase").textContent = quizWasRunning ? "作答中" : "待命";
    $("#quizCurrent").textContent = text;
  }
}

function appendQuizLogs(logs) {
  const host = $("#quizConsole");
  const empty = $("#quizConsoleEmpty");
  if (empty) empty.remove();
  const fragment = document.createDocumentFragment();
  logs.forEach((log) => {
    const line = document.createElement("div");
    line.className = `line ${log.level || "info"}`;
    const time = document.createElement("span");
    time.className = "t";
    time.textContent = log.time || "";
    const message = document.createElement("span");
    message.className = "m";
    message.textContent = log.text || "";
    line.append(time, message);
    fragment.appendChild(line);
  });
  host.appendChild(fragment);
  host.scrollTop = host.scrollHeight;
}

async function pollQuiz() {
  try {
    const data = await api(`/api/quiz/status?since=${quizSince}`);
    if (data.logs && data.logs.length) {
      quizSince = data.logs[data.logs.length - 1].seq;
      appendQuizLogs(data.logs);
    }
    setQuizProgress(data.percent, data.current);
    if (data.running) {
      quizWasRunning = true;
      quizPollTimer = window.setTimeout(pollQuiz, 600);
      return;
    }
    if (quizWasRunning) {
      quizWasRunning = false;
      const summary = data.summary || {};
      const message = summary.submitted
        ? `测验已提交 ${summary.count || 0} 题` + (summary.missing ? `，${summary.missing} 题未答` : "")
        : `作答失败：${summary.message || "未知错误"}`;
      toast(message, summary.submitted ? "ok" : "warn");
      state.quizLoaded = false;
      loadQuizzes(true);
    }
  } catch {
    /* 忽略一次轮询失败 */
  }
}

const quizView = { item: null, questions: [], index: 0, answers: {}, loading: false, completed: false, score: null };

function quizOptionLetter(text, index) {
  const match = text.match(/^\s*([A-Z])[.、．:：)]?\s*/);
  return match ? match[1] : String.fromCharCode(65 + index);
}

function syncQuizAnswer() {
  const question = quizView.questions[quizView.index];
  if (!question) return;
  const host = $("#quizQuestion");
  const area = host.querySelector("textarea");
  if (area) {
    quizView.answers[question.id] = area.value;
    return;
  }
  if (question.type === "multiple") {
    const checked = [...host.querySelectorAll("input:checked")].map((item) => item.value).sort();
    quizView.answers[question.id] = checked.join("");
  } else {
    const checked = host.querySelector("input:checked");
    if (checked) quizView.answers[question.id] = checked.value;
  }
}

function updateOverallProgress() {
  const items = state.quizItems || [];
  const done = items.filter((item) => item.passed).length;
  const total = items.length;
  setQuizProgress(total ? (done / total) * 100 : 0,
    total ? `已完成 ${done} / ${total} 章` : "选择一道测验开始");
}

function updateQuizProgress() {
  const total = quizView.questions.length || 1;
  let percent = 100;
  if (!quizView.completed) {
    const answered = quizView.questions.filter((question) =>
      String(quizView.answers[question.id] ?? "").trim()
    ).length;
    percent = (answered / total) * 100;
  }
  $("#quizModalBar").style.width = `${percent}%`;
  $("#quizRing").style.setProperty("--p", percent);
  $("#quizRingText").textContent = `${Math.round(percent)}%`;
}

function renderQuizQuestion() {
  const question = quizView.questions[quizView.index];
  if (!question) return;
  const total = quizView.questions.length;
  const labels = {
    single: "单选题", multiple: "多选题", judgement: "判断题",
    completion: "填空题", shortanswer: "简答题",
  };
  $("#quizIndex").textContent = `${quizView.index + 1} / ${total}`;
  $("#quizModalBar").style.width = `${((quizView.index + 1) / total) * 100}%`;
  $("#quizMeta").textContent = `${labels[question.type] || question.type} · 共 ${total} 题`;
  updateQuizProgress();
  const host = $("#quizQuestion");
  host.innerHTML = "";
  if (quizView.completed) {
    const banner = document.createElement("div");
    banner.className = "quiz-done-banner";
    banner.textContent = quizView.score != null
      ? `该测验已完成，得分 ${quizView.score} 分。以下为题目回顾，不能再次提交。`
      : "该测验已完成，以下为题目回顾，不能再次提交。";
    host.appendChild(banner);
  }
  const title = document.createElement("h4");
  title.textContent = `${quizView.index + 1}. ${question.title}`;
  host.appendChild(title);
  const current = quizView.answers[question.id] || "";

  const addChoice = (value, text) => {
    const label = document.createElement("label");
    label.className = "quiz-option" + (current.includes(value) ? " selected" : "");
    const input = document.createElement("input");
    input.type = question.type === "multiple" ? "checkbox" : "radio";
    input.name = "quizOption";
    input.value = value;
    input.checked = current.includes(value);
    input.addEventListener("change", () => {
      syncQuizAnswer();
      quizLog("info", `第 ${quizView.index + 1} 题 选择 ${value}`);
      renderQuizQuestion();
    });
    const span = document.createElement("span");
    span.textContent = text;
    label.append(input, span);
    host.appendChild(label);
  };

  if (question.type === "judgement") {
    addChoice("true", "正确");
    addChoice("false", "错误");
  } else if (question.type === "completion" || question.type === "shortanswer" || !question.options.length) {
    const area = document.createElement("textarea");
    area.className = "quiz-textarea";
    area.placeholder = "输入答案…";
    area.value = current;
    area.addEventListener("input", () => {
      quizView.answers[question.id] = area.value;
      updateQuizProgress();
    });
    area.addEventListener("change", () => {
      quizLog("info", `第 ${quizView.index + 1} 题 已填写`);
    });
    host.appendChild(area);
  } else {
    question.options.forEach((text, index) => addChoice(quizOptionLetter(text, index), text));
  }
}

function quizLog(level, text) {
  const host = $("#quizConsole");
  const empty = $("#quizConsoleEmpty");
  if (empty) empty.remove();
  const line = document.createElement("div");
  line.className = `line ${level || "info"}`;
  const time = document.createElement("span");
  time.className = "t";
  time.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const message = document.createElement("span");
  message.className = "m";
  message.textContent = text;
  line.append(time, message);
  host.appendChild(line);
  host.scrollTop = host.scrollHeight;
}

function markQuizPassed(jobid) {
  const item = (state.quizItems || []).find((quiz) => quiz.jobid === jobid);
  if (item) item.passed = true;
  const row = document.querySelector(`.chapter-item[data-jobid="${CSS.escape(jobid)}"]`);
  if (row) {
    row.classList.add("finished");
    row.dataset.passed = "1";
    const pill = row.querySelector(".pill");
    if (pill) pill.textContent = "已完成";
    const action = row.querySelector(".quiz-solve");
    if (action) action.textContent = "回顾";
    applyQuizFilter();
  }
  updateOverallProgress();
}

async function openQuiz(item) {
  if (quizView.loading) return;
  quizView.loading = true;
  try {
    quizLog("info", `打开测验：${item.name}`);
    const data = await api(
      `/api/quiz/detail?course_index=${item.course_index}` +
      `&point_id=${encodeURIComponent(item.point_id)}&jobid=${encodeURIComponent(item.jobid)}`
    );
    quizView.item = item;
    quizView.questions = data.questions || [];
    quizView.answers = {};
    quizView.index = 0;
    if (!quizView.questions.length) throw new Error("没有读取到题目");
    quizView.completed = Boolean(data.completed);
    quizView.score = data.score;
    $("#quizActiveState").textContent = quizView.completed
      ? (data.score != null ? `已完成 ${data.score} 分` : "已完成")
      : "作答中";
    if (quizView.completed) {
      markQuizPassed(item.jobid);
      quizLog("warn", `该测验已完成${data.score != null ? `，得分 ${data.score} 分` : ""}，仅供查看`);
      toast(`该测验已完成${data.score != null ? `：${data.score} 分` : ""}`, "warn");
    }
    $("#quizSubmit").disabled = quizView.completed;
    $("#quizAi").disabled = quizView.completed;
    quizLog("ok", `已加载 ${quizView.questions.length} 道题` + (data.encrypted ? "，字体已解码" : ""));
    $("#quizTitle").textContent = item.name;
    $("#quizList").hidden = true;
    $("#quizActive").hidden = false;
    $("#quizActiveName").textContent = item.name;
    $("#quizRunner").hidden = false;
    animate("#quizRunner", {
      opacity: [0, 1],
      y: [10, 0],
      duration: 300,
      ease: "out(3)",
    });
    $("#quizRunner").scrollIntoView({ behavior: "smooth", block: "start" });
    renderIcons();
    renderQuizQuestion();
  } catch (error) {
    quizLog("error", error.message);
    toast(error.message, "error");
  } finally {
    quizView.loading = false;
  }
}

function closeQuiz() {
  syncQuizAnswer();
  if (quizView.item) quizLog("info", "返回测验列表");
  $("#quizRunner").hidden = true;
  $("#quizActive").hidden = true;
  $("#quizList").hidden = false;
  $("#quizActiveName").textContent = "";
}

function quizStep(delta) {
  syncQuizAnswer();
  const next = quizView.index + delta;
  if (next < 0 || next >= quizView.questions.length) return;
  quizView.index = next;
  renderQuizQuestion();
}

async function aiFillQuestion() {
  const question = quizView.questions[quizView.index];
  if (!question) return;
  const button = $("#quizAi");
  button.disabled = true;
  try {
    quizLog("info", `AI 作答第 ${quizView.index + 1} 题…`);
    const data = await api("/api/quiz/ai", postJson({ questions: [question] }));
    quizView.answers[question.id] = data.answers?.[question.id] || "";
    quizLog("ok", `→ ${quizView.answers[question.id] || "未给出答案"}`);
    renderQuizQuestion();
  } catch (error) {
    quizLog("error", error.message);
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function submitQuizView() {
  syncQuizAnswer();
  const item = quizView.item;
  if (!item) return;
  const button = $("#quizSubmit");
  button.disabled = true;
  try {
    quizLog("info", "提交测验答案…");
    const data = await api("/api/quiz/submit", postJson({
      course_index: item.course_index,
      point_id: item.point_id,
      jobid: item.jobid,
      answers: quizView.answers,
    }));
    const saved = !data.submitted && data.missing > 0;
    const resultText = data.submitted
      ? `已提交 ${data.count} 题${data.missing ? `，${data.missing} 题未答` : ""}`
      : saved
        ? `已保存：${data.message || "未提交"}`
        : `提交失败：${data.message || "未知错误"}`;
    quizLog(data.submitted ? "ok" : (saved ? "warn" : "error"), resultText);
    toast(resultText, data.submitted ? "ok" : "warn");
    if (data.submitted) {
      markQuizPassed(item.jobid);
      state.quizLoaded = true;
    }
    closeQuiz();
  } catch (error) {
    quizLog("error", error.message);
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function solveQuiz(quiz, button) {
  if (quizWasRunning) return;
  button.disabled = true;
  button.textContent = "作答中…";
  try {
    await api("/api/quiz/solve", postJson({
      course_index: quiz.course_index,
      point_id: quiz.point_id,
      jobid: quiz.jobid,
    }));
    const host = $("#quizConsole");
    host.innerHTML = '<div class="console-empty" id="quizConsoleEmpty">等待日志…</div>';
    quizSince = 0;
    quizWasRunning = true;
    setQuizProgress(0, "准备");
    window.clearTimeout(quizPollTimer);
    pollQuiz();
  } catch (error) {
    toast(error.message, "error");
    button.disabled = false;
    button.textContent = "AI 作答";
  }
}

const DEFAULT_AI_MODELS = ["deepseek-chat", "deepseek-reasoner"];
let aiModelOptions = [...DEFAULT_AI_MODELS];

function renderAiModelMenu(filter = "") {
  const menu = $("#aiModelMenu");
  menu.innerHTML = "";
  const keyword = filter.trim().toLowerCase();
  const matched = aiModelOptions.filter((name) => name.toLowerCase().includes(keyword));
  if (!matched.length) {
    const empty = document.createElement("div");
    empty.className = "combo-empty";
    empty.textContent = "没有匹配的模型，可直接输模型 ID";
    menu.appendChild(empty);
    return;
  }
  matched.forEach((name) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "combo-item";
    item.textContent = name;
    item.addEventListener("mousedown", (event) => {
      event.preventDefault();
      $("#aiModel").value = name;
      $("#aiModelMenu").hidden = true;
    });
    menu.appendChild(item);
  });
}

function openAiModelMenu(showAll = false) {
  $("#aiModelMenu").hidden = false;
  renderAiModelMenu(showAll ? "" : $("#aiModel").value);
}

function populateAiModels(models, selected) {
  aiModelOptions = Array.from(new Set([...(models || []), ...DEFAULT_AI_MODELS])).filter(Boolean);
  $("#aiModel").value = selected || aiModelOptions[0] || "";
  renderAiModelMenu();
}

function setAiEffort(value) {
  state.aiEffort = value || "";
  $$(".effort-btn").forEach((button) => {
    button.classList.toggle("active", (button.dataset.effort || "") === state.aiEffort);
  });
}

async function loadAiConfig() {
  try {
    const data = await api("/api/ai/config");
    $("#aiBase").value = data.base_url || "https://api.deepseek.com";
    populateAiModels(DEFAULT_AI_MODELS, data.model);
    setAiEffort(data.reasoning_effort || "");
    $("#aiKey").placeholder = data.has_key ? "已保存，留空表示不修改" : "sk-...";
    if (data.has_key) {
      try {
        const list = await api("/api/ai/models");
        populateAiModels([...(list.models || []), ...DEFAULT_AI_MODELS], data.model);
      } catch {
        /* 拉不到就先用官方两个 */
      }
    }
  } catch {
    /* 忽略 */
  }
}

async function saveAiConfig() {
  const model = $("#aiModel").value.trim() || "deepseek-chat";
  const payload = {
    base_url: $("#aiBase").value.trim() || "https://api.deepseek.com",
    model,
    reasoning_effort: state.aiEffort,
  };
  const key = $("#aiKey").value.trim();
  if (key) payload.api_key = key;
  try {
    await api("/api/ai/config", postJson(payload));
    $("#aiKey").value = "";
    $("#aiKey").placeholder = "已保存，留空表示不修改";
    $("#aiStatus").textContent = "已保存";
    toast("AI 配置已保存", "ok");
  } catch (error) {
    $("#aiStatus").textContent = "";
    toast(error.message, "error");
  }
}

async function testAiConfig() {
  const button = $("#aiTestBtn");
  button.disabled = true;
  $("#aiStatus").textContent = "正在测试…";
  try {
    const data = await api("/api/ai/test", postJson({}));
    $("#aiStatus").textContent = `连接正常：${data.reply}（${data.model}）`;
    toast("AI 接口连接正常", "ok");
  } catch (error) {
    $("#aiStatus").textContent = error.message;
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function loadAutoLogin() {
  try {
    const data = await api("/api/session/saved");
    $("#autoLoginToggle").checked = Boolean(data.saved);
    $("#autoLoginState").textContent = data.saved ? "已开启" : "已关闭";
  } catch {
    /* 忽略 */
  }
}

async function updateAutoLogin(enabled) {
  try {
    const data = await api("/api/session/auto", postJson({ enabled }));
    $("#autoLoginToggle").checked = Boolean(data.saved);
    $("#autoLoginState").textContent = data.saved ? "已开启" : "已关闭";
    toast(data.saved ? "已开启自动登录" : "已关闭并清除保存的登录", "ok");
  } catch (error) {
    toast(error.message, "error");
    loadAutoLogin();
  }
}

async function clearSavedLogin() {
  try {
    await api("/api/session/clear", postJson({}));
    $("#autoLoginToggle").checked = false;
    $("#autoLoginState").textContent = "已关闭";
    toast("已清除保存的登录信息", "ok");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function updateThemeChoice(choice) {
  state.theme = choice;
  try {
    localStorage.setItem(THEME_KEY, choice);
  } catch {
    /* 忽略 */
  }
  applyTheme();
}

/* ------------------------------------------------------------------ 基础 */
function renderIcons() {
  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }
}

async function api(path, options) {
  const response = await fetch(path, options);
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok || data.ok === false) {
    const error = new Error(data.error || `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return data;
}

const postJson = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

function toast(message, type = "info") {
  const host = $("#toasts");
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  const icon = document.createElement("span");
  icon.className = "toast-icon";
  icon.innerHTML = `<i data-lucide="${ICONS[type] || ICONS.info}"></i>`;
  const text = document.createElement("span");
  text.className = "toast-text";
  text.textContent = message;
  node.append(icon, text);
  host.appendChild(node);
  renderIcons();

  if (!prefersReduced) {
    animate(node, {
      x: [26, 0],
      opacity: [0, 1],
      duration: 460,
      ease: "out(3)",
    });
  }

  window.setTimeout(() => {
    if (prefersReduced) return node.remove();
    animate(node, {
      x: 18,
      opacity: 0,
      duration: 260,
      ease: "in(2)",
      onComplete: () => node.remove(),
    });
  }, 3800);
}

function fmtTime(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return minutes ? `${minutes} 分 ${rest} 秒` : `${rest} 秒`;
}

/* ------------------------------------------------------------------ 启动 */
init();

async function init() {
  bindUi();
  renderIcons();
  restorePrefs();
  restoreTheme();
  setMode("password", false);
  ambientMotion();
  loadAiConfig();
  if (window.pywebview) setupDesktop();
  window.addEventListener("pywebviewready", setupDesktop);
  try {
    const session = await api("/api/session");
    if (session.logged_in) {
      state.user = session.name;
      await enterApp(false);
      await loadCourses({ animate: true });
    } else {
      animateLoginIn();
    }
  } catch {
    animateLoginIn();
  } finally {
    $("#bootSplash").classList.add("hidden");
  }
  poll();
}

function ambientMotion() {
  if (prefersReduced) return;
  animate(".orb-1", {
    x: [0, 46], y: [0, 34], scale: [1, 1.09],
    duration: 17000, loop: true, alternate: true, ease: "inOutSine",
  });
  animate(".orb-2", {
    x: [0, -38], y: [0, 46], scale: [1, 1.06],
    duration: 21000, loop: true, alternate: true, ease: "inOutSine",
  });
  animate(".orb-3", {
    x: [0, 30], y: [0, -24], scale: [1, 1.12],
    duration: 24000, loop: true, alternate: true, ease: "inOutSine",
  });
}

function animateLoginIn() {
  if (prefersReduced) return;
  animate(".login-card", {
    y: [22, 0], opacity: [0, 1], duration: 900, ease: "out(3)",
  });
  animate(".eyebrow, .login-card h1, .login-sub", {
    y: [10, 0], opacity: [0, 1], duration: 700,
    delay: stagger(70, { start: 120 }), ease: "out(3)",
  });
  animate(".segmented, .login-card .field, .login-card .hint, #loginBtn, .foot-note", {
    y: [12, 0], opacity: [0, 1], duration: 620,
    delay: stagger(55, { start: 240 }), ease: "out(3)",
  });
}

function animateAppIn() {
  if (prefersReduced) return;
  const timeline = createTimeline({ defaults: { ease: "out(3)" } });
  timeline
    .add(".stats .stat", {
      y: [16, 0], opacity: [0, 1], duration: 620, delay: stagger(60),
    }, 0)
    .add(".courses-panel", { y: [20, 0], opacity: [0, 1], duration: 700 }, 100)
    .add(".control-panel", { x: [18, 0], opacity: [0, 1], duration: 700 }, 170)
    .add(".log-panel", { y: [20, 0], opacity: [0, 1], duration: 700 }, 240);
}

/* ------------------------------------------------------------------ 登录 */
function setMode(mode, animatePill = true) {
  state.mode = mode;
  $$(".seg").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  $$("[data-mode]").forEach((node) => {
    if (node.classList.contains("seg")) return;
    node.hidden = node.dataset.mode !== mode;
  });
  movePill(mode, animatePill);
  $("#loginBtn").hidden = mode === "qrcode";
  if (mode === "qrcode") startQrLogin();
  else stopQrPolling();
}

function movePill(mode, animatePill) {
  const pill = $("#segPill");
  const parent = pill.parentElement;
  const tabs = $$(".seg");
  const index = Math.max(0, tabs.findIndex((tab) => tab.dataset.mode === mode));
  const cell = (parent.clientWidth - 6) / tabs.length;
  const x = index * cell;
  if (!animatePill || prefersReduced) {
    pill.style.width = `${cell}px`;
    pill.style.transform = `translateX(${x}px)`;
    return;
  }
  animate(pill, { x, width: cell, duration: 420, ease: "out(3)" });
}

function setLoginBusy(busy) {
  $("#loginCard").classList.toggle("login-busy", busy);
  $("#loginBtn").disabled = busy;
}

/* ------------------------------------------------------------------ 桌面窗口 */
function setupDesktop() {
  document.body.classList.add("desktop");
  $("#windowControls").hidden = false;
  renderIcons();
  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-external]");
    if (!link) return;
    event.preventDefault();
    const api = desktopApi();
    if (api && api.open_external) api.open_external(link.href);
    else window.open(link.href, "_blank", "noopener");
  });
}

function desktopApi() {
  return window.pywebview && window.pywebview.api;
}

async function toggleMaximize() {
  const api = desktopApi();
  if (!api || !api.maximize || !api.restore) return;
  state.maximized = !state.maximized;
  try {
    if (state.maximized) await api.maximize();
    else await api.restore();
  } catch {
    state.maximized = !state.maximized;
  }
  updateMaxButton();
}

function updateMaxButton() {
  const label = state.maximized ? "还原" : "最大化";
  $("#winMax").innerHTML = `<i data-lucide="${state.maximized ? "copy" : "square"}"></i>`;
  $("#winMax").title = label;
  $("#winMax").setAttribute("aria-label", label);
  renderIcons();
}

function shakeLogin() {
  if (prefersReduced) return;
  animate("#loginCard", {
    x: [0, -9, 9, -6, 6, -2, 0],
    duration: 460,
    ease: "inOut(2)",
  });
}

async function submitLogin(event) {
  event.preventDefault();
  if (state.mode === "qrcode") return;
  const payload = { mode: state.mode, remember: $("#rememberLogin").checked };
  if (state.mode === "cookie") {
    payload.cookie = $("#cookieInput").value.trim();
    if (!payload.cookie) return toast("请先粘贴 Cookie 再登录", "warn");
  } else {
    payload.username = $("#username").value.trim();
    payload.password = $("#password").value;
    if (!payload.username || !payload.password) return toast("请填写账号和密码", "warn");
  }

  setLoginBusy(true);
  try {
    const data = await api("/api/login", postJson(payload));
    state.user = data.name || payload.username || "已登录";
    toast(`登录成功，欢迎 ${state.user}`, "ok");
    await enterApp(true);
    await loadCourses({ animate: true });
  } catch (error) {
    toast(error.message, "error");
    shakeLogin();
  } finally {
    setLoginBusy(false);
  }
}

async function enterApp(animateIn) {
  stopQrPolling();
  $("#loginView").hidden = true;
  $("#appView").hidden = false;
  $("#userChip").hidden = false;
  $("#diagnoseBtn").hidden = false;
  $("#refreshBtn").hidden = false;
  $("#logoutBtn").hidden = false;
  $("#userName").textContent = state.user || "已登录";
  $("#userAvatar").textContent = (state.user || "?").trim().slice(0, 1).toUpperCase();
  if (animateIn) animateAppIn();
}

function backToLogin() {
  location.reload();
}

/* ------------------------------------------------------------------ 扫码登录 */
async function startQrLogin() {
  stopQrPolling();
  const image = $("#qrImage");
  image.removeAttribute("src");
  $("#qrStatus").textContent = "正在获取二维码…";
  $("#qrRefresh").disabled = true;
  try {
    const data = await api("/api/qrcode/start", postJson({}));
    image.src = `data:${data.mime || "image/png"};base64,${data.image}`;
    $("#qrStatus").textContent = "请用学习通 App 扫描二维码";
    $("#qrRefresh").disabled = false;
    state.qrActive = true;
    state.qrTimer = window.setTimeout(pollQr, 1200);
  } catch (error) {
    $("#qrStatus").textContent = error.message;
    $("#qrRefresh").disabled = false;
  }
}

async function pollQr() {
  if (!state.qrActive || state.mode !== "qrcode") return;
  try {
    const data = await api(`/api/qrcode/status?remember=${$("#rememberLogin").checked ? 1 : 0}`);
    const status = $("#qrStatus");
    if (data.state === "waiting") {
      status.textContent = "等待扫码…";
    } else if (data.state === "scanned") {
      status.textContent = data.nickname
        ? `已扫描：${data.nickname}，请在手机上确认`
        : "已扫描，请在手机上点击确认";
    } else if (data.state === "confirmed") {
      state.qrActive = false;
      state.user = data.name || "已登录";
      toast(`扫码登录成功，欢迎 ${state.user}`, "ok");
      await enterApp(true);
      await loadCourses({ animate: true });
      return;
    } else if (data.state === "expired") {
      state.qrActive = false;
      status.textContent = "二维码已失效，请刷新";
      return;
    } else if (data.state === "cancelled") {
      state.qrActive = false;
      status.textContent = data.message || "已取消，请刷新二维码";
      return;
    } else if (data.state === "error") {
      state.qrActive = false;
      status.textContent = data.message || "扫码登录失败";
      return;
    }
  } catch {
    /* 网络抖动时继续轮询 */
  }
  if (state.qrActive) state.qrTimer = window.setTimeout(pollQr, 2000);
}

function stopQrPolling() {
  state.qrActive = false;
  if (state.qrTimer) {
    window.clearTimeout(state.qrTimer);
    state.qrTimer = null;
  }
}

/* ------------------------------------------------------------------ 课程 */
async function loadCourses({ animate: animateIn } = {}) {
  const refreshBtn = $("#refreshBtn");
  refreshBtn.disabled = true;
  try {
    const data = await api("/api/courses");
    state.courses = data.courses || [];
    state.selected.clear();
    state.chapterCache.clear();
    state.chapterCounts.clear();
    state.chapterUnits.clear();
    state.chapterDone.clear();
    state.chapterTotal.clear();
    state.quizLoaded = false;
    state.courseGeneration += 1;
    renderCourses(animateIn);
    updateStats();
    loadChapterCounts(state.courseGeneration);
    if (state.courses.length) {
      toast(`已读取 ${state.courses.length} 门课程`, "ok");
    }
  } catch (error) {
    if (error.status === 401) {
      toast("登录已失效，请重新登录", "error");
      window.setTimeout(backToLogin, 900);
      return;
    }
    toast(error.message, "error");
  } finally {
    refreshBtn.disabled = false;
  }
}

function renderCourses(animateIn) {
  const grid = $("#courseGrid");
  grid.innerHTML = "";
  $("#courseEmpty").hidden = state.courses.length > 0;

  state.courses.forEach((course, index) => {
    grid.appendChild(buildCourseCard(course, index));
  });
  renderIcons();

  if (animateIn && !prefersReduced && state.courses.length) {
    animate(".course-card", {
      y: [14, 0],
      opacity: [0, 1],
      duration: 620,
      delay: stagger(35, { start: 120 }),
      ease: "out(3)",
      onComplete: () => {
        $$(".course-card").forEach((card) => {
          card.style.translate = "";
          card.style.transform = "";
        });
      },
    });
  }

  applyFilter();
}

function buildCourseCard(course, index) {
  const card = document.createElement("div");
  card.className = "course-card";
  card.tabIndex = 0;
  card.dataset.index = String(index);
  card.setAttribute("role", "checkbox");
  card.setAttribute("aria-checked", "false");

  const check = document.createElement("span");
  check.className = "check";
  check.innerHTML = '<i data-lucide="check"></i>';

  const main = document.createElement("div");
  main.className = "card-main";
  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = course.title;
  const meta = document.createElement("div");
  meta.className = "card-meta";
  const teacher = document.createElement("span");
  teacher.textContent = course.teacher || "未标注教师";
  meta.appendChild(teacher);
  if (course.desc) {
    const dot = document.createElement("span");
    dot.className = "dot";
    const desc = document.createElement("span");
    desc.textContent = course.desc;
    meta.append(dot, desc);
  }
  main.append(title, meta);

  const action = document.createElement("button");
  action.type = "button";
  action.className = "card-action";
  action.textContent = "章节";
  action.addEventListener("click", (event) => {
    event.stopPropagation();
    openChapters(index);
  });

  const count = document.createElement("div");
  count.className = "chapter-count";
  count.textContent = "正在读取章节…";
  main.appendChild(count);

  card.append(check, main, action);
  card.addEventListener("click", () => {
    if (state.running) return;
    toggleCard(card, !state.selected.has(index));
  });
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (!state.running) toggleCard(card, !state.selected.has(index));
    }
  });
  return card;
}

function toggleCard(card, selected) {
  const index = Number(card.dataset.index);
  if (selected) state.selected.add(index);
  else state.selected.delete(index);
  card.classList.toggle("selected", selected);
  card.setAttribute("aria-checked", String(selected));

  if (!prefersReduced) {
    const icon = card.querySelector(".check svg");
    if (icon) {
      animate(icon, {
        scale: selected ? [0.3, 1] : [1, 0.7],
        rotate: selected ? [-28, 0] : 0,
        duration: selected ? 430 : 200,
        ease: selected ? "outBack(2)" : "out(2)",
      });
    }
  }
  updateStats();
  updateStartState();
}

function updateStats(extra = {}) {
  if (extra.done !== undefined) state.stats.done = extra.done;
  if (extra.failed !== undefined) state.stats.failed = extra.failed;
  animateNumber($("#statCourses"), state.courses.length);
  animateNumber($("#statSelected"), state.selected.size);
  animateNumber($("#statDone"), state.stats.done);
  animateNumber($("#statFailed"), state.stats.failed);
  const hint = $("#coursesHint");
  if (hint) {
    hint.textContent = `共 ${state.courses.length} 门课程 · 已选 ${state.selected.size} 门 · 点击卡片勾选`;
  }
  updateStartState();
}

function animateNumber(element, value) {
  const previous = Number(element.dataset.value || 0);
  if (previous === value) return;
  element.dataset.value = String(value);
  if (prefersReduced) {
    element.textContent = String(value);
    return;
  }
  const counter = { value: previous };
  animate(counter, {
    value,
    duration: 420,
    ease: "out(2)",
    onUpdate: () => {
      element.textContent = String(Math.round(counter.value));
    },
  });
}

function updateStartState() {
  $("#startBtn").disabled = state.running || state.selected.size === 0;
  const label = $("#startLabel");
  if (label) {
    label.textContent = state.selected.size
      ? `开始刷课（${state.selected.size} 门）`
      : "开始刷课";
  }
}

function applyFilter() {
  const query = $("#searchInput").value.trim().toLowerCase();
  $$(".course-card").forEach((card) => {
    const course = state.courses[Number(card.dataset.index)];
    if (!course) return;
    const haystack = `${course.title} ${course.teacher} ${course.desc}`.toLowerCase();
    card.hidden = Boolean(query) && !haystack.includes(query);
  });
}

async function loadChapterCounts(generation) {
  const queue = state.courses.map((_, index) => index);
  const worker = async () => {
    while (queue.length) {
      const index = queue.shift();
      if (generation !== state.courseGeneration) return;
      try {
        const data = await api(`/api/chapters?index=${index}`);
        if (generation !== state.courseGeneration) return;
        state.chapterCache.set(index, data.chapters || []);
        state.chapterCounts.set(index, (data.chapters || []).length);
        state.chapterUnits.set(index, data.units || 0);
        state.chapterDone.set(index, data.done_tasks || 0);
        state.chapterTotal.set(index, data.total_tasks || 0);
        updateChapterCount(index, data.units || 0, (data.chapters || []).length,
          data.done_tasks || 0, data.total_tasks || 0);
      } catch {
        if (generation === state.courseGeneration) updateChapterCount(index, 0, null, 0, 0);
      }
    }
  };
  await Promise.all(Array.from({ length: 3 }, worker));
}

function updateChapterCount(index, units, count, done, total) {
  const card = document.querySelector(`.course-card[data-index="${index}"]`);
  if (!card) return;
  const el = card.querySelector(".chapter-count");
  if (!el) return;
  if (count === null) {
    el.textContent = "章节读取失败";
    return;
  }
  const structure = units > 0 ? `${units} 章 · ${count} 节` : `共 ${count} 节`;
  el.textContent = total ? `${structure} · 任务点 ${done}/${total}` : structure;
}

/* ------------------------------------------------------------------ 刷课 */
function readOptions() {
  return {
    speed: state.speed,
    skip_finished: $("#optSkipFinished").checked,
    do_document: $("#optDocument").checked,
    do_read: $("#optRead").checked,
    instant: $("#optInstant").checked,
  };
}

async function startBrush() {
  if (state.running) return;
  if (!state.selected.size) return toast("请先勾选要刷的课程", "warn");
  $("#startBtn").disabled = true;
  try {
    await api(
      "/api/brush",
      postJson({ indices: [...state.selected], options: readOptions() }),
    );
    state.running = true;
    state.stats = { done: 0, failed: 0 };
    setRunningUi(true);
    setProgress(0, "准备中…");
    updateStats();
    toast("已开始刷课，日志会实时输出", "ok");
  } catch (error) {
    toast(error.message, "error");
    $("#startBtn").disabled = false;
  }
}

async function stopBrush() {
  try {
    await api("/api/stop", postJson({}));
    toast("已发送停止指令", "warn");
  } catch (error) {
    toast(error.message, "error");
  }
}

function setRunningUi(running) {
  $("#startBtn").hidden = running;
  $("#stopBtn").hidden = !running;
  $("#runDot").classList.toggle("active", running);
  $("#logStatus").textContent = running ? "运行中" : "空闲";
  $("#phaseLabel").textContent = running ? "刷课中" : "待命";
  $("#courseGrid").classList.toggle("locked", running);
  updateStartState();
  if (!prefersReduced) {
    animate(running ? "#stopBtn" : "#startBtn", {
      scale: [0.97, 1],
      opacity: [0.7, 1],
      duration: 320,
      ease: "out(3)",
    });
  }
}

function setProgress(percent, text) {
  if (text) $("#currentTask").textContent = text;
  const target = Math.round(Math.max(0, Math.min(100, Number(percent) || 0)) * 100) / 100;
  if (target === state.percentShown) return;
  const from = state.percentShown;
  state.percentShown = target;
  const label = $("#progressPct");
  const bar = $("#progressBar");

  if (prefersReduced) {
    label.textContent = `${target.toFixed(2)}%`;
    bar.style.width = `${target}%`;
    return;
  }
  const counter = { value: from };
  animate(counter, {
    value: target,
    duration: 620,
    ease: "out(3)",
    onUpdate: () => {
      label.textContent = `${counter.value.toFixed(2)}%`;
      bar.style.width = `${counter.value}%`;
    },
  });
}

/* ------------------------------------------------------------------ 轮询 */
async function poll() {
  try {
    const data = await api(`/api/status?since=${state.since}`);
    applyStatus(data);
  } catch {
    /* 服务不可达时安静重试 */
  } finally {
    state.pollTimer = window.setTimeout(poll, state.running ? 600 : 1400);
  }
}

function applyStatus(data) {
  if (data.logs && data.logs.length) {
    state.since = data.logs[data.logs.length - 1].seq;
    appendLogs(data.logs);
  }
  if (data.running) {
    if (!state.running) {
      state.running = true;
      setRunningUi(true);
    }
    setProgress(data.percent, data.current);
    updateStats({ done: data.stats.done, failed: data.stats.failed });
  } else if (state.running) {
    state.running = false;
    setRunningUi(false);
    const stopped = Boolean(data.summary && data.summary.stopped);
    const failed = Boolean(data.summary && data.summary.error);
    setProgress(stopped || failed ? data.percent : 100, stopped ? "已停止" : "已结束");
    updateStats({ done: data.stats.done, failed: data.stats.failed });
    if (data.summary) summarize(data.summary);
  }
}

function summarize(summary) {
  if (summary.error) {
    toast(summary.error, "error");
    return;
  }
  const parts = [];
  parts.push(summary.stopped ? "已停止" : "刷课完成");
  parts.push(`完成 ${summary.done} 个任务点`);
  if (summary.failed) parts.push(`失败 ${summary.failed}`);
  if (summary.skipped) parts.push(`跳过 ${summary.skipped}`);
  parts.push(`用时 ${fmtTime(summary.elapsed)}`);
  toast(parts.join(" · "), summary.failed ? "warn" : "ok");
}

/* ------------------------------------------------------------------ 日志 */
function appendLogs(logs) {
  const host = $("#console");
  const empty = $("#consoleEmpty");
  if (empty) empty.remove();

  const nodes = [];
  const fragment = document.createDocumentFragment();
  logs.forEach((log) => {
    const line = document.createElement("div");
    line.className = `line ${log.level || "info"}`;
    const time = document.createElement("span");
    time.className = "t";
    time.textContent = log.time || "";
    const message = document.createElement("span");
    message.className = "m";
    message.textContent = log.text || "";
    line.append(time, message);
    fragment.appendChild(line);
    nodes.push(line);
  });
  host.appendChild(fragment);

  while (host.children.length > 900) host.firstElementChild.remove();

  if (!prefersReduced && nodes.length && nodes.length <= 8) {
    animate(nodes, {
      x: [-8, 0],
      opacity: [0, 1],
      duration: 340,
      delay: stagger(18),
      ease: "out(2)",
    });
  }
  if ($("#autoScroll").checked) {
    host.scrollTop = host.scrollHeight;
  }
}

/* ------------------------------------------------------------------ 诊断 */
async function runDiagnose() {
  if (state.running) return toast("正在刷课，结束或停止后再诊断", "warn");
  const button = $("#diagnoseBtn");
  button.disabled = true;
  try {
    const data = await api("/api/diagnose", postJson({}));
    showDiagnose(data.report, data.passed);
    toast(
      data.passed ? "诊断通过：登录、课程、任务点读取都正常" : "诊断发现异常，请查看报告",
      data.passed ? "ok" : "warn",
    );
  } catch (error) {
    if (error.status === 401) {
      toast("登录已失效，请重新登录", "error");
      return;
    }
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function showDiagnose(report, passed) {
  const modal = $("#chapterModal");
  modal.hidden = false;
  $("#chapterTitle").textContent = "连接诊断";
  $("#chapterSub").textContent = passed ? "全部检查通过" : "发现问题，请查看下方";
  const host = $("#chapterList");
  host.innerHTML = "";
  report.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = `chapter-item ${item.ok ? "finished" : "locked"}`;
    const idx = document.createElement("span");
    idx.className = "idx";
    idx.textContent = String(index + 1).padStart(2, "0");
    const dot = document.createElement("span");
    dot.className = "dot";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = `${item.step}：${item.detail}`;
    name.title = name.textContent;
    const pill = document.createElement("span");
    pill.className = "pill";
    pill.textContent = item.ok ? "正常" : "异常";
    row.append(idx, dot, name, pill);
    host.appendChild(row);
  });
  if (!prefersReduced) {
    animate("#modalCard", {
      scale: [0.96, 1], y: [14, 0], opacity: [0, 1],
      duration: 420, ease: "out(3)",
    });
    animate(modal, { opacity: [0, 1], duration: 240, ease: "out(2)" });
  }
}

/* ------------------------------------------------------------------ 章节弹窗 */async function openChapters(index) {
  if (state.running) return toast("正在刷课，结束或停止后再查看章节", "warn");
  const modal = $("#chapterModal");
  const course = state.courses[index];
  modal.hidden = false;
  $("#chapterTitle").textContent = course ? course.title : "课程章节";
  $("#chapterSub").textContent = "正在读取章节与任务点…";
  $("#chapterList").innerHTML = '<div class="chapter-note">加载中…</div>';

  if (!prefersReduced) {
    animate("#modalCard", {
      scale: [0.96, 1], y: [14, 0], opacity: [0, 1],
      duration: 420, ease: "out(3)",
    });
    animate(modal, { opacity: [0, 1], duration: 240, ease: "out(2)" });
  }

  try {
    const data = await api(`/api/chapters?index=${index}`);
    $("#chapterTitle").textContent = data.course || (course ? course.title : "课程章节");
    $("#chapterSub").textContent = data.units
      ? `${data.units} 章 · ${data.chapters.length} 节` +
        (data.total_tasks ? ` · 任务点 ${data.done_tasks}/${data.total_tasks}` : "")
      : `${data.chapters.length} 个章节`;
    renderChapters(data.chapters);
  } catch (error) {
    $("#chapterSub").textContent = "读取失败";
    $("#chapterList").innerHTML = "";
    const note = document.createElement("div");
    note.className = "chapter-note";
    note.textContent = error.message;
    $("#chapterList").appendChild(note);
  }
}

function renderChapters(chapters) {
  const host = $("#chapterList");
  host.innerHTML = "";
  if (!chapters.length) {
    const note = document.createElement("div");
    note.className = "chapter-note";
    note.textContent = "没有读取到章节数据";
    host.appendChild(note);
    return;
  }
  chapters.forEach((chapter, index) => {
    const item = document.createElement("div");
    item.className = "chapter-item";
    if (chapter.finished) item.classList.add("finished");
    if (chapter.locked) item.classList.add("locked");

    const idx = document.createElement("span");
    idx.className = "idx";
    idx.textContent = String(index + 1).padStart(2, "0");
    const dot = document.createElement("span");
    dot.className = "dot";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = chapter.title;
    const pill = document.createElement("span");
    pill.className = "pill";
    pill.textContent = chapter.locked
      ? "未解锁"
      : chapter.finished
        ? "已完成"
        : chapter.jobs
          ? `${chapter.jobs} 个任务点`
          : "未完成";
    item.append(idx, dot, name, pill);
    host.appendChild(item);
  });
}

function closeChapters() {
  const modal = $("#chapterModal");
  if (prefersReduced) {
    modal.hidden = true;
    return;
  }
  animate("#modalCard", {
    scale: 0.97, y: 8, opacity: 0, duration: 210, ease: "in(2)",
  });
  animate(modal, {
    opacity: 0, duration: 210, ease: "in(2)",
    onComplete: () => {
      modal.hidden = true;
      modal.style.opacity = "";
      $("#modalCard").style.opacity = "";
    },
  });
}

/* ------------------------------------------------------------------ 偏好 */
function updateSpeedUi() {
  $("#speed").value = String(state.speed);
  $("#speedValue").textContent = `${state.speed.toFixed(1)}×`;
  const percent = ((state.speed - 1) / 2) * 100;
  $("#speed").style.setProperty("--p", `${percent}%`);
}

function savePrefs() {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify({
      speed: state.speed,
      skipFinished: $("#optSkipFinished").checked,
      document: $("#optDocument").checked,
      read: $("#optRead").checked,
      instant: $("#optInstant").checked,
      autoScroll: $("#autoScroll").checked,
    }));
  } catch {
    /* 忽略隐私模式下的存储异常 */
  }
}

function restorePrefs() {
  let prefs = {};
  try {
    prefs = JSON.parse(localStorage.getItem(PREF_KEY) || "{}");
  } catch {
    prefs = {};
  }
  if (typeof prefs.speed === "number") {
    state.speed = Math.min(3, Math.max(1, prefs.speed));
  }
  const mapping = {
    skipFinished: "#optSkipFinished",
    document: "#optDocument",
    read: "#optRead",
    instant: "#optInstant",
    autoScroll: "#autoScroll",
  };
  Object.entries(mapping).forEach(([key, selector]) => {
    if (typeof prefs[key] === "boolean") $(selector).checked = prefs[key];
  });
  updateSpeedUi();
}

/* ------------------------------------------------------------------ 事件 */
function bindUi() {
  $("#loginForm").addEventListener("submit", submitLogin);
  $$(".seg").forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  });

  $("#togglePass").addEventListener("click", () => {
    const input = $("#password");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    $("#togglePass").title = show ? "隐藏密码" : "显示密码";
    $("#togglePass").innerHTML = `<i data-lucide="${show ? "eye-off" : "eye"}"></i>`;
    renderIcons();
  });

  $("#qrRefresh").addEventListener("click", startQrLogin);

  $("#winMin").addEventListener("click", () => {
    const api = desktopApi();
    if (api && api.minimize) api.minimize();
  });
  $("#winMax").addEventListener("click", toggleMaximize);
  $("#winClose").addEventListener("click", () => {
    const api = desktopApi();
    if (api && api.close) api.close();
  });
  $("#themeBtn").addEventListener("click", cycleTheme);
  $$(".side-item").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  $("#aiModel").addEventListener("focus", () => openAiModelMenu(true));
  $("#aiModel").addEventListener("input", () => openAiModelMenu(false));
  $("#aiModelToggle").addEventListener("click", () => {
    if ($("#aiModelMenu").hidden) openAiModelMenu(true);
    else $("#aiModelMenu").hidden = true;
  });
  $$(".effort-btn").forEach((button) => {
    button.addEventListener("click", () => setAiEffort(button.dataset.effort || ""));
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#aiModelCombo")) $("#aiModelMenu").hidden = true;
  });
  $("#aiSaveBtn").addEventListener("click", saveAiConfig);
  $("#aiTestBtn").addEventListener("click", testAiConfig);
  $("#quizRefresh").addEventListener("click", () => loadQuizzes(true));
  $("#quizSearch").addEventListener("input", () => {
    state.quizQuery = $("#quizSearch").value.trim();
    applyQuizFilter();
  });
  $$("#quizFilter button").forEach((button) => {
    button.addEventListener("click", () => {
      state.quizFilter = button.dataset.filter || "all";
      $$("#quizFilter button").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      applyQuizFilter();
    });
  });
  $("#quizClose").addEventListener("click", closeQuiz);
  $("#quizBack").addEventListener("click", closeQuiz);
  $("#quizPrev").addEventListener("click", () => quizStep(-1));
  $("#quizNext").addEventListener("click", () => quizStep(1));
  $("#quizAi").addEventListener("click", aiFillQuestion);
  $("#quizSubmit").addEventListener("click", submitQuizView);
  $$("[data-theme-choice]").forEach((button) => {
    button.addEventListener("click", () => updateThemeChoice(button.dataset.themeChoice));
  });
  $("#autoLoginToggle").addEventListener("change", () => {
    updateAutoLogin($("#autoLoginToggle").checked);
  });
  $("#clearLoginBtn").addEventListener("click", clearSavedLogin);

  $$(".pywebview-drag-region").forEach((region) => {
    region.addEventListener("dblclick", toggleMaximize);
  });

  $("#refreshBtn").addEventListener("click", () => loadCourses({ animate: true }));
  $("#diagnoseBtn").addEventListener("click", runDiagnose);
  $("#logoutBtn").addEventListener("click", async () => {
    if (state.running) return toast("正在刷课，先停止再退出", "warn");
    try {
      await api("/api/logout", postJson({}));
    } catch {
      /* 忽略 */
    }
    backToLogin();
  });

  $("#selectAllBtn").addEventListener("click", () => {
    if (state.running) return;
    $$(".course-card").forEach((card) => {
      if (!card.hidden && !state.selected.has(Number(card.dataset.index))) {
        toggleCard(card, true);
      }
    });
  });
  $("#selectNoneBtn").addEventListener("click", () => {
    if (state.running) return;
    $$(".course-card").forEach((card) => {
      if (state.selected.has(Number(card.dataset.index))) toggleCard(card, false);
    });
  });
  $("#searchInput").addEventListener("input", applyFilter);

  $("#speed").addEventListener("input", (event) => {
    state.speed = Number(event.target.value);
    updateSpeedUi();
    savePrefs();
  });
  ["#optSkipFinished", "#optDocument", "#optRead", "#optInstant", "#autoScroll"].forEach((selector) => {
    $(selector).addEventListener("change", savePrefs);
  });

  $("#startBtn").addEventListener("click", startBrush);
  $("#stopBtn").addEventListener("click", stopBrush);

  $("#clearLogBtn").addEventListener("click", () => {
    $("#console").innerHTML = '<div class="console-empty" id="consoleEmpty">暂无日志，开始刷课后这里会实时输出。</div>';
    state.since = 0;
  });

  $("#closeModal").addEventListener("click", closeChapters);
  $("#chapterModal").addEventListener("click", (event) => {
    if (event.target === $("#chapterModal")) closeChapters();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#chapterModal").hidden) closeChapters();
  });

  $$(".btn").forEach((button) => {
    button.addEventListener("pointerdown", () => {
      if (prefersReduced || button.disabled) return;
      animate(button, { scale: 0.97, duration: 120, ease: "out(2)" });
    });
    const release = () => {
      if (prefersReduced) return;
      animate(button, { scale: 1, duration: 320, ease: "out(3)" });
    };
    button.addEventListener("pointerup", release);
    button.addEventListener("pointerleave", release);
  });

  window.addEventListener("resize", () => movePill(state.mode, false));
}
