#!/usr/bin/env python3
"""
AWS CDK App for Mini Debug Assist

Defines infrastructure:
- Demo app (ECS Fargate or Lambda)
- CloudWatch log group + alarm
- EventBridge rule to trigger agent
- AppConfig for feature flags
- CodeBuild for validation
- IAM roles with least privilege

Maps to Uber's deployment (Kubernetes + runtime jobs)

Note: Stacks are environment-agnostic when no AWS credentials are present,
allowing `cdk synth` to work without authentication.
"""

import os
import sys

from deploy_config import DeployConfigError, resolve_github_repo

# The fork the agent opens PRs in (from the environment or the repo-root .env).
# Checked first, before anything is synthesized, so a missing value fails with a
# clear message instead of deploying an agent with an empty repo.
try:
    github_repo = resolve_github_repo()
except DeployConfigError as e:
    print(f"\n❌ {e}\n", file=sys.stderr)
    sys.exit(1)

import aws_cdk as cdk  # noqa: E402
from stacks.agent_stack import AgentStack  # noqa: E402
from stacks.demo_app_stack import DemoAppStack  # noqa: E402
from stacks.observability_stack import ObservabilityStack  # noqa: E402

app = cdk.App()

# Environment configuration
# For synth without credentials, stacks are env-agnostic
# When deploying, CDK will use CDK_DEFAULT_ACCOUNT and CDK_DEFAULT_REGION

# Only set env if deploying (credentials available)
# This allows `cdk synth` to work without AWS credentials
env = None
if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
    env = cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT") or app.node.try_get_context("account"),
        region=os.environ.get("CDK_DEFAULT_REGION")
        or app.node.try_get_context("region")
        or "us-east-1",
    )

# Demo app stack (the service with bugs)
demo_app_stack = DemoAppStack(
    app,
    "MiniDebugAssist-DemoApp",
    env=env,
    description="Demo FastAPI application with planted bugs",
)

# Agent stack (debugging agent infrastructure)
agent_stack = AgentStack(
    app,
    "MiniDebugAssist-Agent",
    env=env,
    demo_app_stack=demo_app_stack,
    github_repo=github_repo,
    description="Debug agent orchestration and execution",
)

# Observability stack (logs, metrics, alarms)
observability_stack = ObservabilityStack(
    app,
    "MiniDebugAssist-Observability",
    env=env,
    demo_app_stack=demo_app_stack,
    agent_stack=agent_stack,
    description="CloudWatch logs, X-Ray tracing, and alarms",
)

# Add dependencies
agent_stack.add_dependency(demo_app_stack)
observability_stack.add_dependency(demo_app_stack)
observability_stack.add_dependency(agent_stack)

# Tags
for stack in [demo_app_stack, agent_stack, observability_stack]:
    cdk.Tags.of(stack).add("Project", "MiniDebugAssist")
    cdk.Tags.of(stack).add("ManagedBy", "CDK")

app.synth()
