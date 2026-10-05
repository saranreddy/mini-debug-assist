"""
Agent Stack

Deploys the debugging agent infrastructure:
- EventBridge rule to trigger agent
- ECS Fargate task definition for agent
- CodeBuild project for validation
- IAM roles with least privilege (Bedrock, CloudWatch, etc.)
- Secrets Manager for GitHub token

Maps to Uber's runtime jobs on Kubernetes with Buildkite CI.
"""

from aws_cdk import (
    Duration,
    Stack,
    aws_events as events,
    aws_events_targets as targets,
    aws_ecs as ecs,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
    aws_codebuild as codebuild,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

from .demo_app_stack import DemoAppStack


class AgentStack(Stack):
    """Stack for the debugging agent infrastructure."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        demo_app_stack: DemoAppStack,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ===== GitHub Token Secret =====
        # Store GitHub token in Secrets Manager
        self.github_token_secret = secretsmanager.Secret(
            self,
            "GitHubToken",
            secret_name="mini-debug-assist/github-token",
            description="GitHub token for creating PRs",
        )

        # ===== Agent IAM Role =====
        self.agent_role = iam.Role(
            self,
            "AgentRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="IAM role for debug agent with least-privilege access",
        )

        # Bedrock InvokeModel permission
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                sid="BedrockInvokeModel",
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    # Claude Sonnet
                    f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-3-sonnet-*",
                    # Claude Opus
                    f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-3-opus-*",
                ],
            )
        )

        # CloudWatch Logs read permission
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                sid="CloudWatchLogsRead",
                actions=[
                    "logs:StartQuery",
                    "logs:GetQueryResults",
                    "logs:DescribeLogStreams",
                    "logs:GetLogEvents",
                ],
                resources=[
                    demo_app_stack.log_group.log_group_arn,
                ],
            )
        )

        # X-Ray read permission
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                sid="XRayRead",
                actions=[
                    "xray:BatchGetTraces",
                    "xray:GetTraceSummaries",
                    "xray:GetServiceGraph",
                ],
                resources=["*"],
            )
        )

        # AppConfig read permission
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                sid="AppConfigRead",
                actions=[
                    "appconfig:StartConfigurationSession",
                    "appconfig:GetLatestConfiguration",
                ],
                resources=["*"],
            )
        )

        # AppConfig write permission (for flag rollback)
        # Note: In production, this would require approval workflow
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                sid="AppConfigWrite",
                actions=[
                    "appconfig:CreateHostedConfigurationVersion",
                    "appconfig:StartDeployment",
                ],
                resources=["*"],
                conditions={
                    "StringEquals": {
                        "aws:RequestedRegion": self.region,
                    }
                },
            )
        )

        # Secrets Manager read (for GitHub token)
        self.github_token_secret.grant_read(self.agent_role)

        # ===== Agent Task Definition =====
        self.agent_task_def = ecs.FargateTaskDefinition(
            self,
            "AgentTaskDef",
            cpu=512,
            memory_limit_mib=1024,
            task_role=self.agent_role,
        )

        # Agent container
        # Note: In production, this would be a prebuilt image with the agent code
        self.agent_container = self.agent_task_def.add_container(
            "AgentContainer",
            image=ecs.ContainerImage.from_registry("public.ecr.aws/docker/library/python:3.11-slim"),
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="debug-agent",
                log_group=logs.LogGroup(
                    self,
                    "AgentLogs",
                    log_group_name="/aws/ecs/mini-debug-assist-agent",
                    retention=logs.RetentionDays.ONE_WEEK,
                ),
            ),
            environment={
                "AWS_REGION": self.region,
                "AGENT_TYPE": "python-web",
            },
            secrets={
                "GITHUB_TOKEN": ecs.Secret.from_secrets_manager(
                    self.github_token_secret
                ),
            },
        )

        # ===== EventBridge Rule =====
        # Trigger agent on CloudWatch alarm or manual invoke
        self.trigger_rule = events.Rule(
            self,
            "AgentTriggerRule",
            description="Trigger debug agent on high error rate",
            # Example: trigger every 5 minutes for demo
            # In production, this would be event-driven from CloudWatch alarms
            schedule=events.Schedule.rate(Duration.minutes(5)),
            enabled=False,  # Disabled by default, enable manually
        )

        # Target: Run agent ECS task
        self.trigger_rule.add_target(
            targets.EcsTask(
                cluster=demo_app_stack.cluster,
                task_definition=self.agent_task_def,
                subnet_selection=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                ),
            )
        )

        # ===== CodeBuild Project for Validation =====
        # Maps to Uber's Bazel test execution
        self.validation_project = codebuild.Project(
            self,
            "ValidationProject",
            project_name="mini-debug-assist-validation",
            description="Run tests to validate agent fixes",
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                compute_type=codebuild.ComputeType.SMALL,
            ),
            build_spec=codebuild.BuildSpec.from_object({
                "version": "0.2",
                "phases": {
                    "install": {
                        "runtime-versions": {
                            "python": "3.11",
                        },
                        "commands": [
                            "pip install -e .[dev]",
                        ],
                    },
                    "build": {
                        "commands": [
                            "pytest -v --tb=short",
                        ],
                    },
                },
                "reports": {
                    "test-results": {
                        "files": ["test-results.xml"],
                        "file-format": "JUNITXML",
                    },
                },
            }),
        )
