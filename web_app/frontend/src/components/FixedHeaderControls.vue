<script setup lang="ts">
type PageKey = "home" | "resources" | "replay";

withDefaults(
  defineProps<{
    activePage: PageKey;
    loginStatus?: string;
    checking?: boolean;
    loggingOut?: boolean;
    settingsOpen?: boolean;
  }>(),
  {
    loginStatus: "",
    checking: false,
    loggingOut: false,
    settingsOpen: false,
  },
);

const emit = defineEmits<{
  (event: "login"): void;
  (event: "check"): void;
  (event: "release"): void;
  (event: "logout"): void;
  (event: "settings"): void;
}>();

const navItems: Array<{ key: PageKey; label: string; href: string }> = [
  { key: "home", label: "首页", href: "/" },
  { key: "resources", label: "资料", href: "/resources" },
  { key: "replay", label: "回放", href: "/replay" },
];
</script>

<template>
  <header class="fixed-header-bar" aria-label="顶部导航">
    <div class="fixed-auth-actions" aria-label="登录控制">
      <button class="btn primary compact" type="button" @click="emit('login')">登录</button>
      <button class="btn compact" type="button" :disabled="checking" @click="emit('check')">
        {{ checking ? "检查中" : "检查状态" }}
      </button>
      <button class="btn compact" type="button" @click="emit('release')">释放窗口</button>
      <button class="btn danger compact" type="button" :disabled="loggingOut" @click="emit('logout')">
        {{ loggingOut ? "退出中" : "退出登录" }}
      </button>
      <button class="btn compact settings" type="button" :aria-pressed="settingsOpen" @click="emit('settings')">参数</button>
      <span v-if="loginStatus" class="auth-status-chip" :title="loginStatus">{{ loginStatus }}</span>
    </div>

    <nav class="fixed-page-nav" aria-label="页面导航">
      <a
        v-for="item in navItems"
        :key="item.key"
        :href="item.href"
        :class="{ active: activePage === item.key }"
        :aria-current="activePage === item.key ? 'page' : undefined"
      >
        {{ item.label }}
      </a>
    </nav>
  </header>
</template>
