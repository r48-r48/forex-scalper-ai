PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
PYTHONPYCACHEPREFIX ?= /tmp/scalper_ai_pycache
SUPERVISOR_ITERATIONS ?= 5
SUPERVISOR_HEALTH_INTERVAL_SECONDS ?= 0.1
SUPERVISOR_RECONCILIATION_INTERVAL_SECONDS ?= 0.1
SUPERVISOR_IDLE_SLEEP_SECONDS ?= 0.2
SUPERVISOR_ALERT_JSONL_PATH ?= /app/data/artifacts/paper-supervisor-alerts.jsonl

.PHONY: help install test compile lint lint-baseline typecheck typecheck-baseline run-paper health-paper metrics-paper mt5-preflight run-replay docker-build compose-paper compose-health compose-metrics compose-supervise

help:
	@echo "Available targets:"
	@echo "  install        Install the project with dev and ML extras"
	@echo "  test           Run the full pytest suite"
	@echo "  compile        Compile src, tests, and scripts"
	@echo "  lint           Run ruff checks"
	@echo "  lint-baseline  Run ruff checks with statistics for cleanup tracking"
	@echo "  typecheck      Run mypy"
	@echo "  typecheck-baseline Run mypy for cleanup tracking"
	@echo "  run-paper      Print paper runtime summary"
	@echo "  health-paper   Print paper runtime health snapshot"
	@echo "  metrics-paper  Print paper runtime metrics"
	@echo "  mt5-preflight  Run read-only MT5 preflight diagnostics"
	@echo "  run-replay     Show replay tick collector help"
	@echo "  docker-build   Build the paper-safe runtime image"
	@echo "  compose-paper  Run the paper runtime summary through Docker Compose"
	@echo "  compose-health Run the paper runtime health check through Docker Compose"
	@echo "  compose-metrics Run the paper runtime metrics surface through Docker Compose"
	@echo "  compose-supervise Run bounded paper supervisor cycles through Docker Compose"

install:
	$(PIP) install -e ".[dev,ml]"

test:
	$(PYTHON) -m pytest

compile:
	PYTHONPYCACHEPREFIX=$(PYTHONPYCACHEPREFIX) $(PYTHON) -m compileall src tests scripts

lint:
	$(PYTHON) -m ruff check src tests scripts

lint-baseline:
	$(PYTHON) -m ruff check src tests scripts --statistics

typecheck:
	$(PYTHON) -m mypy src

typecheck-baseline:
	$(PYTHON) -m mypy src

run-paper:
	$(PYTHON) scripts/run_runtime.py describe --config-name paper

health-paper:
	$(PYTHON) scripts/run_runtime.py health --config-name paper

metrics-paper:
	$(PYTHON) scripts/run_runtime.py metrics --config-name paper

mt5-preflight:
	$(PYTHON) scripts/mt5_smoke.py --config-name mt5 --preflight-only

run-replay:
	$(PYTHON) scripts/collect_ticks.py --help

docker-build:
	docker build -t forex-scalper-ai:local .

compose-paper:
	docker compose --profile paper run --rm paper-runtime describe --config-name paper

compose-health:
	docker compose --profile paper run --rm paper-runtime health --config-name paper

compose-metrics:
	docker compose --profile paper run --rm paper-runtime metrics --config-name paper

compose-supervise:
	docker compose --profile paper run --rm paper-runtime supervise --config-name paper --max-iterations $(SUPERVISOR_ITERATIONS) --health-interval-seconds $(SUPERVISOR_HEALTH_INTERVAL_SECONDS) --reconciliation-interval-seconds $(SUPERVISOR_RECONCILIATION_INTERVAL_SECONDS) --idle-sleep-seconds $(SUPERVISOR_IDLE_SLEEP_SECONDS) --alert-jsonl-path $(SUPERVISOR_ALERT_JSONL_PATH)
