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

import asyncio
import json
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

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
                        "description": "Feature flag name",
                    },
                    "application": {
                        "type": "string",
                        "description": "AppConfig application name",
                    },
                    "environment": {
                        "type": "string",
                        "description": "AppConfig environment (default: dev)",
                    },
                },
                "required": ["flag_name", "application"],
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
                        "description": "AppConfig application name",
                    },
                    "environment": {
                        "type": "string",
                        "description": "AppConfig environment (default: dev)",
                    },
                },
                "required": ["application"],
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
                        "description": "Feature flag name",
                    },
                    "new_value": {
                        "type": "string",
                        "description": "New flag value",
                    },
                    "application": {
                        "type": "string",
                        "description": "AppConfig application name",
                    },
                    "environment": {
                        "type": "string",
                        "description": "AppConfig environment (default: dev)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for the change",
                    },
                },
                "required": ["flag_name", "new_value", "application", "reason"],
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


async def _get_flag(args: dict) -> list[TextContent]:
    """Get a feature flag value."""
    flag_name = args["flag_name"]
    application = args["application"]
    environment = args.get("environment", "dev")

    try:
        client = boto3.client("appconfigdata")

        # Start configuration session
        session_response = client.start_configuration_session(
            ApplicationIdentifier=application,
            EnvironmentIdentifier=environment,
            ConfigurationProfileIdentifier="feature-flags",
        )

        token = session_response["InitialConfigurationToken"]

        # Get latest configuration
        config_response = client.get_latest_configuration(ConfigurationToken=token)

        # Parse configuration
        config_data = json.loads(config_response["Configuration"].read())

        # Get flag value
        flag_value = config_data.get(flag_name, "NOT_FOUND")

        return [
            TextContent(
                type="text",
                text=f"Flag '{flag_name}' = '{flag_value}'",
            )
        ]

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return [
                TextContent(
                    type="text",
                    text=f"Flag '{flag_name}' not found in {application}/{environment}",
                )
            ]
        return [
            TextContent(
                type="text",
                text=f"Error getting flag: {e}",
            )
        ]
    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error getting flag: {e}",
            )
        ]


async def _list_flags(args: dict) -> list[TextContent]:
    """List all feature flags."""
    application = args["application"]
    environment = args.get("environment", "dev")

    try:
        client = boto3.client("appconfigdata")

        # Start configuration session
        session_response = client.start_configuration_session(
            ApplicationIdentifier=application,
            EnvironmentIdentifier=environment,
            ConfigurationProfileIdentifier="feature-flags",
        )

        token = session_response["InitialConfigurationToken"]

        # Get latest configuration
        config_response = client.get_latest_configuration(ConfigurationToken=token)

        # Parse configuration
        config_data = json.loads(config_response["Configuration"].read())

        # Format flags
        formatted = json.dumps(config_data, indent=2)

        return [
            TextContent(
                type="text",
                text=f"Feature flags in {application}/{environment}:\n\n{formatted}",
            )
        ]

    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error listing flags: {e}",
            )
        ]


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
    asyncio.run(stdio_server(app))


if __name__ == "__main__":
    main()
