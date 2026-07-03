from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class UiStateCache:
    def __init__(self, *, cache_path: Path | None = None) -> None:
        self._cache_path = cache_path
        self._profiles: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        profiles = payload.get("profiles")
        if isinstance(profiles, dict):
            self._profiles = deepcopy(profiles)

    def _persist(self) -> None:
        if self._cache_path is None:
            return

        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profiles": self._profiles,
            "updated_at": now_iso(),
        }
        temp_path = self._cache_path.with_suffix(f"{self._cache_path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._cache_path)

    def _profile_state(self, profile: str) -> dict[str, Any]:
        return self._profiles.setdefault(profile, {"updated_at": now_iso()})

    def _set_section(self, profile: str, key: str, payload: dict[str, Any]) -> None:
        state = self._profile_state(profile)
        state[key] = deepcopy(payload)
        state["updated_at"] = now_iso()
        self._persist()

    def flush(self) -> None:
        self._persist()

    def clear_profile(self, profile: str) -> None:
        if profile not in self._profiles:
            return
        del self._profiles[profile]
        self._persist()

    def summary(self) -> dict[str, Any]:
        updated = [state.get("updated_at") for state in self._profiles.values() if isinstance(state, dict)]
        file_updated_at = None
        if self._cache_path and self._cache_path.exists():
            file_updated_at = datetime.fromtimestamp(self._cache_path.stat().st_mtime).isoformat(timespec="seconds")
        return {
            "profiles": sorted(self._profiles.keys()),
            "count": len(self._profiles),
            "updated_at": file_updated_at or (max(updated) if updated else None),
            "path": str(self._cache_path) if self._cache_path else None,
        }

    def set_auth(self, profile: str, payload: dict[str, Any]) -> None:
        self._set_section(profile, "auth", payload)

    def set_authenticated(self, profile: str, *, message: str = "已找到可用登录态。") -> None:
        self.set_auth(
            profile,
            {
                "authenticated": True,
                "status": "authenticated",
                "message": message,
            },
        )

    def set_xidian_terms(self, profile: str, payload: dict[str, Any]) -> None:
        self._set_section(profile, "xidian_terms", payload)

    def set_xidian_courses(self, profile: str, payload: dict[str, Any]) -> None:
        self._set_section(profile, "xidian_courses", payload)

    def set_replay_courses(self, profile: str, payload: dict[str, Any]) -> None:
        self._set_section(profile, "replay_courses", payload)

    def resource_bootstrap(self, profile: str) -> dict[str, Any]:
        state = self._profiles.get(profile, {})
        terms_payload = state.get("xidian_terms") or {}
        courses_payload = state.get("xidian_courses") or {}
        selected = terms_payload.get("selected")
        return {
            "auth": deepcopy(state.get("auth")),
            "page": {
                "terms": deepcopy(terms_payload.get("terms", [])),
                "selectedTerm": selected.get("value") if isinstance(selected, dict) else "",
                "courses": deepcopy(courses_payload.get("courses", [])),
            },
            "updated_at": state.get("updated_at"),
        }

    def replay_bootstrap(self, profile: str) -> dict[str, Any]:
        state = self._profiles.get(profile, {})
        courses_payload = state.get("replay_courses") or {}
        selected = courses_payload.get("selected_semester")
        return {
            "auth": deepcopy(state.get("auth")),
            "page": {
                "semesters": deepcopy(courses_payload.get("semesters", [])),
                "selectedSemester": selected.get("value") if isinstance(selected, dict) else "",
                "courses": deepcopy(courses_payload.get("courses", [])),
            },
            "updated_at": state.get("updated_at"),
        }
