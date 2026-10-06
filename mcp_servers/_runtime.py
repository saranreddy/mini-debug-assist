"""
Shared runtime helpers for the stdio MCP servers.

Every server in this package is started by ``agent/mcp_client.py`` as
``python -m mcp_servers.<name>`` and speaks JSON-RPC over stdin/stdout.
"""

import asyncio
import os

from mcp.server import Server
from mcp.server.stdio import stdio_server

DEFAULT_REGION = "us-east-1"


def aws_region() -> str:
    """Region for boto3 clients: AWS_REGION (set on the ECS task), then AWS_DEFAULT_REGION."""
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or DEFAULT_REGION


async def serve(app: Server) -> None:
    """Serve ``app`` over stdio until the client closes stdin."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def run_stdio(app: Server) -> None:
    """Blocking entry point used by each server's ``main()``."""
    asyncio.run(serve(app))
