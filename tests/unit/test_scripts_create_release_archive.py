"""Unit tests for the release archive creation script."""

from __future__ import annotations

import importlib.util
import tarfile
from pathlib import Path
from types import ModuleType


def test_iter_release_files_excludes_local_artifacts_and_caches(tmp_path: Path) -> None:
    script = _load_release_archive_module()
    _write_files(
        tmp_path,
        {
            ".DS_Store": "mac metadata",
            ".env": "secret=true",
            ".env.example": "SCALPER_AI_ENVIRONMENT=paper",
            ".env.local": "secret=true",
            ".git/config": "[core]",
            ".mypy_cache/state.json": "{}",
            ".pytest_cache/v/cache/nodeids": "[]",
            ".ruff_cache/content": "cache",
            ".venv/bin/python": "binary",
            "__MACOSX/._README.md": "resource fork",
            "__pycache__/module.pyc": "bytecode",
            "build/temp.txt": "build",
            "data/artifacts/paper-supervisor.json": "{}",
            "data/processed/features.parquet": "features",
            "data/raw/ticks.parquet": "ticks",
            "dist/old-release.tar.gz": "archive",
            "logs/runtime.log": "log",
            "README.md": "# Forex Scalper AI",
            "configs/base.yaml": "environment: research",
            "docs/current-state.md": "# Current State",
            "pyproject.toml": "[project]",
            "scripts/run_runtime.py": "print('runtime')",
            "src/scalper_ai/__init__.py": "",
            "tests/unit/test_example.py": "def test_example(): pass",
        },
    )

    manifest = script.iter_release_files(tmp_path)

    assert Path(".env.example") in manifest
    assert Path("README.md") in manifest
    assert Path("configs/base.yaml") in manifest
    assert Path("docs/current-state.md") in manifest
    assert Path("pyproject.toml") in manifest
    assert Path("scripts/run_runtime.py") in manifest
    assert Path("src/scalper_ai/__init__.py") in manifest
    assert Path("tests/unit/test_example.py") in manifest
    assert Path(".DS_Store") not in manifest
    assert Path(".env") not in manifest
    assert Path(".env.local") not in manifest
    assert Path(".git/config") not in manifest
    assert Path(".venv/bin/python") not in manifest
    assert Path("data/artifacts/paper-supervisor.json") not in manifest
    assert Path("data/processed/features.parquet") not in manifest
    assert Path("data/raw/ticks.parquet") not in manifest
    assert Path("dist/old-release.tar.gz") not in manifest
    assert Path("logs/runtime.log") not in manifest


def test_create_release_archive_writes_clean_prefixed_tarball(tmp_path: Path) -> None:
    script = _load_release_archive_module()
    root = tmp_path / "repo"
    output_path = tmp_path / "forex-scalper-ai-source.tar.gz"
    _write_files(
        root,
        {
            ".git/config": "[core]",
            "README.md": "# Forex Scalper AI",
            "configs/base.yaml": "environment: research",
            "data/artifacts/evidence.json": "{}",
            "docs/release-archive.md": "# Release Archive",
            "src/scalper_ai/__init__.py": "",
        },
    )

    manifest = script.create_release_archive(root, output_path, prefix="forex-scalper-ai")

    assert manifest == (
        Path("README.md"),
        Path("configs/base.yaml"),
        Path("docs/release-archive.md"),
        Path("src/scalper_ai/__init__.py"),
    )
    with tarfile.open(output_path, mode="r:gz") as archive:
        names = sorted(archive.getnames())

    assert names == [
        "forex-scalper-ai/README.md",
        "forex-scalper-ai/configs/base.yaml",
        "forex-scalper-ai/docs/release-archive.md",
        "forex-scalper-ai/src/scalper_ai/__init__.py",
    ]


def test_iter_release_files_omits_existing_output_archive_inside_root(tmp_path: Path) -> None:
    script = _load_release_archive_module()
    output_path = tmp_path / "forex-scalper-ai-source.tar.gz"
    _write_files(
        tmp_path,
        {
            "README.md": "# Forex Scalper AI",
            "forex-scalper-ai-source.tar.gz": "old archive",
        },
    )

    manifest = script.iter_release_files(tmp_path, output_path=output_path)

    assert manifest == (Path("README.md"),)


def _load_release_archive_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "create_release_archive.py"
    spec = importlib.util.spec_from_file_location("create_release_archive", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_files(root: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
