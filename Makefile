.PHONY: help doctor bootstrap deploy destroy setup-secrets trigger-bug smoke demo-local test lint format clean e2e-local

# Default target
help:
	@echo "Mini Debug Assist - Makefile targets"
	@echo ""
	@echo "Setup:"
	@echo "  make doctor         - Check prerequisites (AWS, CDK, Docker, Python, Bedrock access)"
	@echo "  make bootstrap      - Run cdk bootstrap (one-time per account/region)"
	@echo "  make setup-secrets  - Store GitHub token in Secrets Manager"
	@echo ""
	@echo "Deploy:"
	@echo "  make deploy         - Deploy agent infrastructure to AWS"
	@echo "  make smoke          - Post-deployment validation"
	@echo "  make destroy        - Tear down all AWS resources"
	@echo ""
	@echo "Demo:"
	@echo "  make demo-local     - Run full pipeline in mock mode (no AWS)"
	@echo "  make trigger-bug    - Trigger demo app bug to wake the agent"
	@echo ""
	@echo "Development:"
	@echo "  make test           - Run test suite"
	@echo "  make e2e-local      - Run end-to-end tests locally"
	@echo "  make lint           - Run linters"
	@echo "  make format         - Format code"
	@echo "  make clean          - Clean temporary files"

doctor:
	@echo "Checking prerequisites..."
	@python3 scripts/doctor.py

bootstrap:
	@echo "Bootstrapping CDK (one-time setup)..."
	cd infra && cdk bootstrap

deploy:
	@echo "Deploying agent infrastructure..."
	@python3 infra/deploy_config.py
	cd infra && cdk deploy --all --require-approval never

destroy:
	@echo "Destroying all resources..."
	cd infra && cdk destroy --all --force

setup-secrets:
	@echo "Setting up GitHub token in Secrets Manager..."
	@bash scripts/setup_github_token.sh

trigger-bug:
	@echo "Triggering demo app bug..."
	@python3 scripts/trigger_bug.py

smoke:
	@echo "Running post-deployment smoke tests..."
	@python3 scripts/smoke.py

demo-local:
	@echo "Running agent in mock mode..."
	@python3 -m agent.cli --mode=mock --issue=tests/fixtures/keyerror_issue.yaml

test:
	@echo "Running test suite..."
	@pytest tests/ -v

e2e-local:
	@echo "Running end-to-end tests..."
	@pytest tests/test_e2e_local.py -v

lint:
	@echo "Running linters..."
	@ruff check agent/ tests/ demo_app/ mcp_servers/
	@mypy agent/ --ignore-missing-imports

format:
	@echo "Formatting code..."
	@black agent/ tests/ demo_app/ mcp_servers/ infra/
	@ruff check --fix agent/ tests/ demo_app/ mcp_servers/

clean:
	@echo "Cleaning temporary files..."
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@rm -rf .coverage htmlcov/ .ruff_cache/
