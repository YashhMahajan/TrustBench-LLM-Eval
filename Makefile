# Common tasks. `make help` lists them.
.PHONY: help install test lint selftest check edd-demo dashboard

help:            ## Show this list
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

install:         ## Install TrustBench and the development tools (exact tested versions)
	python -m pip install -c requirements.lock -e ".[dev]"

test:            ## Run the tests
	python -m pytest -q

lint:            ## Check the code
	ruff check src tests examples

selftest:        ## Check that TrustBench measures correctly
	trustbench selftest

check: lint test selftest   ## Everything CI runs for the Python code

edd-demo:        ## The Eval-Driven Development story on a support agent (no API key)
	python examples/support_agent/build_dataset.py
	python examples/support_agent/demo.py

dashboard:       ## Build the dashboard (needs pnpm)
	cd dashboard && pnpm install --frozen-lockfile && pnpm typecheck && pnpm build
