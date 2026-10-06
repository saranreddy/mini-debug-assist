"""
Tests for AWS collectors (real mode).

Uses mocked boto3 responses to test CloudWatch Logs, X-Ray, and deduplication.
"""

import json
from unittest.mock import Mock, patch

from agent.config import AgentConfig
from agent.dedup import get_error_signature, should_investigate


class TestCloudWatchLogsCollector:
    """Test CloudWatch Logs Insights queries."""

    @patch("agent.nodes.context_collector.boto3")
    def test_fetch_cloudwatch_logs_success(self, mock_boto3):
        """Test successful log fetching from CloudWatch Logs Insights."""
        # Mock boto3 client
        mock_client = Mock()
        mock_boto3.client.return_value = mock_client

        # Mock start_query response
        mock_client.start_query.return_value = {"queryId": "test-query-123"}

        # Mock get_query_results response (complete after first check)
        mock_client.get_query_results.return_value = {
            "status": "Complete",
            "results": [
                [
                    {"field": "@timestamp", "value": "2026-10-05T12:00:00Z"},
                    {"field": "@message", "value": "KeyError: 'email'"},
                    {"field": "level", "value": "ERROR"},
                    {"field": "exception_type", "value": "KeyError"},
                ],
                [
                    {"field": "@timestamp", "value": "2026-10-05T12:00:01Z"},
                    {"field": "@message", "value": "Another error"},
                    {"field": "level", "value": "ERROR"},
                    {"field": "exception_type", "value": "KeyError"},
                ],
            ],
        }

        # Import and call
        from agent.nodes.context_collector import _fetch_cloudwatch_logs

        issue_data = {
            "timestamp": "2026-10-05T12:00:00Z",
            "error_type": "KeyError",
        }
        config = AgentConfig(mode="aws")

        logs = _fetch_cloudwatch_logs(issue_data, config)

        # Assertions
        assert len(logs) == 2
        assert logs[0]["timestamp"] == "2026-10-05T12:00:00Z"
        assert logs[0]["message"] == "KeyError: 'email'"
        assert logs[0]["level"] == "ERROR"

        # Verify query was called
        mock_client.start_query.assert_called_once()
        call_kwargs = mock_client.start_query.call_args[1]
        assert "filter exception_type" in call_kwargs["queryString"]

    @patch("agent.nodes.context_collector.boto3")
    def test_fetch_cloudwatch_logs_timeout(self, mock_boto3):
        """Test handling of query timeout."""
        mock_client = Mock()
        mock_boto3.client.return_value = mock_client

        mock_client.start_query.return_value = {"queryId": "test-query-123"}

        # Mock query that never completes
        mock_client.get_query_results.return_value = {"status": "Running", "results": []}

        from agent.nodes.context_collector import _fetch_cloudwatch_logs

        issue_data = {"timestamp": "2026-10-05T12:00:00Z"}
        config = AgentConfig(mode="aws")

        with patch("time.sleep"):  # Speed up test
            logs = _fetch_cloudwatch_logs(issue_data, config)

        # Should return empty on timeout
        assert logs == []


class TestXRayCollector:
    """Test X-Ray trace collection."""

    @patch("agent.nodes.context_collector.boto3")
    def test_fetch_xray_traces_success(self, mock_boto3):
        """Test successful trace fetching from X-Ray."""
        mock_client = Mock()
        mock_boto3.client.return_value = mock_client

        # Mock get_trace_summaries
        mock_client.get_trace_summaries.return_value = {
            "TraceSummaries": [
                {
                    "Id": "trace-1",
                    "Duration": 1.5,
                    "HasError": True,
                },
                {
                    "Id": "trace-2",
                    "Duration": 0.5,
                    "HasFault": True,
                },
            ]
        }

        # Mock batch_get_traces
        mock_client.batch_get_traces.return_value = {
            "Traces": [
                {
                    "Id": "trace-1",
                    "Duration": 1.5,
                    "Segments": [{"Document": json.dumps({"Http": {"Response": {"Status": 500}}})}],
                },
                {
                    "Id": "trace-2",
                    "Duration": 0.5,
                    "Segments": [],
                },
            ]
        }

        from agent.nodes.context_collector import _fetch_xray_traces

        issue_data = {
            "timestamp": "2026-10-05T12:00:00Z",
            "endpoint": "/user/3",
        }
        config = AgentConfig(mode="aws")

        traces = _fetch_xray_traces(issue_data, config)

        # Assertions
        assert len(traces) == 2
        assert traces[0]["id"] == "trace-1"
        assert traces[0]["duration"] == 1.5
        assert traces[0]["has_error"] is True

    @patch("agent.nodes.context_collector.boto3")
    def test_fetch_xray_traces_no_errors(self, mock_boto3):
        """Test when no error traces are found."""
        mock_client = Mock()
        mock_boto3.client.return_value = mock_client

        mock_client.get_trace_summaries.return_value = {
            "TraceSummaries": [
                {
                    "Id": "trace-1",
                    "Duration": 0.1,
                    "HasError": False,
                    "HasFault": False,
                }
            ]
        }

        from agent.nodes.context_collector import _fetch_xray_traces

        issue_data = {"timestamp": "2026-10-05T12:00:00Z"}
        config = AgentConfig(mode="aws")

        traces = _fetch_xray_traces(issue_data, config)

        # Should return empty - no error traces
        assert traces == []


class TestDeduplication:
    """Test deduplication logic."""

    def test_get_error_signature(self):
        """Test error signature generation."""
        sig1 = get_error_signature("HighErrorRate", "KeyError", "/user/3")
        sig2 = get_error_signature("HighErrorRate", "KeyError", "/user/3")
        sig3 = get_error_signature("HighErrorRate", "KeyError", "/user/4")

        # Same inputs = same signature
        assert sig1 == sig2

        # Different endpoint = different signature
        assert sig1 != sig3

        # Signature is reasonable length
        assert len(sig1) == 32

    @patch("agent.dedup.boto3")
    def test_should_investigate_new_error(self, mock_boto3):
        """Test dedup check for new error (should investigate)."""
        # Mock DynamoDB
        mock_dynamodb = Mock()
        mock_table = Mock()
        mock_boto3.resource.return_value = mock_dynamodb
        mock_dynamodb.Table.return_value = mock_table

        # Mock successful put (condition passed - item didn't exist)
        mock_table.put_item.return_value = {}

        with patch.dict("os.environ", {"DEDUP_TABLE_NAME": "test-table"}):
            result = should_investigate("test-signature-123")

        assert result is True
        mock_table.put_item.assert_called_once()

    @patch("agent.dedup.boto3")
    def test_should_investigate_duplicate_error(self, mock_boto3):
        """Test dedup check for duplicate error (should skip)."""
        # Mock DynamoDB
        mock_dynamodb = Mock()
        mock_table = Mock()
        mock_boto3.resource.return_value = mock_dynamodb
        mock_dynamodb.Table.return_value = mock_table

        # Mock conditional check failure (item exists and not expired)
        from botocore.exceptions import ClientError

        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )

        with patch.dict("os.environ", {"DEDUP_TABLE_NAME": "test-table"}):
            result = should_investigate("test-signature-123")

        assert result is False

    def test_should_investigate_no_table(self):
        """Test dedup check when table not configured (should investigate)."""
        with patch.dict("os.environ", {}, clear=True):
            result = should_investigate("test-signature-123")

        # Fail open - investigate when dedup not configured
        assert result is True


class TestLogPruning:
    """Test log pruning and deduplication."""

    def test_prune_logs_deduplication(self):
        """Test that repeated log messages are deduplicated."""
        from agent.nodes.context_collector import _prune_logs

        logs = [
            {
                "timestamp": "2026-10-05T12:00:00Z",
                "message": "Same error occurred",
                "level": "ERROR",
            },
            {
                "timestamp": "2026-10-05T12:00:01Z",
                "message": "Same error occurred",
                "level": "ERROR",
            },
            {
                "timestamp": "2026-10-05T12:00:02Z",
                "message": "Same error occurred",
                "level": "ERROR",
            },
            {
                "timestamp": "2026-10-05T12:00:03Z",
                "message": "Different error",
                "level": "ERROR",
            },
        ]

        pruned = _prune_logs(logs, max_entries=100)

        # Should have 2 entries (2 unique messages)
        assert len(pruned) == 2

        # First message should have count annotation
        same_error_entry = [e for e in pruned if "Same error" in e["message"]][0]
        assert "repeated 3 times" in same_error_entry["message"]
        assert same_error_entry["dedup_count"] == 3

    def test_prune_logs_truncation(self):
        """Test that long messages are truncated."""
        from agent.nodes.context_collector import _prune_logs

        long_message = "A" * 1000
        logs = [
            {
                "timestamp": "2026-10-05T12:00:00Z",
                "message": long_message,
                "level": "ERROR",
            }
        ]

        pruned = _prune_logs(logs)

        # Message should be truncated
        assert len(pruned[0]["message"]) < len(long_message)
        assert "truncated" in pruned[0]["message"]

    def test_prune_logs_level_priority(self):
        """Test that ERROR logs are prioritized over INFO."""
        from agent.nodes.context_collector import _prune_logs

        logs = [
            {"timestamp": "2026-10-05T12:00:00Z", "message": "Info 1", "level": "INFO"},
            {"timestamp": "2026-10-05T12:00:01Z", "message": "Error 1", "level": "ERROR"},
            {"timestamp": "2026-10-05T12:00:02Z", "message": "Info 2", "level": "INFO"},
            {"timestamp": "2026-10-05T12:00:03Z", "message": "Warn 1", "level": "WARN"},
        ]

        pruned = _prune_logs(logs, max_entries=2)

        # Should keep ERROR and WARN (highest priority)
        assert len(pruned) == 2
        levels = [log["level"] for log in pruned]
        assert "ERROR" in levels
        assert "WARN" in levels


class TestAlarmEventParsing:
    """Test parsing of CloudWatch alarm events."""

    def test_parse_alarm_event(self):
        """Test parsing a CloudWatch alarm state change event."""
        from agent.cli import _parse_alarm_event

        alarm_event = {
            "source": "aws.cloudwatch",
            "detail-type": "CloudWatch Alarm State Change",
            "time": "2026-10-05T12:00:00Z",
            "detail": {
                "alarmName": "mini-debug-assist-error-alarm",
                "state": {
                    "value": "ALARM",
                    "reason": "Threshold Crossed: 10 datapoints were greater than 5",
                },
                "configuration": {
                    "metrics": [
                        {
                            "metricStat": {
                                "metric": {
                                    "name": "ErrorCount",
                                    "namespace": "MiniDebugAssist/Demo",
                                    "dimensions": {"error_type": "KeyError", "endpoint": "/user/3"},
                                }
                            }
                        }
                    ]
                },
            },
        }

        issue_data = _parse_alarm_event(alarm_event)

        assert issue_data["alarm_name"] == "mini-debug-assist-error-alarm"
        assert issue_data["alarm_state"] == "ALARM"
        assert issue_data["error_type"] == "KeyError"
        assert issue_data["endpoint"] == "/user/3"
        assert issue_data["source"] == "cloudwatch_alarm"
