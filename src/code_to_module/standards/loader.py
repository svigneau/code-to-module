"""Standards loader for nf-core conventions schema."""

from __future__ import annotations

import json
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import httpx

_REQUIRED_SCHEMA_KEYS = frozenset([
    "schema_version", "valid_labels", "label_resources",
    "versions_use_topic_channels", "docker_registry", "singularity_registry",
    "conda_channels", "meta_yml_required_fields", "ext_args_pattern",
    "ext_prefix_pattern", "known_tools", "tier_thresholds",
    "helper_filename_patterns", "helper_dirname_patterns",
    "test_data_base_path", "test_data_index",
    "derivation_templates", "chain_modules",
])

_DEFAULT_UPDATE_URL = (
    "https://raw.githubusercontent.com/nf-core/nf-core-standards/main/"
    "nf_core_standards.json"
)


def _load_bundled() -> dict[str, Any]:
    schema_bytes = (
        files("code_to_module.standards") / "data" / "nf_core_standards.json"
    ).read_bytes()
    return cast(dict[str, Any], json.loads(schema_bytes))


def _validate_schema(data: dict[str, Any]) -> None:
    missing = _REQUIRED_SCHEMA_KEYS - data.keys()
    if missing:
        raise ValueError(f"Schema missing required keys: {sorted(missing)}")


class Standards:
    """Loaded nf-core standards schema."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        if data is None:
            data = _load_bundled()
        _validate_schema(data)
        self._data = data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Standards:
        """Create a Standards instance from a dict (used in tests)."""
        return cls(data)

    # ── Schema accessor properties ────────────────────────────────────────────

    @property
    def schema_version(self) -> str:
        return cast(str, self._data["schema_version"])

    @property
    def last_updated(self) -> str:
        return cast(str, self._data.get("last_updated", ""))

    @property
    def valid_labels(self) -> list[str]:
        return list(self._data["valid_labels"])

    @property
    def label_resources(self) -> dict[str, dict[str, Any]]:
        return dict(self._data["label_resources"])

    @property
    def versions_use_topic_channels(self) -> bool:
        return bool(self._data["versions_use_topic_channels"])

    @property
    def docker_registry(self) -> str:
        return cast(str, self._data["docker_registry"])

    @property
    def singularity_registry(self) -> str:
        return cast(str, self._data["singularity_registry"])

    @property
    def conda_channels(self) -> list[str]:
        return list(self._data["conda_channels"])

    @property
    def meta_yml_required_fields(self) -> list[str]:
        return list(self._data["meta_yml_required_fields"])

    @property
    def edam_for(self) -> dict[str, str]:
        """Map of format name → EDAM ontology URI."""
        return dict(self._data.get("edam_for", {}))

    @property
    def ext_args_pattern(self) -> str:
        return cast(str, self._data["ext_args_pattern"])

    @property
    def ext_prefix_pattern(self) -> str:
        return cast(str, self._data["ext_prefix_pattern"])

    @property
    def known_tools(self) -> list[str]:
        return list(self._data["known_tools"])

    @property
    def tier_thresholds(self) -> dict[str, Any]:
        return dict(self._data["tier_thresholds"])

    @property
    def helper_filename_patterns(self) -> list[str]:
        return list(self._data["helper_filename_patterns"])

    @property
    def helper_dirname_patterns(self) -> list[str]:
        return list(self._data["helper_dirname_patterns"])

    @property
    def test_data_base_path(self) -> str:
        return cast(str, self._data["test_data_base_path"])

    @property
    def test_data_index(self) -> list[dict[str, Any]]:
        return list(self._data["test_data_index"])

    @property
    def derivation_templates(self) -> dict[str, dict[str, Any]]:
        return dict(self._data.get("derivation_templates", {}))

    @property
    def chain_modules(self) -> dict[str, dict[str, Any]]:
        return dict(self._data.get("chain_modules", {}))

    def find_test_data(self, tags: list[str]) -> list[dict[str, Any]]:
        """Return test_data_index entries that match ALL given tags, sorted by size_kb."""
        tag_set = set(tags)
        matches = [
            entry for entry in self.test_data_index
            if tag_set.issubset(set(entry.get("tags", [])))
        ]
        return sorted(matches, key=lambda e: e.get("size_kb", 0))

    # ── Staleness helpers ─────────────────────────────────────────────────────

    def is_stale(self, max_age_days: int = 30) -> bool:
        """Return True if the schema's last_updated date is older than max_age_days."""
        last = self.last_updated
        if not last:
            return True
        try:
            last_date = date.fromisoformat(last)
        except ValueError:
            return True
        return (date.today() - last_date).days > max_age_days

    def check_for_updates(self) -> str | None:
        """Check remote for a newer schema version.

        Returns the newer version string if one is available, or None.
        Never raises — network errors are silently swallowed.
        """
        url = self._data.get("update_url", _DEFAULT_UPDATE_URL)
        try:
            resp = httpx.get(url, timeout=5.0, follow_redirects=True)
            if resp.status_code != 200:
                return None
            remote_version = cast(str, resp.json().get("schema_version", ""))
            if remote_version and remote_version != self.schema_version:
                return remote_version
        except Exception:
            pass
        return None

    @classmethod
    def fetch_and_save(cls, schema_path: Path) -> Standards:
        """Fetch the latest schema from remote, validate, and save to schema_path.

        Raises ValueError if the fetched schema is malformed.
        The original file is NOT overwritten if validation fails.
        """
        resp = httpx.get(_DEFAULT_UPDATE_URL, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()

        # Validate before writing so we never corrupt the stored file
        missing = _REQUIRED_SCHEMA_KEYS - data.keys()
        if missing:
            raise ValueError(f"Fetched schema missing required keys: {sorted(missing)}")

        schema_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return cls(data)


# ── Module-level helper functions ─────────────────────────────────────────────


def find_test_data(tags: list[str]) -> list[dict[str, Any]]:
    """Return test_data_index entries that match ALL given tags, sorted by size_kb."""
    return get_standards().find_test_data(tags)


def derivation_template_for(tags: list[str]) -> dict[str, Any] | None:
    """Return the first derivation template whose applicable_tags overlap with tags."""
    s = get_standards()
    tag_set = set(tags)
    for tmpl in s.derivation_templates.values():
        if tag_set & set(tmpl.get("applicable_tags", [])):
            return dict(tmpl)
    return None


def chain_module_for(tags: list[str]) -> dict[str, Any] | None:
    """Return the first chain_module entry whose key or produces_tags overlaps with tags."""
    s = get_standards()
    tag_set = set(tags)
    for key, module in s.chain_modules.items():
        if key in tag_set:
            return dict(module)
        produces = set(module.get("produces_tags", []))
        if tag_set & produces:
            return dict(module)
    return None


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: Standards | None = None


def get_standards() -> Standards:
    """Return the singleton Standards instance (loads bundled schema on first call)."""
    global _instance
    if _instance is None:
        _instance = Standards()
    return _instance
