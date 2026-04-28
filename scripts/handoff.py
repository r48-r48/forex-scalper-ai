"""Utility script for session handoff and resume prompts."""

from __future__ import annotations

import argparse
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root from the script location."""

    return Path(__file__).resolve().parents[1]


def handoff_path() -> Path:
    """Return the canonical handoff file path."""

    return repo_root() / "AGENT_HANDOFF.md"


def checkpoint_path() -> Path:
    """Return the canonical persistent session-checkpoint path."""

    return repo_root() / "SESSION_CHECKPOINT.md"


def build_resume_prompt() -> str:
    """Return a ready-to-paste prompt for a new Codex window."""

    path = handoff_path()
    agents_path = repo_root() / "AGENTS.md"
    current_state_path = repo_root() / "docs" / "current-state.md"
    todo_next_path = repo_root() / "docs" / "todo-next.md"
    checkpoint = checkpoint_path()
    return (
        f"Read `{agents_path}`, `{path}`, `{current_state_path}`, "
        f"`{todo_next_path}`, and `{checkpoint}`. "
        "Then inspect the repository and continue from the current phase only. "
        "Follow the existing phased format, keep the implementation production-minded, "
        "and do not redo already completed phases unless a blocking bug is found."
    )


def show_handoff() -> None:
    """Print the current handoff file contents."""

    print(handoff_path().read_text(encoding="utf-8"))


def show_status() -> None:
    """Print a concise status summary extracted from the handoff file."""

    text = handoff_path().read_text(encoding="utf-8").splitlines()
    capture = False
    for line in text:
        if line.strip() == "## Current State":
            capture = True
            continue
        if capture and line.startswith("## "):
            break
        if capture:
            print(line)


def show_checkpoint() -> None:
    """Print the current persistent session checkpoint."""

    print(checkpoint_path().read_text(encoding="utf-8"))


def main() -> None:
    """CLI entrypoint."""

    parser = argparse.ArgumentParser(
        description="Session handoff helper for the forex-scalper-ai repo."
    )
    parser.add_argument(
        "action",
        choices=("path", "show", "prompt", "status", "checkpoint-path", "checkpoint"),
        help="What handoff information to print.",
    )
    args = parser.parse_args()

    if args.action == "path":
        print(handoff_path())
        return
    if args.action == "checkpoint-path":
        print(checkpoint_path())
        return
    if args.action == "show":
        show_handoff()
        return
    if args.action == "checkpoint":
        show_checkpoint()
        return
    if args.action == "prompt":
        print(build_resume_prompt())
        return
    if args.action == "status":
        show_status()
        return


if __name__ == "__main__":
    main()
