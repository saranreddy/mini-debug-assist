.PHONY: test
test:
	pytest -xvs

.PHONY: test-coverage
test-coverage:
	pytest --cov=agent --cov=demo_app --cov-report=html --cov-report=term

.PHONY: lint
lint:
	ruff check agent demo_app tests
	black --check agent demo_app tests

.PHONY: format
format:
	black agent demo_app tests
	ruff check --fix agent demo_app tests

.PHONY: e2e-local
e2e-local:
	@echo "Running end-to-end test in local mode with moto..."
	MCP_MOCK_MODE=true \
	AGENT_MODE=aws \
	DEDUP_TABLE_NAME=test-dedup \
	DEMO_APP_LOG_GROUP=/test/logs \
	python -m pytest tests/test_e2e_local.py -xvs

.PHONY: docker-build-agent
docker-build-agent:
	docker build -t mini-debug-assist-agent -f agent/Dockerfile .

.PHONY: cdk-synth
cdk-synth:
	cd infra && npx cdk synth

.PHONY: cdk-deploy
cdk-deploy:
	cd infra && npx cdk deploy --all

.PHONY: clean
clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	rm -rf htmlcov .coverage
