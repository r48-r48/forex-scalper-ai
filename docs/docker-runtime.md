# Docker Runtime

Snapshot date: 2026-04-28

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
```

Equivalent raw commands:

```bash
docker build -t forex-scalper-ai:local .
docker compose --profile paper run --rm paper-runtime describe --config-name paper
docker compose --profile paper run --rm paper-runtime health --config-name paper
docker compose --profile paper run --rm paper-runtime metrics --config-name paper
```

## Safety Notes

- Do not bake `.env`, broker credentials, or live confirmation tokens into the image.
- Keep MT5/live validation outside this Linux paper runtime unless a dedicated Windows/MT5 runtime design is added.
- Use bind-mounted `data/` only for local artifacts; production storage should be decided after paper/shadow runs are stable.
