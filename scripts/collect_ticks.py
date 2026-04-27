"""Replay tick collection script writing canonical raw data to Parquet."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from scalper_ai.config import load_app_config
from scalper_ai.data import BufferedBatchWriter, RawParquetWriter, ReplayCollector, ReplayTickSource
from scalper_ai.utils import configure_logging, get_logger, resolve_repo_root

app = typer.Typer(add_completion=False, help="Collect canonical tick data from replay files.")


@app.command()
def main(
    input_path: Path = typer.Argument(..., exists=True, readable=True, help="Replay JSONL or Parquet file."),
    config_name: str = typer.Option("research", help="Config overlay to load."),
    output_dir: Optional[Path] = typer.Option(None, help="Optional raw output root override."),
    batch_size: Optional[int] = typer.Option(None, help="Optional batch size override."),
    dataset_name: str = typer.Option("ticks", help="Dataset partition name under raw storage."),
) -> None:
    """Collect replay ticks into the raw Parquet dataset."""

    config = load_app_config(config_name=config_name)
    configure_logging(config.logging)
    logger = get_logger("scalper_ai.scripts.collect_ticks")

    repo_root = resolve_repo_root()
    raw_root = output_dir or (repo_root / config.directories.raw_dir)
    writer = RawParquetWriter(
        root_dir=raw_root,
        dataset_name=dataset_name,
        compression=config.ingestion.parquet_compression,
    )
    buffered_writer = BufferedBatchWriter(
        writer=writer,
        batch_size=batch_size or config.ingestion.batch_size,
    )
    collector = ReplayCollector(source=ReplayTickSource(input_path), writer=buffered_writer)
    stats = collector.run()

    logger.info(
        "Replay tick collection complete.",
        extra={
            "component": "collect_ticks",
            "event": "replay_collection_complete",
            "events_read": stats.events_read,
            "files_written": stats.batches_written,
        },
    )
    typer.echo(f"Collected {stats.events_read} ticks into {stats.batches_written} parquet file(s).")


if __name__ == "__main__":
    app()
