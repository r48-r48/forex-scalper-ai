"""Persistence helpers for the unified audit journal."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from scalper_ai.journal.events import JournalEvent


class JsonlJournalWriter:
    """Append immutable journal events to a JSONL audit file."""

    def __init__(self, path: Path, *, append: bool = True) -> None:
        if not path.name:
            raise ValueError("journal path must include a file name.")
        self._path = path
        self._append = append
        self._has_written = False

    @property
    def path(self) -> Path:
        """Return the output JSONL path."""

        return self._path

    def write(self, event: JournalEvent) -> Path:
        """Persist one event and return the output path."""

        return self.write_batch((event,))

    def write_batch(self, events: Sequence[JournalEvent]) -> Path:
        """Persist a batch of events and return the output path."""

        if not events:
            return self._path

        self._path.parent.mkdir(parents=True, exist_ok=True)
        mode = "ab" if self._append or self._has_written else "wb"
        with self._path.open(mode) as output:
            for event in events:
                output.write(event.to_json_bytes(exclude_none=False))
                output.write(b"\n")
        self._has_written = True
        return self._path


def read_jsonl_journal(path: Path) -> tuple[JournalEvent, ...]:
    """Read a JSONL audit file back into immutable journal events."""

    events: list[JournalEvent] = []
    with path.open("rb") as input_file:
        for line in input_file:
            normalized = line.strip()
            if normalized:
                events.append(JournalEvent.from_json_bytes(normalized))
    return tuple(events)
