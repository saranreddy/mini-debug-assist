"""
AWS AppConfig MCP Server (read + write with approval gate)

Exposes AWS AppConfig feature flags to the agent.
Maps to Uber's flipr-mcp server.

MCP Tools:
- get_flag: Get current value of a feature flag
- list_flags: List all feature flags
- update_flag: Update a flag value (requires human approval)

Uber's approach: Fixes ship gated behind Flipr flags, and the agent
can propose rollbacks when correlation is strong (with approval).
"""

import json
import os
from typing import Any

from botocore.exceptions import ClientError
from mcp.server import Server
from mcp.types import TextContent, Tool

from demo_app.feature_flags import (
    DEFAULT_APPLICATION,
    DEFAULT_ENVIRONMENT,
    DEFAULT_PROFILE,
    AppConfigFlagClient,
    flag_key,
    flag_state,
)
from mcp_servers._runtime import aws_region, run_stdio

# Initialize MCP server
app = Server("appconfig-flags-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="get_flag",
            description=(
                "Get the current value of a feature flag. "
                "Use this to check flag states when investigating issues."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "flag_name": {
                        "type": "string",
                        "description": "Feature flag name (e.g. DISCOUNT_V2 or discount_v2)",
                    },
                    "application": {
                        "type": "string",
                        "description": f"AppConfig application (default: {DEFAULT_APPLICATION})",
                    },
                    "environment": {
                        "type": "string",
                        "description": f"AppConfig environment (default: {DEFAULT_ENVIRONMENT})",
                    },
                },
                "required": ["flag_name"],
            },
        ),
        Tool(
            name="list_flags",
            description="List all feature flags in an AppConfig configuration",
            inputSchema={
                "type": "object",
                "properties": {
                    "application": {
                        "type": "string",
                        "description": f"AppConfig application (default: {DEFAULT_APPLICATION})",
                    },
                    "environment": {
                        "type": "string",
                        "description": f"AppConfig environment (default: {DEFAULT_ENVIRONMENT})",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="update_flag",
            description=(
                "Update a feature flag value (REQUIRES HUMAN APPROVAL). "
                "Use this to propose a flag rollback as mitigation. "
                "The agent should only propose this when correlation is strong."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "flag_name": {
                        "type": "string",
                        "description": "Feature flag name (e.g. DISCOUNT_V2 or discount_v2)",
                    },
                    "new_value": {
                        "type": "string",
                        "description": "New flag value",
                    },
                    "application": {
                        "type": "string",
                        "description": f"AppConfig application (default: {DEFAULT_APPLICATION})",
                    },
                    "environment": {
                        "type": "string",
                        "description": f"AppConfig environment (default: {DEFAULT_ENVIRONMENT})",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for the change",
                    },
                },
                "required": ["flag_name", "new_value", "reason"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    if name == "get_flag":
        return await _get_flag(arguments)
    elif name == "list_flags":
        return await _list_flags(arguments)
    elif name == "update_flag":
        return await _update_flag(arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")


def _flag_client(args: dict) -> AppConfigFlagClient:
    """AppConfig Data client for the application/environment in ``args`` (or the defaults)."""
    return AppConfigFlagClient(
        application=args.get("application")
        or os.getenv("APPCONFIG_APPLICATION", DEFAULT_APPLICATION),
        environment=args.get("environment")
        or os.getenv("APPCONFIG_ENVIRONMENT", DEFAULT_ENVIRONMENT),
        profile=os.getenv("APPCONFIG_CONFIGURATION", DEFAULT_PROFILE),
        region=aws_region(),
    )


async def _get_flag(args: dict) -> list[TextContent]:
    """Get a feature flag value ("on"/"off")."""
    flag_name = args["flag_name"]
    client = _flag_client(args)
    where = f"{client.application}/{client.environment}"

    try:
        value = flag_state(client.get_flags(), flag_name)
        if value is None:
            text = f"Flag '{flag_name}' (key '{flag_key(flag_name)}') not found in {where}"
        else:
            text = f"Flag '{flag_name}' = '{value}' (AppConfig key '{flag_key(flag_name)}')"
        return [TextContent(type="text", text=text)]

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return [
                TextContent(
                    type="text",
                    text=f"No deployed feature-flag configuration found in {where}",
                )
            ]
        return [TextContent(type="text", text=f"Error getting flag: {e}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error getting flag: {e}")]


async def _list_flags(args: dict) -> list[TextContent]:
    """List all feature flags with their on/off state."""
    client = _flag_client(args)
    where = f"{client.application}/{client.environment}"

    try:
        flags = client.get_flags()
        states = {key: flag_state(flags, key) for key in flags}
        formatted = json.dumps(states, indent=2)
        return [TextContent(type="text", text=f"Feature flags in {where}:\n\n{formatted}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error listing flags: {e}")]


async def _update_flag(args: dict) -> list[TextContent]:
    """
    Update a feature flag (with human approval check).

    Uber's approach: Feature flag rollbacks require correlation and approval.
    In production, this would trigger a human approval workflow.
    """
    flag_name = args["flag_name"]
    new_value = args["new_value"]
    reason = args["reason"]

    # Check if human approval is bypassed (for testing)
    bypass_approval = os.getenv("BYPASS_FLAG_APPROVAL", "false").lower() == "true"

    if not bypass_approval:
        return [
            TextContent(
                type="text",
                text=(
                    f"FLAG UPDATE REQUIRES HUMAN APPROVAL\n\n"
                    f"Proposed change:\n"
                    f"  Flag: {flag_name}\n"
                    f"  New value: {new_value}\n"
                    f"  Reason: {reason}\n\n"
                    f"This change has been queued for human review. "
                    f"Set BYPASS_FLAG_APPROVAL=true to test without approval."
                ),
            )
        ]

    # In production, this would update AppConfig
    # For learning purposes, we just simulate approval
    return [
        TextContent(
            type="text",
            text=(
                f"Flag update approved (SIMULATED):\n"
                f"  Flag: {flag_name}\n"
                f"  New value: {new_value}\n"
                f"  Reason: {reason}\n\n"
                f"In production, this would deploy via AppConfig."
            ),
        )
    ]


def main():
    """Run the MCP server."""
    run_stdio(app)


if __name__ == "__main__":
    main()
