"""
GitHub MCP Server (read + create PR)

Exposes GitHub code search and PR creation to the agent.
Maps to Uber's Sourcegraph MCP server.

MCP Tools:
- search_code: Search code across the repository
- read_file: Read a file from the repository
- create_pr: Create a pull request (write operation)
"""

import base64
import json
import os
from typing import Any

from github import Github
from mcp.server import Server
from mcp.types import TextContent, Tool

from mcp_servers._runtime import run_stdio

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


def _repo_name(args: dict) -> str:
    """Repository from the tool arguments, else GITHUB_REPO (set on the agent task)."""
    repo_name = args.get("repo") or os.getenv("GITHUB_REPO")
    if not repo_name:
        raise ValueError("No repository given and GITHUB_REPO is not set")
    return repo_name


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
                    "pattern": {
                        "type": "string",
                        "description": "Alias for query (the agent's tool spec uses this name)",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Limit the search to this path",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository 'owner/repo' (default: GITHUB_REPO)",
                    },
                    "max_results": {
                        "type": "number",
                        "description": "Max results to return (default: 10)",
                    },
                },
                "required": [],
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
                        "description": "Repository 'owner/repo' (default: GITHUB_REPO)",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path within the repository",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Git ref (branch, tag, SHA; default: repo default branch)",
                    },
                },
                "required": ["path"],
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
                        "description": "Repository 'owner/repo' (default: GITHUB_REPO)",
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
                        "description": "Base branch (default: the repo default branch)",
                    },
                },
                "required": ["title", "body", "head"],
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
    max_results = int(args.get("max_results", 10))

    try:
        query = args.get("query") or args.get("pattern")
        if not query:
            raise ValueError("search_code needs 'query' (or 'pattern')")
        repo_name = _repo_name(args)
        gh = _get_github_client()

        # Build search query
        search_query = f"{query} repo:{repo_name}"
        if args.get("file_path"):
            search_query += f" path:{args['file_path']}"

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
    path = args["path"]

    try:
        gh = _get_github_client()
        repo = gh.get_repo(_repo_name(args))
        ref = args.get("ref") or repo.default_branch

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
    title = args["title"]
    body = args["body"]
    head = args["head"]

    try:
        gh = _get_github_client()
        repo = gh.get_repo(_repo_name(args))
        base = args.get("base") or repo.default_branch

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
    run_stdio(app)


if __name__ == "__main__":
    main()
