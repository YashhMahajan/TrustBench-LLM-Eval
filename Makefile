# Common tasks. `make help` lists them.
.PHONY: help install test lint writing selftest check edd-demo dashboard

help:            ## Show this list
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

install:         ## Install TrustBench and the development tools (exact tested versions)
	python -m pip install -c requirements.lock -e ".[dev]"

test:            ## Run the tests
	python -m pytest -q

lint:            ## Check the code
	ruff check src tests examples scripts

writing:         ## Check the writing rules in docs, comments and help text
	python scripts/writing_check.py README.md MEMORY.md docs/*.md examples/support_agent/README.md $$(git ls-files '*.py')

selftest:        ## Check that TrustBench measures correctly
	trustbench selftest

check: lint writing test selftest   ## Everything CI runs for the Python code

edd-demo:        ## The Eval-Driven Development story on a support agent (no API key)
	python examples/support_agent/build_dataset.py
	python examples/support_agent/demo.py

dashboard:       ## Build the dashboard (needs pnpm)
	cd dashboard && pnpm install --frozen-lockfile && pnpm typecheck && pnpm build
