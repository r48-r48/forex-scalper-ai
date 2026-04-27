"""Filesystem path helpers."""

from __future__ import annotations

from pathlib import Path


def resolve_repo_root() -> Path:
    """Return the repository root based on the package layout."""

    return Path(__file__).resolve().parents[3]
