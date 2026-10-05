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

from .demo_app_stack import DemoAppStack


class AgentStack(Stack):
    """Stack for the debugging agent infrastructure."""

    def __init__(
        self, scope: Construct, construct_id: str, demo_app_stack: DemoAppStack, **kwargs
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
                    # Cross-region inference profiles for Claude 5 generation
                    f"arn:aws:bedrock:{self.region}::inference-profile/us.anthropic.claude-sonnet-5-5",
                    f"arn:aws:bedrock:{self.region}::inference-profile/us.anthropic.claude-opus-5-5",
                    f"arn:aws:bedrock:{self.region}::inference-profile/global.anthropic.claude-sonnet-5-5",
                    f"arn:aws:bedrock:{self.region}::inference-profile/global.anthropic.claude-opus-5-5",
                    # Direct model access
                    f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-sonnet-5-5",
                    f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-opus-5-5",
                    # Wildcard for flexibility (supports Claude 4.x, 5.x)
                    f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-*",
                    f"arn:aws:bedrock:{self.region}::inference-profile/us.anthropic.claude-*",
                    f"arn:aws:bedrock:{self.region}::inference-profile/global.anthropic.claude-*",
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

        # DynamoDB access (for deduplication)
        self.dedup_table.grant_read_write_data(self.agent_role)

        # ===== Build Agent Container Image =====
        # Build Docker image from agent/ directory
        import os

        agent_dockerfile_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "agent"
        )

        self.agent_image = ecs.ContainerImage.from_asset(
            agent_dockerfile_path,
            file="Dockerfile",
        )

        # ===== Agent Task Definition =====
        self.agent_task_def = ecs.FargateTaskDefinition(
            self,
            "AgentTaskDef",
            cpu=512,
            memory_limit_mib=1024,
            task_role=self.agent_role,
        )

        # Agent container with built image
        self.agent_container = self.agent_task_def.add_container(
            "AgentContainer",
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
                "GITHUB_REPO": os.getenv("GITHUB_REPO", ""),
            },
            secrets={
                "GITHUB_TOKEN": ecs.Secret.from_secrets_manager(self.github_token_secret),
            },
        )

        # ===== EventBridge Rule (Event-Driven) =====
        # Trigger agent on CloudWatch Alarm State Change
        self.alarm_trigger_rule = events.Rule(
            self,
            "AlarmTriggerRule",
            description="Trigger debug agent when CloudWatch alarm enters ALARM state",
            event_pattern=events.EventPattern(
                source=["aws.cloudwatch"],
                detail_type=["CloudWatch Alarm State Change"],
                detail={
                    "state": {"value": ["ALARM"]},
                    # Optional: filter for specific alarms
                    # "alarmName": [{"prefix": "MiniDebugAssist-"}]
                },
            ),
            enabled=True,
        )

        # Target: Run agent ECS task with alarm event data
        self.alarm_trigger_rule.add_target(
            targets.EcsTask(
                cluster=demo_app_stack.cluster,
                task_definition=self.agent_task_def,
                subnet_selection=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
                container_overrides=[
                    targets.ContainerOverride(
                        container_name="AgentContainer",
                        environment=[
                            targets.TaskEnvironmentVariable(
                                name="ISSUE_SOURCE",
                                value="alarm",
                            ),
                            targets.TaskEnvironmentVariable(
                                name="ALARM_EVENT_JSON",
                                value=events.EventField.from_path("$"),
                            ),
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
