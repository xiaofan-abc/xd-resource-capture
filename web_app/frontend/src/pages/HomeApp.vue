<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { authErrorLabel, authStatusLabel, postJson } from "../api";
import { loadCachedLoginStatus, storeCachedLoginStatus } from "../authCache";
import { readHomeBootstrap } from "../bootstrapState";
import FixedHeaderControls from "../components/FixedHeaderControls.vue";
import { loadPageCache, storePageCache } from "../pageCache";
import type { AuthStatusResponse, TaskRecord } from "../types";

type HomePageCache = {
  profile: string;
  channel: string;
  loginStatus: string;
};

const PAGE_CACHE_KEY = "home-page";
const XIDIAN_LOGIN_URL = "https://ids.xidian.edu.cn/authserver/login?service=https://xdspoc.fanya.chaoxing.com/sso/xdspoc";
const bootstrap = readHomeBootstrap();
const cachedPage = loadPageCache<HomePageCache>(PAGE_CACHE_KEY);

const profile = ref(cachedPage?.profile || ".xidian-profile");
const channel = ref(cachedPage?.channel || "auto");
const auth = ref<AuthStatusResponse | null>(bootstrap?.auth || null);
const showSettingsPanel = ref(false);
const loginStatus = ref(
  bootstrap?.auth
    ? authStatusLabel(bootstrap.auth)
    : loadCachedLoginStatus(cachedPage?.profile || ".xidian-profile", cachedPage?.loginStatus || "未验证"),
);
const checkingAuthStatus = ref(false);
const loggingOut = ref(false);
const loginTaskId = ref("");
const notice = ref(bootstrap?.auth?.message || "先确认登录身份，再进入课程资源或课程回放。");
let loginStatusPollHandle: number | null = null;

const loginForm = ref({
  login_url: XIDIAN_LOGIN_URL,
  profile: profile.value,
  channel: channel.value,
  username: "",
  password: "",
  headless: false,
});

const isAuthenticated = computed(() => Boolean(auth.value?.authenticated));
const personName = computed(() => {
  const name = auth.value?.user?.name?.trim();
  if (name) return name;
  return isAuthenticated.value ? "登录已确认" : "等待登录";
});
const personInitial = computed(() => Array.from(personName.value)[0] || "未");
const identityMeta = computed(() => {
  if (isAuthenticated.value && !auth.value?.user?.name?.trim()) return "账号姓名暂未识别";
  if (isAuthenticated.value) return "账号身份已确认";
  if (loginTaskId.value) return "登录窗口已打开，完成后回到这里检查状态。";
  return "请先打开登录并完成认证";
});
const statusLine = computed(() => (checkingAuthStatus.value ? "正在确认登录态" : loginStatus.value));

function syncLoginSettings() {
  loginForm.value.profile = profile.value;
  loginForm.value.channel = channel.value;
}

function persistPageCache() {
  storePageCache<HomePageCache>(PAGE_CACHE_KEY, {
    profile: profile.value,
    channel: channel.value,
    loginStatus: loginStatus.value,
  });
  storeCachedLoginStatus(profile.value, loginStatus.value);
  document.cookie = `xidian_profile=${encodeURIComponent(profile.value)}; path=/; max-age=31536000`;
}

function setLoginStatus(value: string) {
  loginStatus.value = value;
  storeCachedLoginStatus(profile.value, value);
}

function toggleSettingsPanel() {
  showSettingsPanel.value = !showSettingsPanel.value;
}

function stopLoginStatusPolling() {
  if (loginStatusPollHandle !== null) {
    window.clearInterval(loginStatusPollHandle);
    loginStatusPollHandle = null;
  }
}

async function refreshLoginStatus(preservePending = false): Promise<boolean> {
  checkingAuthStatus.value = true;
  syncLoginSettings();
  try {
    const data = await postJson<AuthStatusResponse>("/api/auth/status", {
      profile: profile.value,
      channel: channel.value,
    });
    auth.value = data;
    if (!data.authenticated && preservePending && loginTaskId.value) {
      setLoginStatus("登录中");
      notice.value = data.message || "登录窗口仍在等待完成。";
      return false;
    }
    setLoginStatus(authStatusLabel(data));
    notice.value = data.message || (data.authenticated ? "登录态可用。" : "还没有可用登录态。");
    if (data.authenticated) {
      loginTaskId.value = "";
      stopLoginStatusPolling();
    }
    return data.authenticated;
  } catch (error) {
    auth.value = null;
    setLoginStatus(authErrorLabel(error));
    notice.value = error instanceof Error ? error.message : String(error);
    return false;
  } finally {
    checkingAuthStatus.value = false;
  }
}

function startLoginStatusPolling() {
  stopLoginStatusPolling();
  loginStatusPollHandle = window.setInterval(() => {
    void refreshLoginStatus(true);
  }, 2200);
}

async function startManualLogin() {
  syncLoginSettings();
  try {
    const task = await postJson<TaskRecord>("/api/auth/open-login", loginForm.value);
    loginTaskId.value = task.id;
    auth.value = null;
    setLoginStatus("登录中");
    notice.value = "登录窗口已打开。完成网页登录后，首页会自动检查身份。";
    startLoginStatusPolling();
  } catch (error) {
    notice.value = error instanceof Error ? error.message : String(error);
  }
}

async function releaseLogin() {
  stopLoginStatusPolling();
  if (loginTaskId.value) {
    await postJson<TaskRecord>(`/api/tasks/${loginTaskId.value}/cancel`, {});
    loginTaskId.value = "";
  }
  await refreshLoginStatus();
}

async function logout() {
  if (loggingOut.value) return;
  loggingOut.value = true;
  stopLoginStatusPolling();
  try {
    await postJson<{ status: string; message: string }>("/api/auth/logout", {
      profile: profile.value,
    });
    auth.value = null;
    loginTaskId.value = "";
    setLoginStatus("未找到登录态");
    notice.value = "已清除本地登录态。";
  } catch (error) {
    notice.value = error instanceof Error ? error.message : String(error);
  } finally {
    loggingOut.value = false;
  }
}

watch(profile, (value, previous) => {
  syncLoginSettings();
  if (value !== previous) {
    auth.value = null;
    setLoginStatus(loadCachedLoginStatus(value, "未验证"));
  }
});

watch(channel, () => {
  syncLoginSettings();
});

watch([profile, channel, loginStatus], persistPageCache);

onMounted(() => {
  void refreshLoginStatus();
});

onBeforeUnmount(() => {
  stopLoginStatusPolling();
});
</script>

<template>
  <div class="desktop-shell">
    <FixedHeaderControls
      active-page="home"
      :login-status="loginStatus"
      :checking="checkingAuthStatus"
      :logging-out="loggingOut"
      :settings-open="showSettingsPanel"
      @login="startManualLogin"
      @check="refreshLoginStatus"
      @release="releaseLogin"
      @logout="logout"
      @settings="toggleSettingsPanel"
    />

    <main id="main" class="desktop-main">
      <section class="desktop-identity" aria-label="登录状态">
        <div class="identity-stamp" :class="{ signed: isAuthenticated }" aria-hidden="true">{{ personInitial }}</div>
        <div>
          <p>{{ statusLine }}</p>
          <h1>{{ personName }}</h1>
          <span>{{ identityMeta }}</span>
        </div>
      </section>

      <section class="desktop-command-list" aria-label="功能入口">
        <a class="desktop-command primary" href="/resources">
          <span>资料</span>
          <strong>课程资源</strong>
          <small>课件、文档、章节视频</small>
        </a>
        <a class="desktop-command" href="/replay">
          <span>回放</span>
          <strong>课堂回放</strong>
          <small>课时画面、课堂录像</small>
        </a>
      </section>

      <section class="desktop-status-line" aria-label="登录提示">
        <span>{{ notice }}</span>
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
    </aside>
  </div>
</template>
