.PHONY: help install test lint run-demo run-agent-mock synth clean

help:
	@echo "Mini Debug Assist - Makefile targets:"
	@echo "  install          Install dependencies"
	@echo "  test             Run tests"
	@echo "  lint             Run linters (black, ruff)"
	@echo "  run-demo         Start demo FastAPI app locally"
	@echo "  run-agent-mock   Run agent in MOCK mode (no AWS)"
	@echo "  synth            Synthesize CDK infrastructure"
	@echo "  clean            Clean build artifacts"

install:
	pip install -e ".[dev,infra]"

test:
	pytest -v --cov=demo_app --cov=agent --cov=mcp_servers

lint:
	black --check demo_app agent mcp_servers tests
	ruff check demo_app agent mcp_servers tests

format:
	black demo_app agent mcp_servers tests
	ruff check --fix demo_app agent mcp_servers tests

run-demo:
	cd demo_app && uvicorn demo_app.main:app --reload --port 8000

run-agent-mock:
	python -m agent.cli --mode mock --issue tests/fixtures/keyerror_issue.yaml

synth:
	cd infra && cdk synth

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf build dist .pytest_cache .coverage htmlcov
	cd infra && rm -rf cdk.out
