"""
Observability Stack

Sets up monitoring and alerting:
- CloudWatch alarms for high error rate
- X-Ray service map
- Dashboard with key metrics
- SNS topic for notifications

Maps to Uber's monitoring with Arize tracing and alerting.
"""

from aws_cdk import (
    Duration,
    Stack,
)
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_cloudwatch_actions as cw_actions,
)
from aws_cdk import (
    aws_sns as sns,
)
from constructs import Construct

from .agent_stack import AgentStack
from .demo_app_stack import ERROR_ALARM_NAME, DemoAppStack


class ObservabilityStack(Stack):
    """Stack for observability and monitoring."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        demo_app_stack: DemoAppStack,
        agent_stack: AgentStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ===== SNS Topic for Alerts =====
        self.alert_topic = sns.Topic(
            self,
            "AlertTopic",
            topic_name="mini-debug-assist-alerts",
            display_name="Mini Debug Assist Alerts",
        )

        # In production, subscribe email/Slack here
        # self.alert_topic.add_subscription(
        #     subscriptions.EmailSubscription("team@example.com")
        # )

        # ===== CloudWatch Alarms =====

        # High error rate alarm on the metric published by the demo app's log metric
        # filter. Same namespace, same name, and no dimensions, so the alarm sees
        # exactly the datapoints the filter emits.
        error_metric = cloudwatch.Metric(
            namespace=demo_app_stack.metric_namespace,
            metric_name=demo_app_stack.error_metric_name,
            statistic="Sum",
            period=Duration.minutes(5),
        )

        self.error_alarm = cloudwatch.Alarm(
            self,
            "HighErrorRateAlarm",
            alarm_name=ERROR_ALARM_NAME,
            alarm_description="Triggers when error rate is high (from structured logs)",
            metric=error_metric,
            threshold=10,  # 10 or more errors in one 5-minute period
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        # Send alarm to SNS
        self.error_alarm.add_alarm_action(cw_actions.SnsAction(self.alert_topic))

        # ===== CloudWatch Dashboard =====
        self.dashboard = cloudwatch.Dashboard(
            self,
            "Dashboard",
            dashboard_name="MiniDebugAssist",
        )

        # Request count widget
        self.dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Request Count",
                left=[
                    cloudwatch.Metric(
                        namespace="AWS/ApplicationELB",
                        metric_name="RequestCount",
                        dimensions_map={
                            "LoadBalancer": demo_app_stack.fargate_service.load_balancer.load_balancer_full_name,
                        },
                        statistic="Sum",
                        period=Duration.minutes(1),
                    )
                ],
                width=12,
            )
        )

        # Error rate widget
        self.dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Error Rate (5XX)",
                left=[error_metric],
                width=12,
            )
        )

        # Latency widget
        self.dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Response Time",
                left=[
                    cloudwatch.Metric(
                        namespace="AWS/ApplicationELB",
                        metric_name="TargetResponseTime",
                        dimensions_map={
                            "LoadBalancer": demo_app_stack.fargate_service.load_balancer.load_balancer_full_name,
                        },
                        statistic="Average",
                        period=Duration.minutes(1),
                    )
                ],
                width=12,
            )
        )

        # Log insights widget
        self.dashboard.add_widgets(
            cloudwatch.LogQueryWidget(
                title="Recent Errors",
                log_group_names=[demo_app_stack.log_group.log_group_name],
                query_lines=[
                    "fields @timestamp, @message",
                    'filter level = "ERROR"',
                    "sort @timestamp desc",
                    "limit 20",
                ],
                width=24,
            )
        )
