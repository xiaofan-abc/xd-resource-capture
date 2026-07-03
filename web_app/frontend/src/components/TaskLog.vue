<script setup lang="ts">
import type { TaskLogEntry } from "../types";

defineProps<{
  logs: TaskLogEntry[];
}>();
</script>

<template>
  <div class="task-log" aria-live="polite">
    <div v-for="(entry, index) in logs" :key="index" class="task-row">
      <time>{{ entry.time }}</time>
      <div>
        <strong :class="{ error: entry.level === 'error' }">{{ entry.title }}</strong>
        <p v-if="entry.detail">{{ entry.detail }}</p>
      </div>
    </div>
    <div v-if="logs.length === 0" class="empty" style="padding: 1rem 0">任务开始后，这里只显示关键进度。</div>
  </div>
</template>
