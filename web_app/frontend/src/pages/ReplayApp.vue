<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { loadCachedLoginStatus, storeCachedLoginStatus } from "../authCache";
import { ApiError, authErrorLabel, authStatusLabel, postJson } from "../api";
import { readReplayBootstrap } from "../bootstrapState";
import NumberField from "../components/NumberField.vue";
import TaskLog from "../components/TaskLog.vue";
import { loadPageCache, storePageCache } from "../pageCache";
import { restartBackendAndWait } from "../restart";
import { createLog, summarizeTask, taskName } from "../taskLog";
import type { AuthStatusResponse, ReplayCourse, ReplaySession, Resource, TaskLogEntry, TaskRecord, Term } from "../types";

type ReplayPageCache = {
  profile: string;
  channel: string;
  loginStatus: string;
  selectedSemester: string;
  downloadForm: {
    out: string;
    concurrency: number;
    name_mode: string;
  };
};

const PAGE_CACHE_KEY = "replay-page";
const bootstrap = readReplayBootstrap();
const cachedPage = loadPageCache<ReplayPageCache>(PAGE_CACHE_KEY);

const profile = ref(cachedPage?.profile || ".xidian-profile");
const channel = ref(cachedPage?.channel || "auto");
const showLoginPanel = ref(false);
const showSettingsPanel = ref(false);
const loginStatus = ref(
  bootstrap?.auth
    ? authStatusLabel(bootstrap.auth)
    : loadCachedLoginStatus(cachedPage?.profile || ".xidian-profile", cachedPage?.loginStatus || "未验证"),
);
const checkingAuthStatus = ref(false);
const loggingOut = ref(false);
const restartingBackend = ref(false);
const loginTaskId = ref("");
const loadingCourses = ref(false);
const loadingSessions = ref(false);
const loadingVideos = ref(false);
const semesters = ref<Term[]>(bootstrap?.page?.semesters || []);
const selectedSemester = ref(bootstrap?.page?.selectedSemester || cachedPage?.selectedSemester || "");
const courses = ref<ReplayCourse[]>(bootstrap?.page?.courses || []);
const courseSearch = ref("");
const selectedCourse = ref<ReplayCourse | null>(null);
const sessions = ref<ReplaySession[]>([]);
const resources = ref<Resource[]>([]);
const selectedResourceUrls = ref<string[]>([]);
const activeTask = ref<TaskRecord | null>(null);
const eventSource = ref<EventSource | null>(null);
const taskLogs = ref<TaskLogEntry[]>([]);
const rawLogCount = ref(0);
let loginStatusPollHandle: number | null = null;
let loginBootstrapLoaded = false;

const loginForm = ref({
  login_url: "https://ids.xidian.edu.cn/authserver/login?service=https://xdspoc.fanya.chaoxing.com/sso/xdspoc",
  profile: profile.value,
  channel: channel.value,
  username: "",
  password: "",
  headless: false,
});

const downloadForm = ref({
  out: cachedPage?.downloadForm?.out || "downloaded_replays",
  concurrency: cachedPage?.downloadForm?.concurrency ?? 4,
  name_mode: cachedPage?.downloadForm?.name_mode || "date",
});

const isRunning = computed(() => Boolean(activeTask.value && ["queued", "running"].includes(activeTask.value.status)));
const statusText = computed(() => {
  if (loadingVideos.value) return "正在解析课堂画面";
  if (loadingSessions.value) return "正在读取课程课时";
  if (loadingCourses.value) return "正在读取学期课程";
  return "使用当前登录缓存";
});
const selectedResources = computed(() => {
  const selected = new Set(selectedResourceUrls.value);
  return resources.value.filter((resource) => selected.has(resource.url));
});
const filteredCourses = computed(() => {
  const keyword = normalizeSearch(courseSearch.value);
  if (!keyword) return courses.value;
  return courses.value.filter((course) =>
    normalizeSearch([course.course_name, course.teacher, course.course_code, course.clazz_name].join(" ")).includes(keyword),
  );
});
const taskSummary = computed(() => summarizeTask(activeTask.value));

function normalizeSearch(value: string) {
  return value.trim().toLowerCase();
}

function syncLoginSettings() {
  loginForm.value.profile = profile.value;
  loginForm.value.channel = channel.value;
}

function persistPageCache() {
  storePageCache<ReplayPageCache>(PAGE_CACHE_KEY, {
    profile: profile.value,
    channel: channel.value,
    loginStatus: loginStatus.value,
    selectedSemester: selectedSemester.value,
    downloadForm: { ...downloadForm.value },
  });
  storeCachedLoginStatus(profile.value, loginStatus.value);
  document.cookie = `xidian_profile=${encodeURIComponent(profile.value)}; path=/; max-age=31536000`;
}

function resetReplaySelection() {
  courseSearch.value = "";
  selectedCourse.value = null;
  sessions.value = [];
  resources.value = [];
  selectedResourceUrls.value = [];
}

function clearAuthBoundData() {
  semesters.value = [];
  selectedSemester.value = "";
  courses.value = [];
  resetReplaySelection();
}

function setLoginStatus(value: string) {
  loginStatus.value = value;
  storeCachedLoginStatus(profile.value, value);
}

function keepLoggedInLabel() {
  if (!loginStatus.value.startsWith("已登录")) {
    setLoginStatus("已登录");
  }
}

function applyAuthFailure(error: unknown) {
  if (!(error instanceof ApiError)) return;
  if (error.code === "missing_auth_state" || error.code === "expired_auth_state") {
    loginBootstrapLoaded = false;
    setLoginStatus(authErrorLabel(error));
    clearAuthBoundData();
  }
}

function toggleLoginPanel() {
  showLoginPanel.value = !showLoginPanel.value;
  if (showLoginPanel.value) showSettingsPanel.value = false;
}

function toggleSettingsPanel() {
  showSettingsPanel.value = !showSettingsPanel.value;
  if (showSettingsPanel.value) showLoginPanel.value = false;
}

function addTaskLog(title: string, detail = "", level: TaskLogEntry["level"] = "info") {
  taskLogs.value.push(createLog(title, detail, level));
}

async function postOrLog<T>(url: string, body: unknown): Promise<T> {
  try {
    return await postJson<T>(url, body);
  } catch (error) {
    addTaskLog("请求失败", error instanceof Error ? error.message : String(error), "error");
    throw error;
  }
}

function stopLoginStatusPolling() {
  if (loginStatusPollHandle !== null) {
    window.clearInterval(loginStatusPollHandle);
    loginStatusPollHandle = null;
  }
}

async function warmResourceBootstrapCache() {
  try {
    const termsPayload = await postJson<{ terms: Term[]; selected?: Term }>("/api/xidian/terms", {
      profile: profile.value,
      channel: channel.value,
    });
    const selected = termsPayload.selected || termsPayload.terms.find((term) => term.selected);
    if (selected) {
      await postJson("/api/xidian/courses", {
        profile: profile.value,
        channel: channel.value,
        term: selected.value,
        term_label: selected.label,
      });
    }
  } catch (error) {
    applyAuthFailure(error);
    addTaskLog("璧勬簮棰勭儹澶辫触", error instanceof Error ? error.message : String(error), "error");
  }
}

async function syncAfterLoginSuccess() {
  await loadCourses().catch(() => undefined);
  await warmResourceBootstrapCache();
  addTaskLog("鐧诲綍瀹屾垚", "宸茶嚜鍔ㄨ鍙栧洖鏀捐绋嬶紝骞跺悓姝ヨ祫婧愰〉缂撳瓨銆?");
}

async function refreshLoginStatus(preservePending = false): Promise<boolean> {
  checkingAuthStatus.value = true;
  try {
    const data = await postJson<AuthStatusResponse>("/api/auth/status", {
      profile: profile.value,
      channel: channel.value,
    });
    if (!data.authenticated && preservePending && loginTaskId.value) {
      setLoginStatus("登录中");
      return false;
    }
    setLoginStatus(authStatusLabel(data));
    return data.authenticated;
  } catch (error) {
    if (preservePending && loginTaskId.value && error instanceof ApiError && error.code === "missing_auth_state") {
      setLoginStatus("鐧诲綍涓?");
      return false;
    }
    setLoginStatus(authErrorLabel(error));
    if (error instanceof ApiError && (error.code === "missing_auth_state" || error.code === "expired_auth_state")) {
      loginBootstrapLoaded = false;
      clearAuthBoundData();
    }
    return false;
  } finally {
    checkingAuthStatus.value = false;
  }
}

async function ensureAuthenticatedData(preservePending = false): Promise<boolean> {
  const authenticated = await refreshLoginStatus(preservePending);
  if (authenticated && !loginBootstrapLoaded) {
    loginBootstrapLoaded = true;
    await syncAfterLoginSuccess();
    stopLoginStatusPolling();
  }
  return authenticated;
}

function startLoginStatusPolling() {
  stopLoginStatusPolling();
  void ensureAuthenticatedData(true);
  loginStatusPollHandle = window.setInterval(() => {
    void ensureAuthenticatedData(true);
  }, 3000);
}

async function initializePage() {
  syncLoginSettings();
  persistPageCache();
}

async function startManualLogin() {
  syncLoginSettings();
  loginBootstrapLoaded = false;
  const task = await postOrLog<TaskRecord>("/api/auth/open-login", loginForm.value);
  loginTaskId.value = task.id;
  startLoginStatusPolling();
  setLoginStatus("登录中");
  attachTask(task, async () => {
    stopLoginStatusPolling();
    loginTaskId.value = "";
    await ensureAuthenticatedData();
  });
}

async function releaseLogin() {
  stopLoginStatusPolling();
  if (!loginTaskId.value) return;
  await postOrLog<TaskRecord>(`/api/tasks/${loginTaskId.value}/cancel`, {});
  loginTaskId.value = "";
  await ensureAuthenticatedData();
}

async function logout() {
  if (loggingOut.value) return;
  loggingOut.value = true;
  stopLoginStatusPolling();
  try {
    await postOrLog<{ status: string; message: string }>("/api/auth/logout", {
      profile: profile.value,
    });
    loginTaskId.value = "";
    loginBootstrapLoaded = false;
    clearAuthBoundData();
    setLoginStatus("未找到登录态");
    addTaskLog("已退出登录", `已清除 ${profile.value} 的本地登录态。`);
  } finally {
    loggingOut.value = false;
  }
}

function courseKey(course: ReplayCourse) {
  return `${course.course_id || ""}:${course.clazz_id || ""}:${course.course_code || course.course_name}`;
}

function resourcesForSession(sessionId: string | number) {
  return resources.value.filter((resource) => String(resource.live_id) === String(sessionId));
}

function sessionDateLabel(session: ReplaySession) {
  const match = String(session.start_time || "").match(/^\d{4}-(\d{2})-(\d{2})/);
  return match ? `${Number(match[1])}月${Number(match[2])}日` : "未知日期";
}

function daySectionLabel(session: ReplaySession) {
  return `第${session.jie || "?"}节`;
}

function courseOrderLabel(session: ReplaySession) {
  const index = sessions.value.findIndex((item) => String(item.id) === String(session.id));
  return `课程第${index >= 0 ? index + 1 : "?"}节`;
}

function fileBaseName(resource: Resource) {
  const session = sessions.value.find((item) => String(item.id) === String(resource.live_id));
  const courseName = selectedCourse.value ? selectedCourse.value.course_name : "未知课程";
  const datePart =
    downloadForm.value.name_mode === "course_order" && session ? courseOrderLabel(session) : session ? sessionDateLabel(session) : "未知日期";
  return `${courseName}_${datePart}_${session ? daySectionLabel(session) : "未知节次"}_${resource.label || resource.attachment_name}`;
}

function downloadReadyResources() {
  return selectedResources.value.map((resource) => ({
    ...resource,
    chapter: selectedCourse.value ? selectedCourse.value.course_name : resource.chapter,
    attachment_name: fileBaseName(resource),
  }));
}

async function loadCourses() {
  loadingCourses.value = true;
  try {
    const data = await postOrLog<{ semesters: Term[]; selected_semester?: Term; courses: ReplayCourse[] }>("/api/replay/courses", {
      profile: profile.value,
      channel: channel.value,
      semester_id: selectedSemester.value || null,
    });
    semesters.value = data.semesters || [];
    const selected = data.selected_semester || semesters.value.find((item) => item.selected);
    if (!selectedSemester.value && selected) selectedSemester.value = selected.value;
    courses.value = (data.courses || []).filter((course) => course.replay_count > 0);
    resetReplaySelection();
    keepLoggedInLabel();
  } catch (error) {
    applyAuthFailure(error);
    throw error;
  } finally {
    loadingCourses.value = false;
  }
}

async function selectCourse(course: ReplayCourse) {
  if (!course.replay_live_id) return;
  selectedCourse.value = course;
  loadingSessions.value = true;
  sessions.value = [];
  resources.value = [];
  selectedResourceUrls.value = [];

  try {
    const data = await postOrLog<{ sessions: ReplaySession[] }>("/api/replay/sessions", {
      live_id: String(course.replay_live_id),
      profile: profile.value,
      channel: channel.value,
    });
    sessions.value = (data.sessions || [])
      .filter((session) => session.status === 2)
      .sort((left, right) => String(left.start_time || "").localeCompare(String(right.start_time || "")));
    keepLoggedInLabel();
    await loadVideosForSessions();
  } catch (error) {
    applyAuthFailure(error);
    throw error;
  } finally {
    loadingSessions.value = false;
  }
}

async function loadVideosForSessions() {
  const liveIds = sessions.value.map((session) => String(session.id));
  if (liveIds.length === 0) return;
  loadingVideos.value = true;
  try {
    const data = await postOrLog<{ resources: Resource[] }>("/api/replay/videos", {
      live_ids: liveIds,
      profile: profile.value,
      channel: channel.value,
    });
    resources.value = data.resources || [];
    toggleResources(true);
    keepLoggedInLabel();
  } catch (error) {
    applyAuthFailure(error);
    throw error;
  } finally {
    loadingVideos.value = false;
  }
}

function toggleResources(checked: boolean) {
  selectedResourceUrls.value = checked ? resources.value.map((resource) => resource.url) : [];
}

async function startDownload() {
  const task = await postOrLog<TaskRecord>("/api/xidian/download", {
    ...downloadForm.value,
    resources: downloadReadyResources(),
    profile: profile.value,
    channel: channel.value,
  });
  attachTask(task);
}

async function restartBackend() {
  if (restartingBackend.value) return;
  restartingBackend.value = true;
  showLoginPanel.value = false;
  showSettingsPanel.value = false;
  persistPageCache();
  eventSource.value?.close();
  eventSource.value = null;
  addTaskLog("后端重启中", "正在保存缓存并等待服务恢复。");

  try {
    await restartBackendAndWait(persistPageCache);
    window.location.reload();
  } catch (error) {
    addTaskLog("后端重启失败", error instanceof Error ? error.message : String(error), "error");
  } finally {
    restartingBackend.value = false;
  }
}

function attachTask(task: TaskRecord, onDone: ((task: TaskRecord) => void | Promise<void>) | null = null) {
  eventSource.value?.close();
  activeTask.value = task;
  taskLogs.value = [];
  rawLogCount.value = 0;
  addTaskLog("任务已创建", `${taskName(task.kind)} 已提交。`);

  const source = new EventSource(`/api/tasks/${task.id}/events`);
  eventSource.value = source;

  source.addEventListener("snapshot", (event) => {
    activeTask.value = JSON.parse((event as MessageEvent).data) as TaskRecord;
  });

  source.addEventListener("status", (event) => {
    const payload = JSON.parse((event as MessageEvent).data) as { data: Partial<TaskRecord> };
    const previous = activeTask.value?.status || "";
    activeTask.value = { ...(activeTask.value as TaskRecord), ...payload.data };

    if (payload.data.status === "running" && previous !== "running") addTaskLog("任务开始运行");
    if (payload.data.status === "done") addTaskLog("任务完成", "结果已同步到页面。");
    if (payload.data.status === "failed") addTaskLog("任务失败", "请检查登录状态、网络或所选内容后重试。", "error");
    if (payload.data.status === "cancelled") addTaskLog("任务已取消");
  });

  source.addEventListener("log", (event) => {
    const payload = JSON.parse((event as MessageEvent).data) as { data: { level?: string } };
    rawLogCount.value += 1;
    if (payload.data.level === "error") {
      addTaskLog("运行时出现错误", "后台输出已折叠，页面仅保留关键信息。", "error");
    } else if (rawLogCount.value === 1 || rawLogCount.value % 12 === 0) {
      addTaskLog("处理中", `已收到 ${rawLogCount.value} 条后台进度。`);
    }
  });

  source.addEventListener("saved", (event) => {
    const payload = JSON.parse((event as MessageEvent).data) as { data: { path: string } };
    addTaskLog("已保存文件", payload.data.path);
  });

  source.addEventListener("done", (event) => {
    source.close();
    eventSource.value = null;
    if (onDone) {
      void onDone(JSON.parse((event as MessageEvent).data) as TaskRecord);
    }
  });
}

async function cancelActiveTask() {
  if (!activeTask.value) return;
  activeTask.value = await postOrLog<TaskRecord>(`/api/tasks/${activeTask.value.id}/cancel`, {});
}

watch(profile, (value, previous) => {
  syncLoginSettings();
  if (value !== previous) {
    setLoginStatus(loadCachedLoginStatus(value, "未验证"));
  }
});

watch(channel, () => {
  syncLoginSettings();
});

watch(
  () => ({
    profile: profile.value,
    channel: channel.value,
    loginStatus: loginStatus.value,
    selectedSemester: selectedSemester.value,
    downloadForm: { ...downloadForm.value },
  }),
  () => {
    persistPageCache();
  },
  { deep: true, immediate: true },
);

void initializePage();
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div>
        <div class="top-actions">
          <nav class="nav-links" aria-label="页面导航">
            <a class="btn" href="/">课程资源</a>
            <a class="btn primary" href="/replay">课程回放</a>
          </nav>
          <div class="menu-group">
            <button class="btn" type="button" @click="toggleLoginPanel">
              {{ loginStatus.startsWith("已登录") ? "已登录" : "登录" }}
            </button>
            <button class="btn" type="button" @click="toggleSettingsPanel">全局参数</button>

            <section v-if="showLoginPanel" class="floating-panel top-panel login-panel">
              <div class="section-head">
                <h2 class="eyebrow">登录</h2>
                <span class="status-pill">{{ loginStatus }}</span>
              </div>
              <div class="button-row login-actions">
                <button class="btn primary" type="button" @click="startManualLogin">打开登录</button>
                <button class="btn" type="button" @click="releaseLogin">释放窗口</button>
                <button class="btn" type="button" :disabled="checkingAuthStatus" @click="() => ensureAuthenticatedData()">
                  {{ checkingAuthStatus ? "检查中" : "检查状态" }}
                </button>
                <button class="btn" type="button" :disabled="loggingOut" @click="logout">
                  {{ loggingOut ? "退出中" : "退出登录" }}
                </button>
              </div>
            </section>

            <section v-if="showSettingsPanel" class="floating-panel top-panel wide">
              <h2 class="eyebrow" style="margin-bottom: 1rem">全局参数</h2>
              <label class="field">
                <span>Profile</span>
                <input v-model.trim="profile" name="profile" autocomplete="off" />
              </label>
              <div class="field-grid">
                <label class="field">
                  <span>浏览器</span>
                  <select v-model="channel" name="channel">
                    <option value="auto">auto</option>
                    <option value="msedge">Edge</option>
                    <option value="chrome">Chrome</option>
                    <option value="chromium">Chromium</option>
                  </select>
                </label>
                <label class="field">
                  <span>文件命名</span>
                  <select v-model="downloadForm.name_mode" name="name_mode">
                    <option value="date">课程 + 月日 + 当天第几节</option>
                    <option value="course_order">课程 + 课程第几节 + 当天第几节</option>
                  </select>
                </label>
              </div>
              <NumberField v-model="downloadForm.concurrency" label="下载并发" :max="16" />
              <label class="field">
                <span>输出目录</span>
                <input v-model.trim="downloadForm.out" name="out" autocomplete="off" />
              </label>
              <div class="button-row">
                <button class="btn" type="button" :disabled="restartingBackend || isRunning" @click="restartBackend">
                  {{ restartingBackend ? "重启中" : "重启后端" }}
                </button>
              </div>
              <p class="panel-note">重启会保存最近缓存，并在服务恢复后自动刷新页面。</p>
            </section>
          </div>
        </div>
        <h1>西电课程回放解析</h1>
        <p>{{ statusText }}</p>
      </div>
      <nav class="nav-links" aria-label="状态">
        <span class="status-pill">{{ loginStatus }}</span>
      </nav>
    </header>

    <main id="main" class="page-grid">
      <section class="content-stack">
        <section class="panel pad">
          <div class="section-head">
            <div class="section-title">
              <span class="step">1</span>
              <h2>学期</h2>
            </div>
            <button class="btn primary" type="button" :disabled="loadingCourses" @click="loadCourses">
              {{ loadingCourses ? "读取中..." : "读取" }}
            </button>
          </div>
          <label class="field" style="margin-bottom: 0">
            <span>选择学期</span>
            <select v-model="selectedSemester" name="semester" @change="loadCourses">
              <option value="">当前学期</option>
              <option v-for="semester in semesters" :key="semester.value" :value="semester.value">{{ semester.label }}</option>
            </select>
          </label>
        </section>

        <section class="panel pad">
          <div class="section-head">
            <div class="section-title">
              <span class="step">2</span>
              <h2>课程</h2>
            </div>
            <span class="empty">{{ filteredCourses.length }} / {{ courses.length }} 门</span>
          </div>
          <label class="field">
            <span>搜索课程</span>
            <input v-model.trim="courseSearch" name="replay_course_search" placeholder="课程名、老师或课号" autocomplete="off" />
          </label>
          <div class="course-grid">
            <button
              v-for="course in filteredCourses"
              :key="courseKey(course)"
              class="list-button panel"
              :class="{ active: selectedCourse && courseKey(selectedCourse) === courseKey(course) }"
              type="button"
              @click="selectCourse(course)"
            >
              <span class="item-name">{{ course.course_name }}</span>
              <span class="item-meta">{{ course.course_code || "无课程号" }} / {{ course.teacher || "未知教师" }} / {{ course.clazz_name || "未知班级" }}</span>
              <span class="item-meta">共 {{ course.live_count }} 课时，可回放 {{ course.replay_count }} 课时</span>
            </button>
            <div v-if="courses.length === 0" class="empty">读取后显示课程。</div>
            <div v-else-if="filteredCourses.length === 0" class="empty">没有匹配的课程。</div>
          </div>
        </section>

        <section class="panel pad">
          <div class="section-head">
            <div class="section-title">
              <span class="step">3</span>
              <div>
                <h2>课时与画面</h2>
                <p>{{ selectedCourse ? selectedCourse.course_name : "先选择课程" }}</p>
              </div>
            </div>
            <div class="button-row">
              <a class="btn" href="/sync-guide" target="_blank" rel="noreferrer">如何同步课件与课堂</a>
              <button class="btn" type="button" :disabled="resources.length === 0" @click="toggleResources(true)">全选</button>
              <button class="btn" type="button" :disabled="selectedResourceUrls.length === 0" @click="toggleResources(false)">清空</button>
              <button class="btn primary" type="button" :disabled="selectedResources.length === 0" @click="startDownload">下载选中</button>
            </div>
          </div>

          <div class="session-list">
            <div v-for="session in sessions" :key="session.id" class="session-block">
              <div class="session-title">
                <div>
                  <strong>周{{ session.week_day }} 第{{ session.jie }}节</strong>
                  <small>{{ session.start_time }} - {{ session.end_time }}</small>
                </div>
                <span class="status-pill">{{ session.status_label }}</span>
              </div>
              <div class="check-grid" style="max-height: none">
                <label v-for="resource in resourcesForSession(session.id)" :key="resource.url" class="check-card">
                  <input v-model="selectedResourceUrls" :value="resource.url" type="checkbox" />
                  <span class="check-card-text">
                    <strong>{{ resource.label || resource.attachment_name }}</strong>
                    <small>MP4</small>
                  </span>
                </label>
                <div v-if="resourcesForSession(session.id).length === 0" class="empty">暂无画面资源</div>
              </div>
            </div>
            <div v-if="sessions.length === 0" class="empty" style="padding: 0.8rem">选择课程后显示所有可回放课时。</div>
          </div>
        </section>

        <section class="panel task-panel">
          <div class="pad">
            <div class="section-head">
              <div class="section-title">
                <span class="step">4</span>
                <div>
                  <h2>任务日志</h2>
                  <p>{{ taskSummary }}</p>
                </div>
              </div>
              <button class="btn" type="button" :disabled="!activeTask || !isRunning" @click="cancelActiveTask">取消</button>
            </div>
            <TaskLog :logs="taskLogs" />
          </div>
        </section>
      </section>
    </main>
  </div>
</template>
