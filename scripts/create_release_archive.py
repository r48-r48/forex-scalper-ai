"""Create a clean source archive for release/export handoff."""

from __future__ import annotations

import argparse
import os
import tarfile
from collections.abc import Iterable
from pathlib import Path

EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__MACOSX",
        "__pycache__",
        "htmlcov",
        "logs",
        "node_modules",
        "redis-data",
    }
)
EXCLUDED_TOP_LEVEL_DIRS = frozenset({"build", "dist"})
EXCLUDED_PATH_PREFIXES = (
    Path("data") / "artifacts",
    Path("data") / "processed",
    Path("data") / "raw",
)
EXCLUDED_FILE_NAMES = frozenset(
    {
        ".coverage",
        ".DS_Store",
        ".env",
        "coverage.xml",
    }
)
EXCLUDED_FILE_SUFFIXES = frozenset(
    {
        ".db",
        ".egg",
        ".log",
        ".pyc",
        ".pyo",
        ".sqlite",
        ".sqlite3",
        ".whl",
    }
)
EXCLUDED_ARCHIVE_SUFFIXES = (
    ".tar",
    ".tar.gz",
    ".tgz",
    ".zip",
)


def repo_root() -> Path:
    """Return the repository root from the script location."""

    return Path(__file__).resolve().parents[1]


def iter_release_files(root: Path, *, output_path: Path | None = None) -> tuple[Path, ...]:
    """Return sorted release file paths relative to ``root``."""

    resolved_root = root.resolve()
    resolved_output = output_path.resolve() if output_path is not None else None
    selected: list[Path] = []

    for directory, dir_names, file_names in os.walk(resolved_root):
        directory_path = Path(directory)
        relative_directory = directory_path.relative_to(resolved_root)
        dir_names[:] = sorted(
            name for name in dir_names if is_release_directory(relative_directory / name)
        )

        for file_name in sorted(file_names):
            path = directory_path / file_name
            if path.is_symlink() or not path.is_file():
                continue
            if resolved_output is not None and path.resolve() == resolved_output:
                continue
            relative_path = path.relative_to(resolved_root)
            if is_release_file(relative_path):
                selected.append(relative_path)

    return tuple(sorted(selected))


def is_release_directory(relative_path: Path) -> bool:
    """Return whether a relative directory may contain release source files."""

    parts = relative_path.parts
    if not parts:
        return False
    if _has_excluded_part(parts):
        return False
    if parts[0] in EXCLUDED_TOP_LEVEL_DIRS:
        return False
    if _has_excluded_prefix(relative_path):
        return False
    return not any(part.endswith(".egg-info") for part in parts)


def is_release_file(relative_path: Path) -> bool:
    """Return whether a relative path belongs in the clean source archive."""

    parts = relative_path.parts
    if not parts:
        return False
    if len(parts) > 1 and not is_release_directory(Path(*parts[:-1])):
        return False

    file_name = relative_path.name
    if file_name in EXCLUDED_FILE_NAMES:
        return False
    if file_name.startswith("._"):
        return False
    if file_name.startswith(".env.") and file_name != ".env.example":
        return False
    if any(part.endswith(".egg-info") for part in parts):
        return False
    if any(file_name.endswith(suffix) for suffix in EXCLUDED_ARCHIVE_SUFFIXES):
        return False
    return relative_path.suffix not in EXCLUDED_FILE_SUFFIXES


def create_release_archive(
    root: Path,
    output_path: Path,
    *,
    prefix: str | None = None,
) -> tuple[Path, ...]:
    """Create a gzip-compressed tar source archive and return its manifest."""

    resolved_root = root.resolve()
    resolved_output = output_path.resolve()
    archive_prefix = _clean_archive_prefix(prefix or resolved_root.name)
    manifest = iter_release_files(resolved_root, output_path=resolved_output)

    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(resolved_output, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        for relative_path in manifest:
            source_path = resolved_root / relative_path
            archive_name = Path(archive_prefix) / relative_path
            archive.add(
                source_path,
                arcname=archive_name.as_posix(),
                recursive=False,
                filter=_normalize_tar_info,
            )

    return manifest


def format_manifest(paths: Iterable[Path]) -> str:
    """Render one relative path per line for dry-run/list output."""

    return "\n".join(path.as_posix() for path in paths)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Create a clean source archive without local caches or evidence artifacts.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=repo_root(),
        help="Repository root to archive. Defaults to this script's repository.",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Archive path. Defaults to dist/<root-name>-source.tar.gz.",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Top-level directory name inside the archive. Defaults to the root directory name.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the selected file manifest and do not create an archive.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the selected file manifest. With --dry-run, no archive is created.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    args = parse_args()
    root = args.root.resolve()
    output_path = args.output_path or root / "dist" / f"{root.name}-source.tar.gz"

    if args.dry_run:
        manifest = iter_release_files(root, output_path=output_path)
        print(format_manifest(manifest))
        return

    manifest = create_release_archive(root, output_path, prefix=args.prefix)
    if args.list:
        print(format_manifest(manifest))
        return
    print(f"Created {output_path} with {len(manifest)} files.")


def _has_excluded_part(parts: tuple[str, ...]) -> bool:
    return any(part in EXCLUDED_DIR_NAMES for part in parts)


def _has_excluded_prefix(relative_path: Path) -> bool:
    return any(
        relative_path == excluded_path or excluded_path in relative_path.parents
        for excluded_path in EXCLUDED_PATH_PREFIXES
    )


def _clean_archive_prefix(value: str) -> str:
    cleaned = value.strip().strip("/")
    if not cleaned or cleaned in {".", ".."} or "/" in cleaned:
        raise ValueError("prefix must be a single non-empty relative directory name.")
    return cleaned


def _normalize_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


if __name__ == "__main__":
    main()
