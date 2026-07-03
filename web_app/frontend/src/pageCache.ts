const PAGE_CACHE_PREFIX = "xidian-page-cache-v1:";

type CacheEnvelope<T> = {
  value: T;
  updatedAt: number;
};

export function loadPageCache<T>(key: string): T | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const raw = window.localStorage.getItem(`${PAGE_CACHE_PREFIX}${key}`);
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as CacheEnvelope<T>;
    return parsed?.value;
  } catch {
    return undefined;
  }
}

export function storePageCache<T>(key: string, value: T) {
  if (typeof window === "undefined") return;
  try {
    const payload: CacheEnvelope<T> = {
      value,
      updatedAt: Date.now(),
    };
    window.localStorage.setItem(`${PAGE_CACHE_PREFIX}${key}`, JSON.stringify(payload));
  } catch {
    // Ignore cache write failures and keep runtime behavior unchanged.
  }
}
