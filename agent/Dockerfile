# Agent Dockerfile
# Builds a container that can run the debug agent in AWS mode

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml ./
COPY demo_app ./demo_app
COPY agent ./agent
COPY mcp_servers ./mcp_servers
COPY skills ./skills
COPY tests ./tests

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Set environment defaults
ENV PYTHONUNBUFFERED=1
ENV AWS_DEFAULT_REGION=us-east-1

# Run agent in AWS mode by default
# Container overrides will provide ISSUE_SOURCE and other vars
CMD ["python", "-m", "agent.cli", "--mode", "aws"]
