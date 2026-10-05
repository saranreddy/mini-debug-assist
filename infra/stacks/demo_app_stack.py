"""
Demo App Stack

Deploys the FastAPI demo application to ECS Fargate with:
- VPC and networking
- ECS cluster + Fargate service
- Application Load Balancer
- AppConfig for feature flags
- CloudWatch log group
- X-Ray tracing

Maps to Uber's production services monitored by Healthline.
"""

from aws_cdk import (
    Duration,
    Stack,
)
from aws_cdk import (
    aws_appconfig as appconfig,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_ecs as ecs,
)
from aws_cdk import (
    aws_ecs_patterns as ecs_patterns,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_logs as logs,
)
from constructs import Construct


class DemoAppStack(Stack):
    """Stack for the demo FastAPI application."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ===== VPC =====
        # Create a simple VPC with public subnets only (no NAT gateway = no extra cost)
        # Environment-agnostic: uses 2 AZs without requiring AWS credentials for synth
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            nat_gateways=0,  # No NAT gateway to keep costs low
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                )
            ],
        )

        # ===== ECS Cluster =====
        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name="mini-debug-assist-cluster",
            vpc=self.vpc,
            container_insights=False,  # Disable to avoid lookups
        )

        # ===== CloudWatch Log Group =====
        self.log_group = logs.LogGroup(
            self,
            "DemoAppLogs",
            log_group_name="/aws/ecs/mini-debug-assist-demo",
            retention=logs.RetentionDays.ONE_WEEK,
        )

        # ===== AppConfig for Feature Flags =====
        # Application
        self.appconfig_app = appconfig.CfnApplication(
            self,
            "AppConfigApp",
            name="MiniDebugAssist",
            description="Feature flags for Mini Debug Assist demo",
        )

        # Environment
        self.appconfig_env = appconfig.CfnEnvironment(
            self,
            "AppConfigEnv",
            application_id=self.appconfig_app.ref,
            name="production",
            description="Production environment",
        )

        # Configuration Profile
        self.appconfig_profile = appconfig.CfnConfigurationProfile(
            self,
            "AppConfigProfile",
            application_id=self.appconfig_app.ref,
            name="feature-flags",
            location_uri="hosted",
            type="AWS.AppConfig.FeatureFlags",
        )

        # Hosted Configuration (initial flags)
        initial_flags = {
            "DISCOUNT_V2": "off",  # Start with safe default
        }

        import json

        self.appconfig_config = appconfig.CfnHostedConfigurationVersion(
            self,
            "AppConfigInitialFlags",
            application_id=self.appconfig_app.ref,
            configuration_profile_id=self.appconfig_profile.ref,
            content=json.dumps(initial_flags),
            content_type="application/json",
        )

        # Deployment Strategy (immediate rollout for demo)
        self.appconfig_strategy = appconfig.CfnDeploymentStrategy(
            self,
            "AppConfigStrategy",
            name="ImmediateDeployment",
            deployment_duration_in_minutes=0,
            growth_factor=100,
            replicate_to="NONE",
            final_bake_time_in_minutes=0,
        )

        # ===== ECS Fargate Service =====
        # Task definition
        task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            cpu=256,
            memory_limit_mib=512,
        )

        # Grant AppConfig access
        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=[
                    "appconfig:StartConfigurationSession",
                    "appconfig:GetLatestConfiguration",
                ],
                resources=["*"],  # Scoped down in production
            )
        )

        # Grant X-Ray tracing
        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=[
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                ],
                resources=["*"],
            )
        )

        # Container
        container = task_definition.add_container(
            "DemoApp",
            # In production, this would be built and pushed to ECR
            image=ecs.ContainerImage.from_registry(
                "public.ecr.aws/docker/library/python:3.11-slim"
            ),
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="demo-app",
                log_group=self.log_group,
            ),
            environment={
                "USE_AWS_APPCONFIG": "true",
                "APPCONFIG_APPLICATION": self.appconfig_app.name,
                "APPCONFIG_ENVIRONMENT": "production",
                "APPCONFIG_CONFIGURATION": "feature-flags",
            },
            # Note: In production, you'd use a proper container with the app installed
            # For CDK synth purposes, we use a base image and would overlay the app
        )

        container.add_port_mappings(ecs.PortMapping(container_port=8000))

        # Fargate service with ALB
        # Deploy to public subnets with public IP assignment (no NAT gateway needed)
        self.fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "DemoAppService",
            cluster=self.cluster,
            task_definition=task_definition,
            desired_count=1,
            public_load_balancer=True,
            assign_public_ip=True,  # Required for public subnets
            task_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            # Health check
            health_check_grace_period=Duration.seconds(60),
        )

        # Configure health check
        self.fargate_service.target_group.configure_health_check(
            path="/health",
            interval=Duration.seconds(30),
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
        )

        # ===== CloudWatch Logs Metric Filter =====
        # Create metric filter to count errors from structured logs
        from aws_cdk import aws_logs as logs_

        logs_.MetricFilter(
            self,
            "ErrorMetricFilter",
            log_group=self.log_group,
            metric_namespace="MiniDebugAssist/Demo",
            metric_name="ErrorCount",
            filter_pattern=logs_.FilterPattern.literal('{ $.level = "ERROR" }'),
            metric_value="1",
            default_value=0,
            dimensions={
                "error_type": "$.exception_type",
                "endpoint": "$.path",
            },
        )

        # Store references for other stacks
        self.service_name = self.fargate_service.service.service_name
        self.load_balancer_dns = self.fargate_service.load_balancer.load_balancer_dns_name
        self.metric_namespace = "MiniDebugAssist/Demo"

        # Output the demo app URL
        from aws_cdk import CfnOutput

        CfnOutput(
            self,
            "DemoAppUrl",
            value=f"http://{self.load_balancer_dns}",
            description="Demo application URL",
            export_name=f"{Stack.of(self).stack_name}-DemoAppUrl",
        )
