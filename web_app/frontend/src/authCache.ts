const AUTH_CACHE_KEY = "xidian-auth-cache-v1";

type CachedAuthRecord = {
  profile: string;
  label: string;
  updatedAt: number;
};

type AuthCacheState = {
  records: CachedAuthRecord[];
};

function readCache(): AuthCacheState {
  if (typeof window === "undefined") return { records: [] };
  try {
    const raw = window.localStorage.getItem(AUTH_CACHE_KEY);
    if (!raw) return { records: [] };
    const parsed = JSON.parse(raw) as AuthCacheState;
    if (!parsed || !Array.isArray(parsed.records)) return { records: [] };
    return {
      records: parsed.records.filter(
        (record) => record && typeof record.profile === "string" && typeof record.label === "string" && typeof record.updatedAt === "number",
      ),
    };
  } catch {
    return { records: [] };
  }
}

function writeCache(state: AuthCacheState) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify(state));
  } catch {
    // Ignore cache write failures and keep runtime behavior unchanged.
  }
}

export function loadCachedLoginStatus(profile: string, fallback = "未验证"): string {
  const state = readCache();
  const record = state.records.find((item) => item.profile === profile);
  return record?.label || fallback;
}

export function storeCachedLoginStatus(profile: string, label: string) {
  const state = readCache();
  const remaining = state.records.filter((item) => item.profile !== profile);
  remaining.unshift({ profile, label, updatedAt: Date.now() });
  writeCache({ records: remaining.slice(0, 8) });
}
