# Release Archive

Use `scripts/create_release_archive.py` to create a clean source export for review,
handoff, or release packaging. The archive is built from a tested manifest function
and omits local state, caches, evidence, generated datasets, and previous build outputs.

## Dry Run

Preview the exact source manifest without writing an archive:

```bash
.venv/bin/python scripts/create_release_archive.py --dry-run
```

To print the manifest after creating the archive:

```bash
.venv/bin/python scripts/create_release_archive.py --list
```

## Create Archive

The default output path is `dist/forex-scalper-ai-source.tar.gz`:

```bash
.venv/bin/python scripts/create_release_archive.py
```

Use an explicit path or top-level archive prefix when needed:

```bash
.venv/bin/python scripts/create_release_archive.py \
  --output-path /tmp/forex-scalper-ai-source.tar.gz \
  --prefix forex-scalper-ai
```

## Exclusions

The source archive excludes:

- repository and virtual environment internals: `.git`, `.venv`
- Python and tool caches: `__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`
- macOS metadata: `.DS_Store`, `__MACOSX`, resource fork files
- local secrets: `.env`, `.env.*` except `.env.example`
- generated or evidence data: `data/artifacts`, `data/raw`, `data/processed`
- build/runtime outputs: `build`, `dist`, `htmlcov`, `logs`, `redis-data`
- compiled/cache/archive byproducts such as `*.pyc`, `*.log`, `*.sqlite`, `*.tar.gz`,
  `*.tgz`, `*.zip`, and `*.whl`

Normal source, tests, documentation, configs, CI files, and packaging metadata are
included when they are regular files under the selected root.
