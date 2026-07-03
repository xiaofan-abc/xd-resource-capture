import type { TaskLogEntry, TaskRecord } from "./types";

export function nowLabel(): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

export function taskName(kind: string): string {
  return (
    {
      login: "登录窗口",
      "xidian-extract": "解析资源",
      "xidian-download": "下载资源",
    }[kind] || "任务"
  );
}

export function taskStatus(status: string): string {
  return (
    {
      queued: "排队中",
      running: "运行中",
      done: "已完成",
      failed: "失败",
      cancelled: "已取消",
    }[status] || status
  );
}

export function createLog(title: string, detail = "", level: TaskLogEntry["level"] = "info"): TaskLogEntry {
  return { time: nowLabel(), title, detail, level };
}

export function summarizeTask(task: TaskRecord | null): string {
  if (!task) return "暂无任务";
  return `${taskName(task.kind)} / ${taskStatus(task.status)}`;
}
