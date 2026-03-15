"""Match ChannelSpec format tags to nf-core/test-datasets index entries."""

from __future__ import annotations

from pydantic import BaseModel

from code_to_module.models import ChannelSpec
from code_to_module.standards import Standards


class TestDataMatch(BaseModel):
    """A resolved test-dataset entry for a channel."""

    id: str
    paths: list[str]
    tags: list[str]
    organism: str
    size_kb: int
    resolved_paths: list[str]


def match(channel: ChannelSpec, standards: Standards) -> TestDataMatch | None:
    """Find the smallest nf-core/test-datasets entry covering all of channel.format_tags.

    Returns None when:
    - channel.format_tags is empty, or
    - no index entry covers all requested tags.
    """
    if not channel.format_tags:
        return None

    candidates = standards.find_test_data(channel.format_tags)
    if not candidates:
        return None

    # find_test_data already sorts by size_kb ascending; take the smallest
    best = candidates[0]

    base = standards.test_data_base_path.rstrip("/")
    branch = "modules"
    resolved_paths = [f"{base}/{branch}/{p}" for p in best.get("paths", [])]

    return TestDataMatch(
        id=best["id"],
        paths=best["paths"],
        tags=best["tags"],
        organism=best.get("organism", ""),
        size_kb=best.get("size_kb", 0),
        resolved_paths=resolved_paths,
    )
