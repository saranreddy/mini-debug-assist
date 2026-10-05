"""
GitHub MCP Server (read + create PR)

Exposes GitHub code search and PR creation to the agent.
Maps to Uber's Sourcegraph MCP server.

MCP Tools:
- search_code: Search code across the repository
- read_file: Read a file from the repository
- create_pr: Create a pull request (write operation)
"""

import asyncio
import base64
import os
from typing import Any

from github import Github
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# Initialize MCP server
app = Server("github-mcp")

# GitHub client (lazy initialization)
_gh_client: Github | None = None


def _get_github_client() -> Github:
    """Get or initialize GitHub client."""
    global _gh_client
    if _gh_client is None:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN environment variable not set")
        _gh_client = Github(token)
    return _gh_client


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="search_code",
            description=(
                "Search for code in the repository. "
                "Use this to find where specific functions, classes, or patterns are defined."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (supports GitHub code search syntax)",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository (e.g., 'owner/repo')",
                    },
                    "max_results": {
                        "type": "number",
                        "description": "Max results to return (default: 10)",
                    },
                },
                "required": ["query", "repo"],
            },
        ),
        Tool(
            name="read_file",
            description="Read a file from the repository",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository (e.g., 'owner/repo')",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path within the repository",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Git ref (branch, tag, commit SHA, default: main)",
                    },
                },
                "required": ["repo", "path"],
            },
        ),
        Tool(
            name="create_pr",
            description=(
                "Create a pull request. " "Use this after generating and validating a fix."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repository (e.g., 'owner/repo')",
                    },
                    "title": {
                        "type": "string",
                        "description": "PR title",
                    },
                    "body": {
                        "type": "string",
                        "description": "PR description",
                    },
                    "head": {
                        "type": "string",
                        "description": "Branch with changes",
                    },
                    "base": {
                        "type": "string",
                        "description": "Base branch (default: main)",
                    },
                },
                "required": ["repo", "title", "body", "head"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    if name == "search_code":
        return await _search_code(arguments)
    elif name == "read_file":
        return await _read_file(arguments)
    elif name == "create_pr":
        return await _create_pr(arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")


async def _search_code(args: dict) -> list[TextContent]:
    """Search code in repository."""
    query = args["query"]
    repo_name = args["repo"]
    max_results = args.get("max_results", 10)

    try:
        gh = _get_github_client()

        # Build search query
        search_query = f"{query} repo:{repo_name}"

        # Search
        results = gh.search_code(search_query)

        # Format results
        formatted_results = []
        for i, result in enumerate(results[:max_results]):
            formatted_results.append(
                {
                    "file": result.path,
                    "repo": result.repository.full_name,
                    "url": result.html_url,
                }
            )

        import json

        formatted = json.dumps(formatted_results, indent=2)

        return [
            TextContent(
                type="text",
                text=f"Found {len(formatted_results)} results:\n\n{formatted}",
            )
        ]

    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error searching code: {e}",
            )
        ]


async def _read_file(args: dict) -> list[TextContent]:
    """Read a file from repository."""
    repo_name = args["repo"]
    path = args["path"]
    ref = args.get("ref", "main")

    try:
        gh = _get_github_client()
        repo = gh.get_repo(repo_name)

        # Get file content
        file_content = repo.get_contents(path, ref=ref)

        # Decode content
        if isinstance(file_content, list):
            return [
                TextContent(
                    type="text",
                    text=f"Path {path} is a directory, not a file",
                )
            ]

        content = base64.b64decode(file_content.content).decode("utf-8")

        return [
            TextContent(
                type="text",
                text=f"File: {path}\n\n```\n{content}\n```",
            )
        ]

    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error reading file: {e}",
            )
        ]


async def _create_pr(args: dict) -> list[TextContent]:
    """Create a pull request."""
    repo_name = args["repo"]
    title = args["title"]
    body = args["body"]
    head = args["head"]
    base = args.get("base", "main")

    try:
        gh = _get_github_client()
        repo = gh.get_repo(repo_name)

        # Create PR
        pr = repo.create_pull(
            title=title,
            body=body,
            head=head,
            base=base,
        )

        return [
            TextContent(
                type="text",
                text=f"Created PR #{pr.number}: {pr.html_url}",
            )
        ]

    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error creating PR: {e}",
            )
        ]


def main():
    """Run the MCP server."""
    asyncio.run(stdio_server(app))


if __name__ == "__main__":
    main()
