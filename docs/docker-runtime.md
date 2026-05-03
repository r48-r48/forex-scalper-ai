# Docker Runtime

Snapshot date: 2026-05-03

## Purpose

The Docker runtime is a paper-safe operational wrapper around the existing PHASE 12 runtime.
It is intentionally not a live-trading image and does not include broker credentials.

## Image

`Dockerfile` builds `forex-scalper-ai:local` from `python:3.12-slim`, installs the package without dev/ML extras, copies configs/scripts, and starts as an unprivileged `scalper_ai` user.

Default command:

```bash
python scripts/run_runtime.py describe --config-name paper
```

## Compose

`docker-compose.yml` now defines:

- `redis`: development Redis dependency
- `paper-runtime`: profile-gated paper-safe runtime tool container

The paper container mounts:

- `./configs:/app/configs:ro`
- `./data:/app/data`

It sets `SCALPER_AI_BROKER_LIVE_ENABLED=false` and `SCALPER_AI_BROKER_LIVE_ADAPTER=unconfigured`, so it remains paper-safe by default.

## Commands

```bash
make docker-build
make compose-paper
make compose-health
make compose-metrics
make compose-supervise
```

Equivalent raw commands:

```bash
docker build -t forex-scalper-ai:local .
docker compose --profile paper run --rm paper-runtime describe --config-name paper
docker compose --profile paper run --rm paper-runtime health --config-name paper
docker compose --profile paper run --rm paper-runtime metrics --config-name paper
docker compose --profile paper run --rm paper-runtime supervise --config-name paper --max-iterations 5 --health-interval-seconds 0.1 --reconciliation-interval-seconds 0.1 --idle-sleep-seconds 0.2 --alert-jsonl-path /app/data/artifacts/paper-supervisor-alerts.jsonl
```

## Current Validation Status

Initial source/config check on 2026-04-28:

- Local macOS Codex environment: no `docker` binary was available.
- Parallels Windows 11 VM: no `docker` command was available.
- `docker-compose.yml` parsed successfully with PyYAML.
- Local paper runtime fallback passed without Docker:
  - `.venv/bin/python scripts/run_runtime.py describe --config-name paper`
  - `.venv/bin/python scripts/run_runtime.py health --config-name paper`
  - `.venv/bin/python scripts/run_runtime.py metrics --config-name paper`

Docker Desktop validation on 2026-05-03:

- `docker version` reached Docker Desktop `4.71.0` / Engine `29.4.1` through the
  `desktop-linux` context.
- `docker build -t forex-scalper-ai:local .` completed successfully from
  `python:3.12-slim` and installed the package without dev/ML extras.
- `docker compose --profile paper run --rm paper-runtime describe --config-name paper`
  started the runtime in paper mode and returned `effective_mode=paper`.
- `docker compose --profile paper run --rm paper-runtime health --config-name paper`
  returned `overall_status=pass`, including storage, execution mode, risk guardrail,
  reconciliation, and metrics-surface checks.
- `docker compose --profile paper run --rm paper-runtime metrics --config-name paper`
  emitted the Prometheus-style runtime metrics surface.
- `docker compose --profile paper run --rm paper-runtime supervise --config-name paper
  --max-iterations 5 --health-interval-seconds 0.1
  --reconciliation-interval-seconds 0.1 --idle-sleep-seconds 0.2
  --alert-jsonl-path /app/data/artifacts/paper-supervisor-alerts.jsonl` completed
  five bounded supervisor iterations with `overall_status=pass`, `health_due=true`,
  `reconciliation_due=true`, rendered metrics, and zero alerts/errors.
- `docker compose --profile paper down` was run afterward to stop and remove the
  test Redis container/network.

## Safety Notes

- Do not bake `.env`, broker credentials, or live confirmation tokens into the image.
- Keep MT5/live validation outside this Linux paper runtime unless a dedicated Windows/MT5 runtime design is added.
- Use bind-mounted `data/` only for local artifacts; production storage should be decided after paper/shadow runs are stable.
