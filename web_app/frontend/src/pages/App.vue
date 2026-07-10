<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { loadCachedLoginStatus, storeCachedLoginStatus } from "../authCache";
import { ApiError, authErrorLabel, authStatusLabel, postJson } from "../api";
import { readResourceBootstrap } from "../bootstrapState";
import FixedHeaderControls from "../components/FixedHeaderControls.vue";
import NumberField from "../components/NumberField.vue";
import TaskLog from "../components/TaskLog.vue";
import { loadPageCache, storePageCache } from "../pageCache";
import { restartBackendAndWait } from "../restart";
import { createLog, summarizeTask, taskName } from "../taskLog";
import type { AuthStatusResponse, Chapter, Course, Resource, TaskLogEntry, TaskRecord, Term } from "../types";

type ResourcePageCache = {
  profile: string;
  channel: string;
  loginStatus: string;
  selectedTerm: string;
  extractForm: {
    mode: string;
    metadata_concurrency: number;
  };
  downloadForm: {
    out: string;
    concurrency: number;
  };
};

const PAGE_CACHE_KEY = "resource-page";
const bootstrap = readResourceBootstrap();
const cachedPage = loadPageCache<ResourcePageCache>(PAGE_CACHE_KEY);

const profile = ref(cachedPage?.profile || ".xidian-profile");
const channel = ref(cachedPage?.channel || "auto");
const showSettingsPanel = ref(false);
const loginStatus = ref(
  bootstrap?.auth
    ? authStatusLabel(bootstrap.auth)
    : loadCachedLoginStatus(cachedPage?.profile || ".xidian-profile", cachedPage?.loginStatus || "未验证"),
);
const loginUserName = ref(bootstrap?.auth?.user?.name || "");
const checkingAuthStatus = ref(false);
const loggingOut = ref(false);
const restartingBackend = ref(false);
const loginTaskId = ref("");
const loadingTerms = ref(false);
const terms = ref<Term[]>(bootstrap?.page?.terms || []);
const selectedTerm = ref(bootstrap?.page?.selectedTerm || cachedPage?.selectedTerm || "");
const courses = ref<Course[]>(bootstrap?.page?.courses || []);
const courseSearch = ref("");
const selectedCourse = ref<Course | null>(null);
const chapters = ref<Chapter[]>([]);
const chapterSearch = ref("");
const chaptersLoaded = ref(false);
const loadingChapters = ref(false);
const selectedChapterIds = ref<string[]>([]);
const extractedResources = ref<Resource[]>([]);
const selectedResourceUrls = ref<string[]>([]);
const activeTask = ref<TaskRecord | null>(null);
const taskLogs = ref<TaskLogEntry[]>([]);
const showTaskDrawer = ref(false);
const rawLogCount = ref(0);
const eventSource = ref<EventSource | null>(null);
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

const extractForm = ref({
  mode: cachedPage?.extractForm?.mode || "all",
  metadata_concurrency: cachedPage?.extractForm?.metadata_concurrency ?? 24,
});

const downloadForm = ref({
  out: cachedPage?.downloadForm?.out || "downloaded_xidian",
  concurrency: cachedPage?.downloadForm?.concurrency ?? 4,
});

const isRunning = computed(() => Boolean(activeTask.value && ["queued", "running"].includes(activeTask.value.status)));
const selectedChapters = computed(() => {
  const selected = new Set(selectedChapterIds.value);
  return chapters.value.filter((chapter) => selected.has(chapter.chapter_id));
});
const selectedResources = computed(() => {
  const selected = new Set(selectedResourceUrls.value);
  return extractedResources.value.filter((resource) => selected.has(resource.url));
});
const taskSummary = computed(() => summarizeTask(activeTask.value));
const selectedCourseTitle = computed(() => selectedCourse.value?.name || "未选择课程");
const filteredCourses = computed(() => {
  const keyword = normalizeSearch(courseSearch.value);
  if (!keyword) return courses.value;
  return courses.value.filter((course) =>
    normalizeSearch([course.name, course.teacher, course.code, course.clazz].join(" ")).includes(keyword),
  );
});
const filteredChapters = computed(() => {
  const keyword = normalizeSearch(chapterSearch.value);
  if (!keyword) return chapters.value;
  return chapters.value.filter((chapter) => normalizeSearch([chapter.title, chapter.chapter_id].join(" ")).includes(keyword));
});
const chapterEmptyText = computed(() => {
  if (loadingChapters.value) return "正在读取章节...";
  if (chapters.value.length > 0 && filteredChapters.value.length === 0) return "没有匹配的章节。";
  if (!selectedCourse.value) return "选择课程后显示章节。";
  if (chaptersLoaded.value) return "这门课程没有可解析的章节。";
  return "选择课程后显示章节。";
});

function normalizeSearch(value: string) {
  return value.trim().toLowerCase();
}

function syncLoginSettings() {
  loginForm.value.profile = profile.value;
  loginForm.value.channel = channel.value;
}

function persistPageCache() {
  storePageCache<ResourcePageCache>(PAGE_CACHE_KEY, {
    profile: profile.value,
    channel: channel.value,
    loginStatus: loginStatus.value,
    selectedTerm: selectedTerm.value,
    extractForm: { ...extractForm.value },
    downloadForm: { ...downloadForm.value },
  });
  storeCachedLoginStatus(profile.value, loginStatus.value);
  document.cookie = `xidian_profile=${encodeURIComponent(profile.value)}; path=/; max-age=31536000`;
}

function resetCourseSelection() {
  courses.value = [];
  courseSearch.value = "";
  selectedCourse.value = null;
  chapters.value = [];
  chapterSearch.value = "";
  chaptersLoaded.value = false;
  selectedChapterIds.value = [];
  extractedResources.value = [];
  selectedResourceUrls.value = [];
}

function clearAuthBoundData() {
  terms.value = [];
  selectedTerm.value = "";
  resetCourseSelection();
}

function setLoginStatus(value: string) {
  loginStatus.value = value;
  storeCachedLoginStatus(profile.value, value);
}

function applyAuthIdentity(data: AuthStatusResponse) {
  loginUserName.value = data.authenticated ? data.user?.name || "" : "";
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
    loginUserName.value = "";
    setLoginStatus(authErrorLabel(error));
    clearAuthBoundData();
  }
}

function toggleSettingsPanel() {
  showSettingsPanel.value = !showSettingsPanel.value;
}

function addTaskLog(title: string, detail = "", level: TaskLogEntry["level"] = "info") {
  taskLogs.value.push(createLog(title, detail, level));
  void nextTick();
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

async function warmReplayBootstrapCache() {
  try {
    await postJson("/api/replay/courses", {
      profile: profile.value,
      channel: channel.value,
      semester_id: null,
    });
  } catch (error) {
    applyAuthFailure(error);
    addTaskLog("回放预热失败", error instanceof Error ? error.message : String(error), "error");
  }
}

async function syncAfterLoginSuccess() {
  await loadTerms().catch(() => undefined);
  await warmReplayBootstrapCache();
  addTaskLog("登录完成", "已自动读取资源课程，并同步回放学期缓存。");
}

async function refreshLoginStatus(preservePending = false): Promise<boolean> {
  checkingAuthStatus.value = true;
  try {
    const data = await postJson<AuthStatusResponse>("/api/auth/status", {
      profile: profile.value,
      channel: channel.value,
    });
    applyAuthIdentity(data);
    if (!data.authenticated && preservePending && loginTaskId.value) {
      setLoginStatus("登录中");
      return false;
    }
    setLoginStatus(authStatusLabel(data));
    return data.authenticated;
  } catch (error) {
    if (preservePending && loginTaskId.value && error instanceof ApiError && error.code === "missing_auth_state") {
      setLoginStatus("登录中");
      return false;
    }
    setLoginStatus(authErrorLabel(error));
    if (error instanceof ApiError && (error.code === "missing_auth_state" || error.code === "expired_auth_state")) {
      loginBootstrapLoaded = false;
      loginUserName.value = "";
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
    loginUserName.value = "";
    clearAuthBoundData();
    setLoginStatus("未找到登录态");
    addTaskLog("已退出登录", "已清除本地登录态。");
  } finally {
    loggingOut.value = false;
  }
}

async function loadTerms() {
  loadingTerms.value = true;
  try {
    const data = await postOrLog<{ terms: Term[]; selected?: Term }>("/api/xidian/terms", {
      profile: profile.value,
      channel: channel.value,
    });
    terms.value = data.terms || [];
    keepLoggedInLabel();
    const selected = data.selected || terms.value.find((term) => term.selected);
    selectedTerm.value = selected ? selected.value : "";
    if (selectedTerm.value) await loadCourses();
  } catch (error) {
    applyAuthFailure(error);
    throw error;
  } finally {
    loadingTerms.value = false;
  }
}

async function loadCourses() {
  if (!selectedTerm.value) return;
  const selected = terms.value.find((term) => term.value === selectedTerm.value);
  resetCourseSelection();

  try {
    const data = await postOrLog<{ courses: Course[]; terms?: Term[] }>("/api/xidian/courses", {
      profile: profile.value,
      channel: channel.value,
      term: selectedTerm.value,
      term_label: selected ? selected.label : "",
    });
    courses.value = data.courses || [];
    if (data.terms?.length) terms.value = data.terms;
    keepLoggedInLabel();
  } catch (error) {
    applyAuthFailure(error);
    throw error;
  }
}

async function selectCourse(course: Course) {
  selectedCourse.value = course;
  chapters.value = [];
  chapterSearch.value = "";
  chaptersLoaded.value = false;
  loadingChapters.value = true;
  selectedChapterIds.value = [];
  extractedResources.value = [];
  selectedResourceUrls.value = [];

  try {
    const data = await postOrLog<{ chapters: Chapter[] }>("/api/xidian/chapters", {
      profile: profile.value,
      channel: channel.value,
      course_url: course.href,
    });
    chapters.value = data.chapters || [];
    chaptersLoaded.value = true;
    keepLoggedInLabel();
  } catch (error) {
    applyAuthFailure(error);
    throw error;
  } finally {
    loadingChapters.value = false;
  }
}

function toggleAllChapters(checked: boolean) {
  selectedChapterIds.value = checked ? filteredChapters.value.map((chapter) => chapter.chapter_id) : [];
}

function toggleAllResources(checked: boolean) {
  selectedResourceUrls.value = checked ? extractedResources.value.map((resource) => resource.url) : [];
}

async function startExtractLinks() {
  const task = await postOrLog<TaskRecord>("/api/xidian/extract_links", {
    ...extractForm.value,
    chapters: selectedChapters.value,
    profile: profile.value,
    channel: channel.value,
  });
  extractedResources.value = [];
  selectedResourceUrls.value = [];
  attachTask(task, (finalTask) => {
    if (Array.isArray(finalTask.result)) {
      extractedResources.value = finalTask.result as Resource[];
      toggleAllResources(true);
      addTaskLog("已载入资源", `找到 ${extractedResources.value.length} 个可下载资源。`);
    }
  });
}

async function startDownload() {
  const task = await postOrLog<TaskRecord>("/api/xidian/download", {
    ...downloadForm.value,
    resources: selectedResources.value,
    profile: profile.value,
    channel: channel.value,
  });
  attachTask(task);
}

async function restartBackend() {
  if (restartingBackend.value) return;
  restartingBackend.value = true;
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
  showTaskDrawer.value = true;
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
    selectedTerm: selectedTerm.value,
    extractForm: { ...extractForm.value },
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
  <div class="workbench-shell">
    <FixedHeaderControls
      active-page="resources"
      :login-status="loginStatus"
      :checking="checkingAuthStatus"
      :logging-out="loggingOut"
      :settings-open="showSettingsPanel"
      @login="startManualLogin"
      @check="ensureAuthenticatedData"
      @release="releaseLogin"
      @logout="logout"
      @settings="toggleSettingsPanel"
    />

    <main id="main" class="workbench-main">
      <header class="workbench-toolbar">
        <div>
          <p class="eyebrow">资料</p>
          <h1>课程资源</h1>
        </div>
        <div class="toolbar-controls">
          <select v-model="selectedTerm" name="term" @change="loadCourses">
            <option value="">选择学期</option>
            <option v-for="term in terms" :key="term.value" :value="term.value">{{ term.label }}</option>
          </select>
          <button class="btn primary" type="button" :disabled="loadingTerms" @click="loadTerms">
            {{ loadingTerms ? "读取中" : "读取" }}
          </button>
        </div>
      </header>

      <section class="workbench-board">
        <section class="course-browser" aria-label="课程列表">
          <div class="pane-heading">
            <strong>课程</strong>
            <span>{{ filteredCourses.length }} / {{ courses.length }}</span>
          </div>
          <label class="search-line">
            <span class="visually-hidden">搜索课程</span>
            <input v-model.trim="courseSearch" name="course_search" placeholder="搜索课程、老师或课号" autocomplete="off" />
          </label>
          <div class="plain-list">
            <button
              v-for="course in filteredCourses"
              :key="course.href"
              class="plain-row"
              :class="{ active: selectedCourse?.href === course.href }"
              type="button"
              @click="selectCourse(course)"
            >
              <strong>{{ course.name }}</strong>
              <span>{{ course.code || "无课程号" }} / {{ course.teacher || "未知教师" }}</span>
              <small>{{ course.clazz || "未知班级" }}</small>
            </button>
            <div v-if="courses.length === 0" class="empty-line">选择学期后显示课程。</div>
            <div v-else-if="filteredCourses.length === 0" class="empty-line">没有匹配的课程。</div>
          </div>
        </section>

        <section class="content-pane" aria-label="当前课程">
          <div class="detail-title">
            <div>
              <span>当前课程</span>
              <h2>{{ selectedCourseTitle }}</h2>
            </div>
            <div class="detail-actions">
              <button class="btn" type="button" :disabled="loadingChapters || filteredChapters.length === 0" @click="toggleAllChapters(true)">全选章节</button>
              <button class="btn" type="button" :disabled="loadingChapters || selectedChapterIds.length === 0" @click="toggleAllChapters(false)">清空</button>
              <button class="btn primary" type="button" :disabled="selectedChapters.length === 0" @click="startExtractLinks">解析</button>
            </div>
          </div>

          <div class="split-content">
            <section class="tree-pane" aria-label="章节">
              <div class="pane-heading">
                <strong>章节</strong>
                <span>已选 {{ selectedChapters.length }}</span>
              </div>
              <label class="search-line">
                <span class="visually-hidden">搜索章节</span>
                <input v-model.trim="chapterSearch" name="chapter_search" placeholder="搜索章节" autocomplete="off" />
              </label>
              <div class="tree-list">
                <label v-for="chapter in filteredChapters" :key="chapter.chapter_id" class="tree-row">
                  <input v-model="selectedChapterIds" :value="chapter.chapter_id" type="checkbox" />
                  <span>
                    <strong>{{ chapter.title }}</strong>
                    <small>{{ chapter.chapter_id }}</small>
                  </span>
                </label>
                <div v-if="chapters.length === 0 || filteredChapters.length === 0" class="empty-line">{{ chapterEmptyText }}</div>
              </div>
            </section>

            <section class="file-pane" aria-label="资源">
              <div class="pane-heading">
                <strong>资源</strong>
                <span>已选 {{ selectedResources.length }}</span>
              </div>
              <div class="file-list-plain">
                <label v-for="resource in extractedResources" :key="resource.url" class="file-row">
                  <input v-model="selectedResourceUrls" :value="resource.url" type="checkbox" />
                  <span>
                    <strong>{{ resource.attachment_name }}</strong>
                    <small>{{ resource.chapter }} / {{ resource.kind.toUpperCase() }}</small>
                  </span>
                </label>
                <div v-if="extractedResources.length === 0" class="empty-line">解析后显示资源。</div>
              </div>
            </section>
          </div>
        </section>
      </section>
    </main>

    <aside v-if="showSettingsPanel" class="workspace-drawer">
      <div class="drawer-title">
        <strong>参数</strong>
        <button type="button" @click="toggleSettingsPanel">关闭</button>
      </div>
      <label class="field">
        <span>Profile</span>
        <input v-model.trim="profile" name="profile" autocomplete="off" />
      </label>
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
        <span>提取类型</span>
        <select v-model="extractForm.mode" name="mode">
          <option value="all">全部</option>
          <option value="pdf">PDF / 文档</option>
          <option value="video">视频</option>
        </select>
      </label>
      <NumberField v-model="extractForm.metadata_concurrency" label="解析并发" />
      <NumberField v-model="downloadForm.concurrency" label="下载并发" :max="16" />
      <label class="field">
        <span>输出目录</span>
        <input v-model.trim="downloadForm.out" name="out" autocomplete="off" />
      </label>
      <button class="btn" type="button" :disabled="restartingBackend || isRunning" @click="restartBackend">
        {{ restartingBackend ? "重启中" : "重启后端" }}
      </button>
    </aside>

    <footer class="download-rail">
      <div>
        <strong>已选 {{ selectedChapters.length }} 个章节 · {{ selectedResources.length }} 个资源</strong>
        <span>{{ taskSummary }}</span>
      </div>
      <div class="download-actions">
        <button class="btn" type="button" @click="showTaskDrawer = !showTaskDrawer">日志</button>
        <button class="btn" type="button" :disabled="extractedResources.length === 0" @click="toggleAllResources(true)">全选资源</button>
        <button class="btn" type="button" :disabled="selectedResourceUrls.length === 0" @click="toggleAllResources(false)">清空资源</button>
        <button class="btn primary" type="button" :disabled="selectedResources.length === 0" @click="startDownload">下载</button>
      </div>
    </footer>

    <section v-if="showTaskDrawer" class="task-drawer">
      <div class="drawer-title">
        <strong>任务日志</strong>
        <button type="button" @click="showTaskDrawer = false">收起</button>
      </div>
      <button class="btn" type="button" :disabled="!activeTask || !isRunning" @click="cancelActiveTask">取消任务</button>
      <TaskLog :logs="taskLogs" />
    </section>
  </div>
</template>
