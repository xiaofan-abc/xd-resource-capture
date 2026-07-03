import type { AuthStatusResponse, SavedFile } from "./types";

type ApiErrorPayload = {
  code?: string;
  message?: string;
  status?: string;
};

export class ApiError extends Error {
  statusCode: number;
  code?: string;
  payload?: unknown;

  constructor(message: string, options: { statusCode: number; code?: string; payload?: unknown }) {
    super(message);
    this.name = "ApiError";
    this.statusCode = options.statusCode;
    this.code = options.code;
    this.payload = options.payload;
  }
}

function extractErrorPayload(payload: unknown): ApiErrorPayload {
  if (!payload || typeof payload !== "object") return {};
  const root = payload as Record<string, unknown>;
  const detail = root.detail && typeof root.detail === "object" ? (root.detail as Record<string, unknown>) : root;
  return {
    code: typeof detail.code === "string" ? detail.code : undefined,
    message: typeof detail.message === "string" ? detail.message : typeof root.detail === "string" ? root.detail : undefined,
    status: typeof detail.status === "string" ? detail.status : undefined,
  };
}

async function readJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const contentType = response.headers.get("content-type") || "";
    let payload: unknown;

    try {
      payload = contentType.includes("application/json") ? await response.json() : await response.text();
    } catch {
      payload = await response.text().catch(() => "");
    }

    const parsed = extractErrorPayload(payload);
    const fallback =
      typeof payload === "string" && payload.trim() ? payload : `请求失败：${response.status}`;

    throw new ApiError(parsed.message || fallback, {
      statusCode: response.status,
      code: parsed.code,
      payload,
    });
  }

  return response.json() as Promise<T>;
}

export async function getJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, init);
  return readJsonResponse<T>(response);
}

export async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readJsonResponse<T>(response);
}

export async function getFiles(out: string): Promise<SavedFile[]> {
  const response = await fetch(`/api/files?out=${encodeURIComponent(out)}`);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<SavedFile[]>;
}

export function authStatusLabel(status: AuthStatusResponse): string {
  if (status.authenticated) {
    return status.profile?.status === "occupied" ? "已登录（缓存）" : "已登录";
  }
  if (status.status === "expired") return "登录态过期";
  return "未找到登录态";
}

export function authErrorLabel(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "expired_auth_state") return "登录态过期";
    if (error.code === "missing_auth_state") return "未找到登录态";
  }
  return "未登录";
}

export function authErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

export function formatSize(size: number): string {
  const formatter = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
  if (size > 1024 * 1024 * 1024) return `${formatter.format(size / 1024 / 1024 / 1024)} GB`;
  if (size > 1024 * 1024) return `${formatter.format(size / 1024 / 1024)} MB`;
  if (size > 1024) return `${formatter.format(size / 1024)} KB`;
  return `${formatter.format(size)} B`;
}
