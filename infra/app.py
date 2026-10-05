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
"""

import aws_cdk as cdk

from stacks.demo_app_stack import DemoAppStack
from stacks.agent_stack import AgentStack
from stacks.observability_stack import ObservabilityStack

app = cdk.App()

# Environment - use CDK_DEFAULT_ACCOUNT and CDK_DEFAULT_REGION from AWS CLI config
# This allows anyone to deploy to their own account without hardcoding
import os

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT") or app.node.try_get_context("account"),
    region=os.environ.get("CDK_DEFAULT_REGION") or app.node.try_get_context("region") or "us-east-1",
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
