export type TaskStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface TaskRecord {
  id: string;
  kind: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  exit_code: number | null;
  request: Record<string, unknown>;
  saved_files: string[];
  result: unknown;
  log_count: number;
}

export interface Term {
  value: string;
  label: string;
  selected?: boolean;
}

export interface AuthStatusResponse {
  authenticated: boolean;
  status: "authenticated" | "missing" | "expired" | string;
  message: string;
  url?: string;
  storage_state?: {
    path: string;
    exists: boolean;
    updated_at?: string;
    size?: number;
  };
  profile?: {
    status?: "occupied" | "available" | string;
    message?: string;
    session?: {
      path?: string;
      pid?: number;
      started_at?: string;
      storage_state_path?: string;
      channel?: string;
    } | null;
  };
}

export interface SystemTaskRef {
  id: string;
  kind: string;
  status: TaskStatus;
}

export interface SystemStatusResponse {
  pid: number;
  started_at: string;
  host: string;
  port: number;
  active_tasks: SystemTaskRef[];
  active_task_count: number;
  cache: {
    profiles: string[];
    count: number;
    updated_at?: string | null;
    path?: string | null;
  };
}

export interface RestartResponse extends SystemStatusResponse {
  status: "restarting" | string;
  message: string;
  helper_pid: number;
}

export interface Course {
  name: string;
  href: string;
  course_id?: string;
  clazz_id?: string;
  code?: string;
  teacher?: string;
  clazz?: string;
}

export interface Chapter {
  title: string;
  chapter_id: string;
  course_id: string;
  clazz_id: string;
  cpi: string;
  url: string;
}

export interface Resource {
  chapter: string;
  chapter_id: string;
  attachment_name: string;
  attachment_type?: string;
  object_id?: string;
  url: string;
  kind: string;
  source_field?: string;
  live_id?: string;
  label?: string;
}

export interface SavedFile {
  name: string;
  path: string;
  size: number;
  modified: number;
}

export interface ReplayCourse {
  course_id?: string;
  course_code?: string;
  course_name: string;
  clazz_id?: string;
  clazz_name?: string;
  teacher?: string;
  place?: string;
  replay_live_id?: string;
  live_count: number;
  replay_count: number;
  weeks?: string[];
}

export interface ReplaySession {
  id: string | number;
  week_day?: string | number;
  jie?: string;
  status: number;
  status_label: string;
  start_time?: string;
  end_time?: string;
}

export interface TaskLogEntry {
  time: string;
  title: string;
  detail: string;
  level: "info" | "error";
}
