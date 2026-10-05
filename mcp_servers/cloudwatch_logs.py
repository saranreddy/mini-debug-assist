"""
CloudWatch Logs MCP Server (read-only)

Exposes CloudWatch Logs Insights queries to the agent.
Maps to Uber's logging MCP server (queries their ClickHouse-based log platform).

MCP Tools:
- query_logs: Run a Logs Insights query
- get_log_streams: List log streams for a log group
"""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import boto3
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# Initialize MCP server
app = Server("cloudwatch-logs-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="query_logs",
            description=(
                "Run a CloudWatch Logs Insights query to search application logs. "
                "Use this to find errors, exceptions, and relevant log entries "
                "around the time of an issue."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "log_group": {
                        "type": "string",
                        "description": "Log group name (e.g., '/aws/ecs/my-service')",
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Logs Insights query string. "
                            "Example: 'fields @timestamp, @message | "
                            'filter level = "ERROR" | limit 100\''
                        ),
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Start time (ISO format, default: 1 hour ago)",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "End time (ISO format, default: now)",
                    },
                },
                "required": ["log_group", "query"],
            },
        ),
        Tool(
            name="get_log_streams",
            description="List log streams in a log group",
            inputSchema={
                "type": "object",
                "properties": {
                    "log_group": {
                        "type": "string",
                        "description": "Log group name",
                    },
                    "limit": {
                        "type": "number",
                        "description": "Max streams to return (default: 50)",
                    },
                },
                "required": ["log_group"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    if name == "query_logs":
        return await _query_logs(arguments)
    elif name == "get_log_streams":
        return await _get_log_streams(arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")


async def _query_logs(args: dict) -> list[TextContent]:
    """
    Run a CloudWatch Logs Insights query.

    Note: This is a simplified implementation. Production would handle
    query polling, pagination, and result formatting more robustly.
    """
    log_group = args["log_group"]
    query = args["query"]

    # Parse time range
    if "start_time" in args:
        start_time = datetime.fromisoformat(args["start_time"])
    else:
        start_time = datetime.now() - timedelta(hours=1)

    if "end_time" in args:
        end_time = datetime.fromisoformat(args["end_time"])
    else:
        end_time = datetime.now()

    try:
        client = boto3.client("logs")

        # Start query
        response = client.start_query(
            logGroupName=log_group,
            startTime=int(start_time.timestamp()),
            endTime=int(end_time.timestamp()),
            queryString=query,
        )

        query_id = response["queryId"]

        # Poll for results (simplified - should add timeout and error handling)
        for _ in range(30):  # Max 30 seconds
            await asyncio.sleep(1)

            results_response = client.get_query_results(queryId=query_id)
            status = results_response["status"]

            if status == "Complete":
                results = results_response["results"]

                # Format results
                formatted = json.dumps(results, indent=2)

                return [
                    TextContent(
                        type="text",
                        text=f"Query completed. Found {len(results)} results:\n\n{formatted}",
                    )
                ]

            elif status == "Failed":
                return [
                    TextContent(
                        type="text",
                        text=f"Query failed: {results_response.get('statistics', {})}",
                    )
                ]

        return [
            TextContent(
                type="text",
                text="Query timed out after 30 seconds",
            )
        ]

    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error querying logs: {e}",
            )
        ]


async def _get_log_streams(args: dict) -> list[TextContent]:
    """List log streams in a log group."""
    log_group = args["log_group"]
    limit = args.get("limit", 50)

    try:
        client = boto3.client("logs")

        response = client.describe_log_streams(
            logGroupName=log_group,
            orderBy="LastEventTime",
            descending=True,
            limit=limit,
        )

        streams = response["logStreams"]

        # Format streams
        stream_list = [
            {
                "name": s["logStreamName"],
                "last_event": datetime.fromtimestamp(s["lastEventTimestamp"] / 1000).isoformat(),
                "stored_bytes": s.get("storedBytes", 0),
            }
            for s in streams
        ]

        formatted = json.dumps(stream_list, indent=2)

        return [
            TextContent(
                type="text",
                text=f"Found {len(streams)} log streams:\n\n{formatted}",
            )
        ]

    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error listing log streams: {e}",
            )
        ]


def main():
    """Run the MCP server."""
    asyncio.run(stdio_server(app))


if __name__ == "__main__":
    main()
