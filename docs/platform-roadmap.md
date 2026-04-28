# Platform Roadmap

## Principle

Stabilize the trading platform before adding heavier orchestration.
The project should move in this order:

1. local reproducibility
2. Docker and Compose
3. CI safety checks
4. service boundaries and tracing
5. production orchestration

## Current Baseline

Already present:

- Makefile commands for install, test, compile, lint, paper runtime, health, and MT5 preflight
- GitHub Actions CI for safe non-live checks
- paper-safe deployment runtime
- health snapshots and Prometheus-style metrics
- MT5 preflight diagnostics
- unified journal contracts
- OMS/RiskEngine safety controls
- paper-safe Dockerfile and Compose `paper-runtime` profile

## Next Platform Steps

Docker/Compose first:

- validate the runtime image in an environment with Docker installed
- keep configs and artifact directories mounted explicitly
- keep Redis or any future infrastructure dependency through Compose
- keep live credentials outside images
- keep paper mode the default container command

Service boundaries only when needed:

- market data service
- strategy service
- OMS/risk service
- broker adapter service
- journal/export service

OpenTelemetry should wait until at least two real service boundaries exist.
Before that, health snapshots, metrics text, JSONL journal events, and logs are enough.

## Later, Not Now

Kubernetes, Helm, Argo, DVC, MLflow, and Feast should wait until:

- real datasets and model artifacts exist
- MT5 validation is complete
- paper/shadow reporting is stable
- incident and release runbooks are exercised
- Compose-based operation is boring and repeatable
