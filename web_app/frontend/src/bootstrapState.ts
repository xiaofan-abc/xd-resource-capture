import type { AuthStatusResponse, Course, ReplayCourse, Term } from "./types";

export type ResourceBootstrap = {
  auth?: AuthStatusResponse;
  page?: {
    terms?: Term[];
    selectedTerm?: string;
    courses?: Course[];
  };
  updated_at?: string;
};

export type ReplayBootstrap = {
  auth?: AuthStatusResponse;
  page?: {
    semesters?: Term[];
    selectedSemester?: string;
    courses?: ReplayCourse[];
  };
  updated_at?: string;
};

function readBootstrap<T>(): T | undefined {
  if (typeof window === "undefined") return undefined;
  return window.__XIDIAN_BOOTSTRAP__ as T | undefined;
}

export function readResourceBootstrap(): ResourceBootstrap | undefined {
  return readBootstrap<ResourceBootstrap>();
}

export function readReplayBootstrap(): ReplayBootstrap | undefined {
  return readBootstrap<ReplayBootstrap>();
}
