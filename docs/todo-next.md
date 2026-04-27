# Todo Next

## Active Target

- POST-PHASE — Hardening, live integration refinement, and operational stabilization

## Deliverables

- full Python 3.11+ environment validation, including repo-wide test execution in the actual target interpreter
- real-terminal validation and refinement of the new MT5-backed client, reusing the existing reconciliation and connectivity contracts plus the new preflight/auto-discovery layer
- deeper operational hardening for long-running services, alerting, and dependency supervision beyond the current broker health checks
- production-readiness cleanup around packaging and startup ergonomics

## Must-Have Capabilities

- keep paper mode as the default runtime posture unless explicit live-safe conditions are met
- preserve clear boundaries between research, paper, and live execution concerns
- validate the full stack in the actual target runtime, not only the reduced local thread environment
- keep replay/mock fallback available when broker or infrastructure dependencies are unavailable

## Implementation Guidance

- keep deployment and hardening work separate from domain, model, and execution math
- reuse the PHASE 12 runtime, health, and metrics surfaces instead of duplicating startup logic
- favor explicit safety checks, reconciliation, and observability over implicit automation
- use the new paper-safe runtime as the default fallback for environments missing live dependencies

## Validation Goal

- run repository-wide `pytest` in Python 3.11+ with the full dependency set installed
- add targeted validation around the MT5-backed live adapter, real terminal behavior, preflight diagnostics, reconciliation, and long-running runtime behavior
- keep deployment CLI and paper-safe runtime wiring working while hardening the live path

## After This Roadmap

- evaluate whether a new formal phase breakdown is needed for live rollout and operations maturity
