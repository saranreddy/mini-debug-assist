"""
MCP (Model Context Protocol) client for tool execution.

Manages MCP server lifecycle and tool calling via JSON-RPC 2.0 over stdio.
"""

import json
import logging
import os
import subprocess
import threading
from typing import Any

logger = logging.getLogger(__name__)

# MCP server configurations
MCP_SERVERS = {
    # Our own Python MCP servers (always available)
    "cloudwatch_logs": {
        "command": "python3",
        "args": ["-m", "mcp_servers.cloudwatch_logs"],
        "env": {
            "AWS_REGION": os.getenv("AWS_REGION", "us-east-1"),
        },
    },
    "xray": {
        "command": "python3",
        "args": ["-m", "mcp_servers.xray_mcp"],
        "env": {
            "AWS_REGION": os.getenv("AWS_REGION", "us-east-1"),
        },
    },
    "github_mcp": {
        "command": "python3",
        "args": ["-m", "mcp_servers.github_mcp"],
        "env": {
            "GITHUB_TOKEN": os.getenv("GITHUB_TOKEN", ""),
        },
    },
    "appconfig_flags": {
        "command": "python3",
        "args": ["-m", "mcp_servers.appconfig_flags"],
        "env": {
            "AWS_REGION": os.getenv("AWS_REGION", "us-east-1"),
        },
    },
    # Optional npm-based GitHub server (requires npx)
    "github_npm": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {
            "GITHUB_PERSONAL_ACCESS_TOKEN": os.getenv("GITHUB_TOKEN", ""),
        },
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
            env_update: dict[str, str] = config["env"]  # type: ignore[assignment]
            env.update(env_update)

            # Start server as stdio subprocess
            args_list: list[str] = list(config["args"])  # type: ignore[arg-type]
            command: str = str(config["command"])
            cmd_list: list[str] = [command] + args_list
            process = subprocess.Popen(
                cmd_list,
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
        Send initialize request to MCP server and then the initialized notification.

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

        Then send notifications/initialized notification (no response expected).
        """
        try:
            request = {
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "mini-debug-assist", "version": "0.1.0"},
                },
            }

            response = self._send_request(server_name, request, timeout=5.0)

            if response and "result" in response:
                logger.info(
                    f"Server {server_name} initialized: {response['result'].get('serverInfo', {})}"
                )
                self.servers[server_name]["initialized"] = True

                # Send notifications/initialized notification
                self._send_notification(server_name, "notifications/initialized")

                return True
            else:
                logger.error(f"Initialize failed for {server_name}: {response}")
                return False

        except Exception as e:
            logger.error(f"Error initializing server {server_name}: {e}", exc_info=True)
            return False

    def _send_notification(
        self, server_name: str, method: str, params: dict[str, Any] | None = None
    ):
        """
        Send a JSON-RPC notification (no id, no response expected).
        """
        server_info = self.servers.get(server_name)
        if not server_info:
            return

        process = server_info["process"]

        try:
            notification: dict[str, Any] = {
                "jsonrpc": "2.0",
                "method": method,
            }

            if params:
                notification["params"] = params

            notification_line = json.dumps(notification) + "\n"
            process.stdin.write(notification_line)
            process.stdin.flush()
            logger.debug(f"Sent notification to {server_name}: {method}")

        except Exception as e:
            logger.error(f"Error sending notification to {server_name}: {e}")

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
                "params": {},
            }

            response = self._send_request(server_name, request, timeout=5.0)

            if response and "result" in response:
                tools = response["result"].get("tools", [])
                self.tools_cache[server_name] = tools
                logger.info(
                    f"Server {server_name} has {len(tools)} tools: {[t['name'] for t in tools]}"
                )
                return True
            else:
                logger.error(f"tools/list failed for {server_name}: {response}")
                return False

        except Exception as e:
            logger.error(f"Error listing tools for {server_name}: {e}", exc_info=True)
            return False

    def _send_request(self, server_name: str, request: dict, timeout: float = 10.0) -> dict | None:
        """
        Send JSON-RPC request and wait for matching response by ID.

        Writes request as JSON line to stdin, reads responses from stdout.
        Skips notifications (no "id") and log lines, matches by request ID.
        """
        server_info = self.servers.get(server_name)
        if not server_info:
            logger.error(f"Server {server_name} not found")
            return None

        process = server_info["process"]
        request_id = request.get("id")

        if request_id is None:
            logger.error("Request must have an 'id' field")
            return None

        try:
            # Send request
            request_line = json.dumps(request) + "\n"
            process.stdin.write(request_line)
            process.stdin.flush()
            logger.debug(f"Sent request to {server_name}: {request}")

            # Read responses until we get a matching ID or timeout
            import select
            import time

            start_time = time.time()

            while time.time() - start_time < timeout:
                # Check if data is available with 1 second timeout per read
                remaining = timeout - (time.time() - start_time)
                if remaining <= 0:
                    break

                ready, _, _ = select.select([process.stdout], [], [], min(1.0, remaining))

                if not ready:
                    continue

                response_line = process.stdout.readline()

                if not response_line:
                    logger.warning(f"Empty line from {server_name}")
                    continue

                try:
                    response = json.loads(response_line)
                except json.JSONDecodeError:
                    logger.warning(f"Non-JSON line from {server_name}: {response_line[:100]}")
                    continue

                # Skip notifications (no "id" field)
                if "id" not in response:
                    method = response.get("method", "unknown")
                    logger.debug(f"Skipping notification from {server_name}: {method}")
                    continue

                # Check if this response matches our request
                if response["id"] == request_id:
                    logger.debug(f"Received matching response from {server_name}: {response}")
                    result_response: dict[Any, Any] = response
                    return result_response
                else:
                    logger.debug(
                        f"Skipping response with different ID: {response['id']} != {request_id}"
                    )

            logger.error(
                f"Timeout waiting for response from {server_name} (request ID: {request_id})"
            )
            return None

        except Exception as e:
            logger.error(f"Error sending request to {server_name}: {e}", exc_info=True)
            return None

    def _next_request_id(self) -> int:
        """Get next request ID."""
        with self._lock:
            self._request_id_counter += 1
            result: int = self._request_id_counter
            return result

    def call_tool(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """
        Call a tool via MCP.

        In real mode: Sends JSON-RPC tools/call request, returns error if tool unknown
        In mock mode (MCP_MOCK_MODE=true): Falls back to in-process mocks
        """
        logger.info(f"MCP tool call: {tool_name} with input: {tool_input}")

        # Check if in explicit mock mode
        mock_mode = os.getenv("MCP_MOCK_MODE", "false").lower() == "true"

        if mock_mode:
            logger.debug(f"MCP_MOCK_MODE=true, using mock implementation for {tool_name}")
            return self._call_tool_mock(tool_name, tool_input)

        # Real mode: never silently fall back to mocks
        if not self.servers:
            error_msg = (
                "No MCP servers started. Call start_server() first or set MCP_MOCK_MODE=true."
            )
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "isError": True,
            }

        # Find which server has this tool
        server_name = None
        for sname, tools in self.tools_cache.items():
            if any(t["name"] == tool_name for t in tools):
                server_name = sname
                break

        if not server_name:
            available = self._list_available_tools()
            error_msg = (
                f"Tool '{tool_name}' not found in any started server. "
                f"Available tools: {available}"
            )
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "isError": True,
            }

        # Send tools/call request
        try:
            request = {
                "jsonrpc": "2.0",
                "id": self._next_request_id(),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": tool_input},
            }

            response = self._send_request(server_name, request, timeout=30.0)

            if response and "result" in response:
                # MCP tools/call returns {"content": [...], "isError": false}
                result = response["result"]

                if result.get("isError"):
                    logger.error(f"Tool {tool_name} returned error: {result}")
                    return {
                        "success": False,
                        "error": result.get("content", [{}])[0].get("text", "Unknown error"),
                    }

                # Extract content
                content = result.get("content", [])
                if content and len(content) > 0:
                    text_content = content[0].get("text", "")

                    # Try to parse as JSON
                    try:
                        parsed: dict[str, Any] = json.loads(text_content)
                        return parsed
                    except (json.JSONDecodeError, ValueError):
                        return {"success": True, "result": text_content}
                else:
                    return {"success": True, "result": "No content"}

            elif response and "error" in response:
                logger.error(f"Tool call error for {tool_name}: {response['error']}")
                return {
                    "success": False,
                    "error": response["error"].get("message", "Unknown error"),
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

    def _list_available_tools(self) -> list[str]:
        """List all available tool names across all servers."""
        tools = []
        for server_tools in self.tools_cache.values():
            tools.extend([t["name"] for t in server_tools])
        return tools

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
_mcp_client: MCPClient | None = None


def get_mcp_client() -> MCPClient:
    """Get or create global MCP client."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client
