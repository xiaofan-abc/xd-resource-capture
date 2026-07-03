import { getJson, postJson } from "./api";
import type { RestartResponse, SystemStatusResponse } from "./types";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export async function fetchSystemStatus(): Promise<SystemStatusResponse> {
  return getJson<SystemStatusResponse>(`/api/system/status?_=${Date.now()}`, {
    cache: "no-store",
  });
}

export async function restartBackendAndWait(persistState: () => void): Promise<SystemStatusResponse> {
  const current = await fetchSystemStatus().catch(() => null);
  persistState();

  const restart = await postJson<RestartResponse>("/api/system/restart", {});
  const previousPid = restart.pid || current?.pid || -1;
  const previousStartedAt = restart.started_at || current?.started_at || "";
  const deadline = Date.now() + 45_000;

  while (Date.now() < deadline) {
    await sleep(1000);
    try {
      const status = await fetchSystemStatus();
      if (status.pid !== previousPid || status.started_at !== previousStartedAt) {
        return status;
      }
    } catch {
      // The old process may be stopping; keep polling until the new one is ready.
    }
  }

  throw new Error("鍚庣閲嶅惎鍚庢湭鍦?45 绉掑唴鎭㈠銆?");
}
