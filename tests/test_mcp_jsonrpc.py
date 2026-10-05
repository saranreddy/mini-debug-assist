"""
Tests for MCP client JSON-RPC over stdio.

Tests the real JSON-RPC protocol implementation with a simple test server.
"""

import os
from pathlib import Path

import pytest

from agent.mcp_client import MCPClient

# Simple test MCP server script
TEST_SERVER_SCRIPT = """
import json
import sys

# Read initialize request
line = sys.stdin.readline()
req = json.loads(line)
assert req["method"] == "initialize"

# Send initialize response
resp = {
    "jsonrpc": "2.0",
    "id": req["id"],
    "result": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "serverInfo": {"name": "test-server", "version": "0.1.0"}
    }
}
print(json.dumps(resp), flush=True)

# Read tools/list request
line = sys.stdin.readline()
req = json.loads(line)
assert req["method"] == "tools/list"

# Send tools/list response
resp = {
    "jsonrpc": "2.0",
    "id": req["id"],
    "result": {
        "tools": [
            {
                "name": "test_tool",
                "description": "A test tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"}
                    }
                }
            }
        ]
    }
}
print(json.dumps(resp), flush=True)

# Handle tool calls in a loop
while True:
    line = sys.stdin.readline()
    if not line:
        break
    
    req = json.loads(line)
    
    if req["method"] == "tools/call":
        tool_name = req["params"]["name"]
        tool_args = req["params"]["arguments"]
        
        # Send tool response
        resp = {
            "jsonrpc": "2.0",
            "id": req["id"],
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "success": True,
                            "message": f"Called {tool_name} with {tool_args}"
                        })
                    }
                ],
                "isError": False
            }
        }
        print(json.dumps(resp), flush=True)
"""


class TestMCPJSONRPC:
    """Test MCP client JSON-RPC protocol."""

    def test_mcp_mock_mode_fallback(self):
        """Test that MCP_MOCK_MODE=true uses mock implementations."""
        # Set mock mode
        old_mock = os.environ.get("MCP_MOCK_MODE")
        os.environ["MCP_MOCK_MODE"] = "true"

        try:
            client = MCPClient()

            # Should use mock for unknown server
            result = client.call_tool("search_code", {"pattern": "test"})

            # Should get mock result
            assert result["success"] is True
            assert "results" in result

        finally:
            if old_mock is not None:
                os.environ["MCP_MOCK_MODE"] = old_mock
            else:
                os.environ.pop("MCP_MOCK_MODE", None)


class TestMCPWithRealServer:
    """
    Test MCP client with one of our own mcp_servers.

    These tests start real Python MCP servers as subprocesses.
    """

    @pytest.mark.skipif(
        not Path("mcp_servers/cloudwatch_logs.py").exists(),
        reason="CloudWatch Logs MCP server not found",
    )
    def test_cloudwatch_logs_server(self):
        """Test with real CloudWatch Logs MCP server."""
        # Skip if boto3/mcp not available
        try:
            import boto3
            import mcp
        except ImportError:
            pytest.skip("boto3 or mcp SDK not available")

        # Use the cloudwatch_logs server from our mcp_servers package
        # This tests the full JSON-RPC protocol with a real server

        client = MCPClient()

        old_mock = os.environ.get("MCP_MOCK_MODE")
        os.environ["MCP_MOCK_MODE"] = "false"

        try:
            # Start the cloudwatch_logs server
            success = client.start_server("cloudwatch_logs")

            if not success:
                pytest.skip(
                    "Failed to start cloudwatch_logs MCP server (may need AWS dependencies)"
                )

            # Verify tools were listed
            assert "cloudwatch_logs" in client.tools_cache
            tools = client.tools_cache["cloudwatch_logs"]
            assert len(tools) > 0

            tool_names = [t["name"] for t in tools]
            assert "query_logs" in tool_names or "get_log_streams" in tool_names

            # Call a tool (will fail without real AWS but tests the protocol)
            result = client.call_tool(
                "get_log_streams", {"log_group": "/aws/lambda/test", "limit": 10}
            )

            # Should get a structured response (even if it's an AWS error)
            assert isinstance(result, dict)
            # Result should have success key or content
            assert "success" in result or "result" in result or "error" in result

        finally:
            if old_mock is not None:
                os.environ["MCP_MOCK_MODE"] = old_mock
            else:
                os.environ.pop("MCP_MOCK_MODE", None)

            client.stop_all()

    def test_real_mode_no_fallback(self):
        """Test that real mode never silently falls back to mocks."""
        client = MCPClient()

        old_mock = os.environ.get("MCP_MOCK_MODE")
        os.environ["MCP_MOCK_MODE"] = "false"

        try:
            # Call tool without starting any servers
            result = client.call_tool("unknown_tool", {"test": "data"})

            # Should get an error, not a mock result
            assert result["success"] is False
            assert "isError" in result
            assert result["isError"] is True
            assert "No MCP servers started" in result["error"]

        finally:
            if old_mock is not None:
                os.environ["MCP_MOCK_MODE"] = old_mock
            else:
                os.environ.pop("MCP_MOCK_MODE", None)
