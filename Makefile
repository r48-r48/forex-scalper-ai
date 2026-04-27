PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
PYTHONPYCACHEPREFIX ?= /tmp/scalper_ai_pycache

.PHONY: help install test compile lint typecheck run-paper health-paper metrics-paper mt5-preflight run-replay

help:
	@echo "Available targets:"
	@echo "  install        Install the project with dev and ML extras"
	@echo "  test           Run the full pytest suite"
	@echo "  compile        Compile src, tests, and scripts"
	@echo "  lint           Run ruff checks"
	@echo "  typecheck      Run mypy"
	@echo "  run-paper      Print paper runtime summary"
	@echo "  health-paper   Print paper runtime health snapshot"
	@echo "  metrics-paper  Print paper runtime metrics"
	@echo "  mt5-preflight  Run read-only MT5 preflight diagnostics"
	@echo "  run-replay     Show replay tick collector help"

install:
	$(PIP) install -e ".[dev,ml]"

test:
	$(PYTHON) -m pytest

compile:
	PYTHONPYCACHEPREFIX=$(PYTHONPYCACHEPREFIX) $(PYTHON) -m compileall src tests scripts

lint:
	$(PYTHON) -m ruff check src tests scripts

typecheck:
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
