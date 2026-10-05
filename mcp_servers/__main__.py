"""
MCP Servers entry point for module execution.

Usage:
    python -m mcp_servers.cloudwatch_logs
    python -m mcp_servers.xray_mcp
    python -m mcp_servers.github_mcp
    python -m mcp_servers.appconfig_flags
"""

import sys

print("Please specify which MCP server to run:")
print("  python -m mcp_servers.cloudwatch_logs")
print("  python -m mcp_servers.xray_mcp")
print("  python -m mcp_servers.github_mcp")
print("  python -m mcp_servers.appconfig_flags")
sys.exit(1)
