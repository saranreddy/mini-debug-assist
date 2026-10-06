# Agent Dockerfile
# Builds a container that can run the debug agent in AWS mode.
#
# The build context is the REPO ROOT (CDK: infra/stacks/agent_stack.py builds it
# with file="agent/Dockerfile"), so every COPY below is relative to the repo root.
# Local test, from the repo root:
#   docker build -t mini-debug-assist-agent -f agent/Dockerfile .

FROM public.ecr.aws/docker/library/python:3.11-slim

# System dependencies. git is required: the agent applies its fixes with
# `git apply` (agent/patching.py); `patch` is not used and not installed.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml README.md ./
COPY demo_app ./demo_app
COPY agent ./agent
COPY mcp_servers ./mcp_servers
COPY skills ./skills
COPY tests ./tests

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Set environment defaults. No region here: the ECS task sets AWS_REGION to the
# stack's region, and every boto3 client passes it explicitly.
ENV PYTHONUNBUFFERED=1

# Run agent in AWS mode by default
# Container overrides will provide ISSUE_SOURCE and other vars
CMD ["python", "-m", "agent.cli", "--mode", "aws"]
