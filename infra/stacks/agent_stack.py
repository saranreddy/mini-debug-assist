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

import os

from aws_cdk import (
    IgnoreMode,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_codebuild as codebuild,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_ecr_assets as ecr_assets,
)
from aws_cdk import (
    aws_ecs as ecs,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

from .demo_app_stack import ERROR_ALARM_NAME, DemoAppStack

# Explicit rule name so scripts/smoke.py can look the rule up directly
AGENT_TRIGGER_RULE_NAME = "mini-debug-assist-agent-trigger"

# Name of the agent container (task definition container id, used in overrides)
AGENT_CONTAINER_NAME = "AgentContainer"

# Text fields of the CloudWatch "Alarm State Change" event handed to the agent task
# as environment variables (agent/cli.py rebuilds the event from them). Each path is
# a string in every alarm state-change event; ECS only accepts string env values, so
# never pass a JSON object or array here.
ALARM_EVENT_ENV_FIELDS = {
    "ALARM_NAME": "$.detail.alarmName",
    "ALARM_STATE": "$.detail.state.value",
    "ALARM_REASON": "$.detail.state.reason",
    "ALARM_TIME": "$.time",
    "ALARM_REGION": "$.region",
    "ALARM_ACCOUNT": "$.account",
    "ALARM_EVENT_ID": "$.id",
}

# The agent image is built from the repo root (agent/Dockerfile copies pyproject.toml,
# README.md, agent/, demo_app/, mcp_servers/, skills/, tests/ from there).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AGENT_DOCKERFILE = "agent/Dockerfile"

# Kept out of the agent image build context: big, irrelevant, or secret files.
# (Docker-style patterns, relative to the repo root.)
AGENT_IMAGE_EXCLUDES = [
    ".git",
    ".github",
    ".env",
    ".venv",
    "venv",
    "**/node_modules",
    "**/cdk.out",
    "infra",
    "docs",
    "output",
    "**/*.mp4",
    "**/__pycache__",
    "**/*.pyc",
    "**/.pytest_cache",
    "**/.mypy_cache",
    "**/.ruff_cache",
    ".coverage",
    "htmlcov",
    "**/*.egg-info",
    "build",
    "dist",
]


class AgentStack(Stack):
    """Stack for the debugging agent infrastructure."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        demo_app_stack: DemoAppStack,
        github_repo: str,
        github_token_secret_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ===== GitHub Token Secret =====
        # Import the secret created by `make setup-secrets` by its COMPLETE ARN
        # (with the 6-character suffix). ECS resolves secret fields by full ARN;
        # from_secret_name_v2 only yields a partial ARN. infra/deploy_config.py
        # looks the ARN up (`make deploy`) or uses a placeholder for offline synth.
        self.github_token_secret = secretsmanager.Secret.from_secret_complete_arn(
            self,
            "GitHubToken",
            github_token_secret_arn,
        )

        # ===== DynamoDB Deduplication Table =====
        self.dedup_table = dynamodb.Table(
            self,
            "DedupTable",
            table_name="mini-debug-assist-dedup",
            partition_key=dynamodb.Attribute(
                name="error_signature", type=dynamodb.AttributeType.STRING
            ),
            time_to_live_attribute="ttl",
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For development
        )

        # ===== Agent IAM Role =====
        self.agent_role = iam.Role(
            self,
            "AgentRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="IAM role for debug agent with least-privilege access",
        )

        # Bedrock permissions (matches model IDs in agent/config.py)
        # Model IDs from:
        # - https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5-5.html
        # - https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5.html
        # - Claude Sonnet 5.5 (latest): anthropic.claude-sonnet-5-5
        # - Claude Opus 5.5 (latest): anthropic.claude-opus-5-5
        # - Cross-region profiles: us./eu./au./global. prefixes
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                sid="BedrockInvokeModel",
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    # Inference profiles (us./global. prefixes, see agent/config.py).
                    # System-defined profile ARNs include the account ID.
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/global.anthropic.claude-*",
                    # A cross-region profile routes to the foundation model in any of
                    # its destination regions, so allow the model in every region.
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                    # Global profiles authorize against a region-less model ARN.
                    "arn:aws:bedrock:::foundation-model/anthropic.claude-*",
                ],
            )
        )

        # Bedrock Converse API (new API for tool use)
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                sid="BedrockConverse",
                actions=[
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                resources=[
                    f"arn:aws:bedrock:{self.region}::inference-profile/us.anthropic.claude-3-*",
                    f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-3-*",
                ],
            )
        )

        # CloudWatch Logs read permission
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                sid="CloudWatchLogsRead",
                actions=[
                    "logs:StartQuery",
                    "logs:DescribeLogStreams",
                    "logs:GetLogEvents",
                ],
                resources=[
                    demo_app_stack.log_group.log_group_arn,
                ],
            )
        )

        # GetQueryResults takes only a query ID, not a log group, so it can't be
        # scoped to the demo log group: it needs "*". (StopQuery isn't used.)
        self.agent_role.add_to_policy(
            iam.PolicyStatement(
                sid="CloudWatchLogsQueryResults",
                actions=["logs:GetQueryResults"],
                resources=["*"],
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

        # DynamoDB access (for deduplication)
        self.dedup_table.grant_read_write_data(self.agent_role)

        # ===== Build Agent Container Image =====
        # Build context = repo root, Dockerfile = agent/Dockerfile, so every COPY in the
        # Dockerfile resolves. `cdk synth` only stages the (filtered) context into
        # cdk.out; Docker runs at `cdk deploy`. LINUX_AMD64 matches Fargate's default
        # x86_64 runtime, even when building on an Apple Silicon laptop.
        self.agent_image = ecs.ContainerImage.from_asset(
            REPO_ROOT,
            file=AGENT_DOCKERFILE,
            exclude=AGENT_IMAGE_EXCLUDES,
            ignore_mode=IgnoreMode.DOCKER,
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        # ===== Agent Task Definition =====
        self.agent_task_def = ecs.FargateTaskDefinition(
            self,
            "AgentTaskDef",
            family="mini-debug-assist-agent",
            cpu=512,
            memory_limit_mib=1024,
            task_role=self.agent_role,
        )

        # Agent container with built image
        self.agent_container = self.agent_task_def.add_container(
            AGENT_CONTAINER_NAME,
            image=self.agent_image,
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
                "DEMO_APP_LOG_GROUP": demo_app_stack.log_group.log_group_name,
                "DEDUP_TABLE_NAME": self.dedup_table.table_name,
                # Validated in infra/deploy_config.py (never empty or the placeholder)
                "GITHUB_REPO": github_repo,
            },
            secrets={
                # The secret is JSON {"token": ..., "repo": ...} (scripts/setup_github_token.sh);
                # inject only the token, not the whole JSON document.
                "GITHUB_TOKEN": ecs.Secret.from_secrets_manager(
                    self.github_token_secret, field="token"
                ),
            },
        )

        # ===== EventBridge Rule (Event-Driven) =====
        # Trigger agent on CloudWatch Alarm State Change
        self.alarm_trigger_rule = events.Rule(
            self,
            "AlarmTriggerRule",
            rule_name=AGENT_TRIGGER_RULE_NAME,
            description="Trigger debug agent when CloudWatch alarm enters ALARM state",
            event_pattern=events.EventPattern(
                source=["aws.cloudwatch"],
                detail_type=["CloudWatch Alarm State Change"],
                # Only the demo app's error alarm going into ALARM, so no other
                # alarm in the account can launch the agent.
                detail={
                    "alarmName": [ERROR_ALARM_NAME],
                    "state": {"value": ["ALARM"]},
                },
            ),
            enabled=True,
        )

        # Target: Run agent ECS task with alarm event data
        self.alarm_trigger_rule.add_target(
            targets.EcsTask(
                cluster=demo_app_stack.cluster,
                task_definition=self.agent_task_def,
                # Public subnets and no NAT gateway: the task needs a public IP to pull
                # its image from ECR and reach Bedrock, Secrets Manager, and GitHub.
                subnet_selection=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
                assign_public_ip=True,
                # String fields only: EventBridge inserts a JSON object unquoted, and
                # ECS RunTask rejects non-string environment values.
                container_overrides=[
                    targets.ContainerOverride(
                        container_name=AGENT_CONTAINER_NAME,
                        environment=[
                            targets.TaskEnvironmentVariable(name="ISSUE_SOURCE", value="alarm"),
                            *[
                                targets.TaskEnvironmentVariable(
                                    name=name, value=events.EventField.from_path(path)
                                )
                                for name, path in ALARM_EVENT_ENV_FIELDS.items()
                            ],
                        ],
                    )
                ],
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
            build_spec=codebuild.BuildSpec.from_object(
                {
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
                }
            ),
        )
