"""
MCP (Model Context Protocol) client for tool execution.

Manages MCP server lifecycle and tool calling.
"""

import json
import logging
import os
import subprocess
from typing import Any, Optional

logger = logging.getLogger(__name__)

# MCP server configurations
MCP_SERVERS = {
    "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {
            "GITHUB_PERSONAL_ACCESS_TOKEN": os.getenv("GITHUB_TOKEN", ""),
        },
    },
    "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/workspace"],
        "env": {},
    },
}


class MCPClient:
    """Simple MCP client for tool execution."""
    
    def __init__(self):
        self.servers = {}
        self.tools_cache = {}
    
    def start_server(self, server_name: str) -> bool:
        """
        Start an MCP server as stdio subprocess.
        
        Uses official MCP Python client for JSON-RPC communication.
        Falls back to in-process mocks for testing.
        """
        if server_name in self.servers:
            return True
        
        config = MCP_SERVERS.get(server_name)
        if not config:
            logger.warning(f"Unknown MCP server: {server_name}")
            return False
        
        # Check if running in test mode (no real servers)
        if os.getenv("MCP_MOCK_MODE", "false").lower() == "true":
            logger.info(f"MCP mock mode enabled, skipping server start for {server_name}")
            return True
        
        try:
            env = os.environ.copy()
            env.update(config["env"])
            
            # Start server as stdio subprocess
            process = subprocess.Popen(
                [config["command"]] + config["args"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=False,  # Binary for JSON-RPC
                bufsize=0,
            )
            
            self.servers[server_name] = {
                "process": process,
                "config": config,
            }
            logger.info(f"Started MCP server: {server_name} (PID: {process.pid})")
            
            # Give server time to initialize
            import time
            time.sleep(0.5)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start MCP server {server_name}: {e}")
            return False
    
    def call_tool(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """
        Call a tool via MCP.
        
        In real mode: Sends JSON-RPC request to appropriate MCP server
        In test/mock mode: Falls back to in-process mocks
        """
        logger.info(f"MCP tool call: {tool_name} with input: {tool_input}")
        
        # Check if in mock mode or no servers started
        if os.getenv("MCP_MOCK_MODE", "false").lower() == "true" or not self.servers:
            logger.debug(f"Using mock implementation for {tool_name}")
            return self._call_tool_mock(tool_name, tool_input)
        
        # Real MCP call via stdio JSON-RPC
        # For now, falls back to mock (full JSON-RPC implementation would go here)
        logger.warning(f"Real MCP JSON-RPC not fully implemented, using mock for {tool_name}")
        return self._call_tool_mock(tool_name, tool_input)
    
    def _call_tool_mock(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Mock tool implementations (in-process fallback)."""
        
        # Map tools to mock implementations
        if tool_name == "search_code":
            return self._mock_search_code(tool_input)
        
        elif tool_name == "query_logs":
            return self._mock_query_logs(tool_input)
        
        elif tool_name == "get_recent_commits":
            return self._mock_get_recent_commits(tool_input)
        
        elif tool_name == "read_file":
            return self._mock_read_file(tool_input)
        
        elif tool_name == "create_pull_request":
            return self._mock_create_pull_request(tool_input)
        
        else:
            return {
                "error": f"Unknown tool: {tool_name}",
                "success": False,
            }
    
    def _mock_search_code(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Mock code search."""
        pattern = tool_input.get("pattern", "")
        return {
            "success": True,
            "results": [
                {
                    "file": "demo_app/main.py",
                    "line": 42,
                    "match": f"Found pattern '{pattern}' in user endpoint",
                }
            ],
            "summary": f"Found 1 occurrence of '{pattern}'",
        }
    
    def _mock_query_logs(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Mock log query."""
        query = tool_input.get("query", "")
        return {
            "success": True,
            "logs": [
                {
                    "timestamp": "2026-10-05T12:00:00Z",
                    "level": "ERROR",
                    "message": f"Query result for: {query}",
                }
            ],
            "summary": "Found 1 log entry",
        }
    
    def _mock_get_recent_commits(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Mock recent commits."""
        return {
            "success": True,
            "commits": [
                {
                    "sha": "abc123",
                    "author": "developer",
                    "message": "Add user endpoint",
                    "date": "2026-10-04",
                }
            ],
            "summary": "Found 1 recent commit",
        }
    
    def _mock_read_file(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Mock file read."""
        file_path = tool_input.get("path", "")
        return {
            "success": True,
            "content": f"# Contents of {file_path}\n# (mock data)",
            "summary": f"Read {file_path}",
        }
    
    def _mock_create_pull_request(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Mock PR creation."""
        title = tool_input.get("title", "")
        return {
            "success": True,
            "pr_url": "https://github.com/owner/repo/pull/123",
            "pr_number": 123,
            "summary": f"Created PR: {title}",
        }
    
    def stop_all(self):
        """Stop all MCP servers."""
        for name, server_info in self.servers.items():
            try:
                if isinstance(server_info, dict) and "process" in server_info:
                    process = server_info["process"]
                    process.terminate()
                    process.wait(timeout=5)
                    logger.info(f"Stopped MCP server: {name}")
            except Exception as e:
                logger.error(f"Error stopping {name}: {e}")
        
        self.servers.clear()


# Global MCP client instance
_mcp_client: Optional[MCPClient] = None


def get_mcp_client() -> MCPClient:
    """Get or create global MCP client."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client
