"""
AWS X-Ray MCP Server (read-only)

Exposes X-Ray distributed tracing to the agent.
Maps to Uber's Jaeger MCP server.

MCP Tools:
- get_trace: Get a specific trace by ID
- query_traces: Query traces by filter expression
"""

import json
from datetime import datetime, timedelta
from typing import Any

import boto3
from mcp.server import Server
from mcp.types import TextContent, Tool

from mcp_servers._runtime import aws_region, run_stdio

# Initialize MCP server
app = Server("xray-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="get_trace",
            description=(
                "Get a specific X-Ray trace by ID. "
                "Use this to understand the full request flow for an error."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "trace_id": {
                        "type": "string",
                        "description": "X-Ray trace ID",
                    },
                },
                "required": ["trace_id"],
            },
        ),
        Tool(
            name="query_traces",
            description=(
                "Query X-Ray traces by filter expression. "
                "Use this to find traces related to errors or specific services."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filter_expression": {
                        "type": "string",
                        "description": (
                            "X-Ray filter expression. "
                            "Example: 'service(\"my-service\") AND http.status = 500'"
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
                "required": ["filter_expression"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    if name == "get_trace":
        return await _get_trace(arguments)
    elif name == "query_traces":
        return await _query_traces(arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")


async def _get_trace(args: dict) -> list[TextContent]:
    """Get a specific trace by ID."""
    trace_id = args["trace_id"]

    try:
        client = boto3.client("xray", region_name=aws_region())

        # Get trace
        response = client.batch_get_traces(
            TraceIds=[trace_id],
        )

        traces = response.get("Traces", [])

        if not traces:
            return [
                TextContent(
                    type="text",
                    text=f"Trace {trace_id} not found",
                )
            ]

        trace = traces[0]

        # Format trace (simplified)
        formatted = {
            "id": trace["Id"],
            "duration": trace.get("Duration"),
            "segments": len(trace.get("Segments", [])),
        }

        formatted_json = json.dumps(formatted, indent=2)

        return [
            TextContent(
                type="text",
                text=f"Trace details:\n\n{formatted_json}",
            )
        ]

    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error getting trace: {e}",
            )
        ]


async def _query_traces(args: dict) -> list[TextContent]:
    """Query traces by filter expression."""
    filter_expression = args["filter_expression"]

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
        client = boto3.client("xray", region_name=aws_region())

        # Get trace summaries
        response = client.get_trace_summaries(
            StartTime=start_time,
            EndTime=end_time,
            FilterExpression=filter_expression,
        )

        summaries = response.get("TraceSummaries", [])

        # Format summaries
        formatted_summaries = [
            {
                "id": s["Id"],
                "duration": s.get("Duration"),
                "has_error": s.get("HasError", False),
                "http_status": s.get("Http", {}).get("HttpStatus"),
            }
            for s in summaries[:20]  # Limit to 20 results
        ]

        formatted = json.dumps(formatted_summaries, indent=2)

        return [
            TextContent(
                type="text",
                text=f"Found {len(formatted_summaries)} traces:\n\n{formatted}",
            )
        ]

    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error querying traces: {e}",
            )
        ]


def main():
    """Run the MCP server."""
    run_stdio(app)


if __name__ == "__main__":
    main()
