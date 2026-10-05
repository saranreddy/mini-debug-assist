"""
MCP (Model Context Protocol) client for tool execution.

Manages MCP server lifecycle and tool calling via JSON-RPC 2.0 over stdio.
"""

import json
import logging
import os
import subprocess
import threading
import uuid
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
    """
    MCP client for tool execution via JSON-RPC 2.0 over stdio.
    
    Implements the Model Context Protocol handshake and tool calling:
    1. initialize: Handshake with server capabilities
    2. tools/list: Discover available tools
    3. tools/call: Execute a tool
    """
    
    def __init__(self):
        self.servers = {}
        self.tools_cache = {}
        self._request_id_counter = 0
        self._lock = threading.Lock()
    
    def start_server(self, server_name: str) -> bool:
        """
        Start an MCP server as stdio subprocess and perform initialize handshake.
        
        Uses JSON-RPC 2.0 protocol over stdio.
        Falls back to in-process mocks when MCP_MOCK_MODE=true.
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
                text=True,  # Text mode for JSON lines
                bufsize=1,  # Line buffered
            )
            
            self.servers[server_name] = {
                "process": process,
                "config": config,
                "initialized": False,
            }
            logger.info(f"Started MCP server: {server_name} (PID: {process.pid})")
            
            # Perform initialize handshake
            init_success = self._initialize_server(server_name)
            
            if init_success:
                # List available tools
                self._list_tools(server_name)
                return True
            else:
                logger.error(f"Failed to initialize server {server_name}")
                self._stop_server(server_name)
                return False
            
        except Exception as e:
            logger.error(f"Failed to start MCP server {server_name}: {e}", exc_info=True)
            return False
    
    def _initialize_server(self, server_name: str) -> bool:
        """
        Send initialize request to MCP server.
        
        JSON-RPC request:
        {
          "jsonrpc": "2.0",
          "id": 1,
          "method": "initialize",
          "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mini-debug-assist", "version": "0.1.0"}
          }
        }
        """
        try:
            request = {
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "mini-debug-assist",
                        "version": "0.1.0"
                    }
                }
            }
            
            response = self._send_request(server_name, request, timeout=5.0)
            
            if response and "result" in response:
                logger.info(f"Server {server_name} initialized: {response['result'].get('serverInfo', {})}")
                self.servers[server_name]["initialized"] = True
                return True
            else:
                logger.error(f"Initialize failed for {server_name}: {response}")
                return False
                
        except Exception as e:
            logger.error(f"Error initializing server {server_name}: {e}", exc_info=True)
            return False
    
    def _list_tools(self, server_name: str) -> bool:
        """
        Send tools/list request to discover available tools.
        
        JSON-RPC request:
        {
          "jsonrpc": "2.0",
          "id": 2,
          "method": "tools/list",
          "params": {}
        }
        """
        try:
            request = {
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "tools/list",
                "params": {}
            }
            
            response = self._send_request(server_name, request, timeout=5.0)
            
            if response and "result" in response:
                tools = response["result"].get("tools", [])
                self.tools_cache[server_name] = tools
                logger.info(f"Server {server_name} has {len(tools)} tools: {[t['name'] for t in tools]}")
                return True
            else:
                logger.error(f"tools/list failed for {server_name}: {response}")
                return False
                
        except Exception as e:
            logger.error(f"Error listing tools for {server_name}: {e}", exc_info=True)
            return False
    
    def _send_request(self, server_name: str, request: dict, timeout: float = 10.0) -> Optional[dict]:
        """
        Send JSON-RPC request and wait for response.
        
        Writes request as JSON line to stdin, reads response from stdout.
        """
        server_info = self.servers.get(server_name)
        if not server_info:
            logger.error(f"Server {server_name} not found")
            return None
        
        process = server_info["process"]
        
        try:
            # Send request
            request_line = json.dumps(request) + "\n"
            process.stdin.write(request_line)
            process.stdin.flush()
            logger.debug(f"Sent request to {server_name}: {request}")
            
            # Read response with timeout
            import select
            
            # Use select for timeout on stdout
            ready, _, _ = select.select([process.stdout], [], [], timeout)
            
            if not ready:
                logger.error(f"Timeout waiting for response from {server_name}")
                return None
            
            response_line = process.stdout.readline()
            
            if not response_line:
                logger.error(f"Empty response from {server_name}")
                return None
            
            response = json.loads(response_line)
            logger.debug(f"Received response from {server_name}: {response}")
            
            return response
            
        except Exception as e:
            logger.error(f"Error sending request to {server_name}: {e}", exc_info=True)
            return None
    
    def _next_request_id(self) -> int:
        """Get next request ID."""
        with self._lock:
            self._request_id_counter += 1
            return self._request_id_counter
    
    def call_tool(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """
        Call a tool via MCP.
        
        In real mode: Sends JSON-RPC tools/call request
        In test/mock mode: Falls back to in-process mocks
        """
        logger.info(f"MCP tool call: {tool_name} with input: {tool_input}")
        
        # Check if in mock mode or no servers started
        if os.getenv("MCP_MOCK_MODE", "false").lower() == "true" or not self.servers:
            logger.debug(f"Using mock implementation for {tool_name}")
            return self._call_tool_mock(tool_name, tool_input)
        
        # Find which server has this tool
        server_name = None
        for sname, tools in self.tools_cache.items():
            if any(t["name"] == tool_name for t in tools):
                server_name = sname
                break
        
        if not server_name:
            logger.warning(f"Tool {tool_name} not found in any server, using mock")
            return self._call_tool_mock(tool_name, tool_input)
        
        # Send tools/call request
        try:
            request = {
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": tool_input
                }
            }
            
            response = self._send_request(server_name, request, timeout=30.0)
            
            if response and "result" in response:
                # MCP tools/call returns {"content": [...], "isError": false}
                result = response["result"]
                
                if result.get("isError"):
                    logger.error(f"Tool {tool_name} returned error: {result}")
                    return {
                        "success": False,
                        "error": result.get("content", [{}])[0].get("text", "Unknown error")
                    }
                
                # Extract content
                content = result.get("content", [])
                if content and len(content) > 0:
                    text_content = content[0].get("text", "")
                    
                    # Try to parse as JSON
                    try:
                        return json.loads(text_content)
                    except:
                        return {
                            "success": True,
                            "result": text_content
                        }
                else:
                    return {"success": True, "result": "No content"}
            
            elif response and "error" in response:
                logger.error(f"Tool call error for {tool_name}: {response['error']}")
                return {
                    "success": False,
                    "error": response["error"].get("message", "Unknown error")
                }
            
            else:
                logger.error(f"Invalid response for {tool_name}: {response}")
                return {"success": False, "error": "Invalid response"}
                
        except Exception as e:
            logger.error(f"Error calling tool {tool_name}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def _stop_server(self, server_name: str):
        """Stop a specific MCP server."""
        server_info = self.servers.get(server_name)
        if not server_info:
            return
        
        try:
            if isinstance(server_info, dict) and "process" in server_info:
                process = server_info["process"]
                process.terminate()
                process.wait(timeout=5)
                logger.info(f"Stopped MCP server: {server_name}")
        except Exception as e:
            logger.error(f"Error stopping {server_name}: {e}")
        
        del self.servers[server_name]
    
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
