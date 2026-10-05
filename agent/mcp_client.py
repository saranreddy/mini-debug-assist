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
        """Start an MCP server as stdio subprocess."""
        if server_name in self.servers:
            return True
        
        config = MCP_SERVERS.get(server_name)
        if not config:
            logger.warning(f"Unknown MCP server: {server_name}")
            return False
        
        try:
            env = os.environ.copy()
            env.update(config["env"])
            
            process = subprocess.Popen(
                [config["command"]] + config["args"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,
            )
            
            self.servers[server_name] = process
            logger.info(f"Started MCP server: {server_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start MCP server {server_name}: {e}")
            return False
    
    def call_tool(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """
        Call a tool via MCP.
        
        For now, implements mock tools inline. In production, this would:
        1. Determine which MCP server has the tool
        2. Send JSON-RPC request to that server
        3. Parse response
        
        Mock implementation for demonstration.
        """
        logger.info(f"MCP tool call: {tool_name} with input: {tool_input}")
        
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
        for name, process in self.servers.items():
            try:
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
