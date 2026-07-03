import type { SavedFile } from "./types";

export async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `请求失败：${response.status}`);
  }

  return response.json() as Promise<T>;
}

export async function getFiles(out: string): Promise<SavedFile[]> {
  const response = await fetch(`/api/files?out=${encodeURIComponent(out)}`);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<SavedFile[]>;
}

export function formatSize(size: number): string {
  const formatter = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
  if (size > 1024 * 1024 * 1024) return `${formatter.format(size / 1024 / 1024 / 1024)} GB`;
  if (size > 1024 * 1024) return `${formatter.format(size / 1024 / 1024)} MB`;
  if (size > 1024) return `${formatter.format(size / 1024)} KB`;
  return `${formatter.format(size)} B`;
}
