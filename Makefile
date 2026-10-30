# SilverFile shortcuts. `make help` lists them.
# Uses .venv/bin/python when `make install` has created it, otherwise python3.

VENV   := .venv
PY     := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
REQS   := $(if $(wildcard requirements.lock),requirements.lock,requirements.txt)
OFFLINE := SILVERFILE_OFFLINE=1

.PHONY: help install lock test test-fast demo1 demo2 dashboard taxpayer sample-trace docker-build docker-up docker-demo1 docker-test clean

help:  ## List targets
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-14s %s\n", $$1, $$2}'

install:  ## Create .venv and install dependencies (exact versions from requirements.lock if present)
	test -d $(VENV) || python3.11 -m venv $(VENV) || python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r $(REQS)

lock:  ## Write requirements.lock with the exact versions installed in .venv
	$(VENV)/bin/pip freeze --exclude-editable | sort > requirements.lock
	@echo "Wrote requirements.lock; commit it so Docker and CI use the same versions."

test:  ## Run the whole test suite offline (tax_math in-process)
	$(OFFLINE) TAX_MATH_INPROCESS=1 $(PY) -m pytest -q

test-fast:  ## Only the tax_math and Computation tests
	$(OFFLINE) TAX_MATH_INPROCESS=1 $(PY) -m pytest -q tests/test_tax_math.py tests/test_computation.py tests/test_mcp_stdio.py

demo1:  ## Eval case 1: clean run, no human review
	$(OFFLINE) $(PY) run.py --case demo/eval_case_1/case.json

demo2:  ## Eval case 2: raises a HITL event, resolved from the case file or the dashboard
	$(OFFLINE) $(PY) run.py --case demo/eval_case_2/case.json

dashboard:  ## Staff dashboard on http://localhost:8000
	$(OFFLINE) $(PY) run.py --dashboard

taxpayer:  ## Taxpayer page on http://localhost:8001
	$(OFFLINE) $(PY) run.py --taxpayer

sample-trace: demo1  ## Run eval case 1, then keep its trace as traces/sample_eval_case_1.jsonl
	cp "$$(ls -t traces/eval_case_1-*.jsonl | head -1)" traces/sample_eval_case_1.jsonl
	@echo "Updated traces/sample_eval_case_1.jsonl"

docker-build:  ## Build the image
	docker compose build

docker-up:  ## Start the dashboard (8000) and taxpayer page (8001) in Docker
	docker compose up dashboard taxpayer

docker-demo1:  ## Run eval case 1 in Docker (writes ./outputs)
	docker compose run --rm app python run.py --case demo/eval_case_1/case.json

docker-test:  ## Run the test suite inside the image
	docker compose run --rm app python -m pytest -q

clean:  ## Remove caches (keeps outputs, traces and the checkpoint DB)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache
