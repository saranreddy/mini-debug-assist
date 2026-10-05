"""
Tests for MCP client JSON-RPC over stdio.

Tests the real JSON-RPC protocol implementation with a simple test server.
"""

import json
import os
import pytest
import subprocess
import sys
import time
from pathlib import Path

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
    
    @pytest.fixture
    def test_server_path(self, tmp_path):
        """Create a test MCP server script."""
        server_path = tmp_path / "test_server.py"
        server_path.write_text(TEST_SERVER_SCRIPT)
        return str(server_path)
    
    def test_mcp_client_initialize(self, test_server_path):
        """Test MCP client initialize handshake."""
        # Temporarily add test server to MCP_SERVERS
        from agent import mcp_client
        
        original_servers = mcp_client.MCP_SERVERS.copy()
        
        try:
            mcp_client.MCP_SERVERS["test"] = {
                "command": sys.executable,
                "args": [test_server_path],
                "env": {},
            }
            
            # Create client and start server
            client = MCPClient()
            
            # Disable mock mode
            old_mock = os.environ.get("MCP_MOCK_MODE")
            os.environ["MCP_MOCK_MODE"] = "false"
            
            try:
                success = client.start_server("test")
                
                assert success is True
                assert "test" in client.servers
                assert client.servers["test"]["initialized"] is True
                
                # Should have cached tools
                assert "test" in client.tools_cache
                assert len(client.tools_cache["test"]) == 1
                assert client.tools_cache["test"][0]["name"] == "test_tool"
                
            finally:
                # Restore mock mode
                if old_mock is not None:
                    os.environ["MCP_MOCK_MODE"] = old_mock
                else:
                    os.environ.pop("MCP_MOCK_MODE", None)
                
                # Clean up
                client.stop_all()
        
        finally:
            mcp_client.MCP_SERVERS = original_servers
    
    def test_mcp_client_call_tool(self, test_server_path):
        """Test MCP client tool call."""
        from agent import mcp_client
        
        original_servers = mcp_client.MCP_SERVERS.copy()
        
        try:
            mcp_client.MCP_SERVERS["test"] = {
                "command": sys.executable,
                "args": [test_server_path],
                "env": {},
            }
            
            client = MCPClient()
            
            old_mock = os.environ.get("MCP_MOCK_MODE")
            os.environ["MCP_MOCK_MODE"] = "false"
            
            try:
                # Start server
                client.start_server("test")
                
                # Call tool
                result = client.call_tool("test_tool", {"message": "hello"})
                
                # Should get successful result
                assert result["success"] is True
                assert "test_tool" in result["message"]
                assert "hello" in result["message"]
                
            finally:
                if old_mock is not None:
                    os.environ["MCP_MOCK_MODE"] = old_mock
                else:
                    os.environ.pop("MCP_MOCK_MODE", None)
                
                client.stop_all()
        
        finally:
            mcp_client.MCP_SERVERS = original_servers
    
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
    
    These tests are skipped if the server is not available.
    """
    
    @pytest.mark.skipif(
        not Path("mcp_servers/github_server.py").exists(),
        reason="GitHub MCP server not found"
    )
    def test_github_mcp_server(self):
        """Test with real GitHub MCP server (if available)."""
        from agent import mcp_client
        
        # Check if server file exists
        server_path = Path("mcp_servers/github_server.py")
        if not server_path.exists():
            pytest.skip("GitHub MCP server not found")
        
        original_servers = mcp_client.MCP_SERVERS.copy()
        
        try:
            # Add our GitHub server
            mcp_client.MCP_SERVERS["github_test"] = {
                "command": sys.executable,
                "args": [str(server_path)],
                "env": {"GITHUB_TOKEN": os.getenv("GITHUB_TOKEN", "test_token")},
            }
            
            client = MCPClient()
            
            old_mock = os.environ.get("MCP_MOCK_MODE")
            os.environ["MCP_MOCK_MODE"] = "false"
            
            try:
                success = client.start_server("github_test")
                
                # If initialization succeeds, test a tool call
                if success:
                    # This will likely fail without real creds, but tests the protocol
                    result = client.call_tool("search_code", {"query": "test"})
                    
                    # Should at least get a structured response
                    assert isinstance(result, dict)
                else:
                    pytest.skip("Could not start GitHub MCP server")
                
            finally:
                if old_mock is not None:
                    os.environ["MCP_MOCK_MODE"] = old_mock
                else:
                    os.environ.pop("MCP_MOCK_MODE", None)
                
                client.stop_all()
        
        finally:
            mcp_client.MCP_SERVERS = original_servers
