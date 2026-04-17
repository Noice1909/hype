"""MCP server for Broadcom Autosys — job scheduling read-only access."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

app = Server("autosys")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_api_url() -> str:
    url = os.environ.get("AUTOSYS_API_URL")
    if not url:
        raise RuntimeError("AUTOSYS_API_URL environment variable is required.")
    return url.rstrip("/")


def _get_headers() -> dict[str, str]:
    token = os.environ.get("AUTOSYS_TOKEN")
    if not token:
        raise RuntimeError("AUTOSYS_TOKEN environment variable is required.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def _get_client():
    try:
        import httpx
    except ImportError:
        raise RuntimeError("httpx is not installed. Install it with: pip install httpx")
    return httpx.AsyncClient(timeout=30.0)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="get_job_status",
        description="Get the current status of an Autosys job.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_name": {
                    "type": "string",
                    "description": "Name of the Autosys job.",
                },
            },
            "required": ["job_name"],
        },
    ),
    Tool(
        name="list_jobs",
        description="List Autosys jobs matching a pattern, optionally filtered by status.",
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Job name pattern with wildcards (default '*').",
                },
                "status": {
                    "type": "string",
                    "description": "Filter by job status (e.g. SUCCESS, FAILURE, RUNNING).",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="get_job_history",
        description="Get the run history of an Autosys job over recent days.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_name": {
                    "type": "string",
                    "description": "Name of the Autosys job.",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days of history to retrieve (default 7).",
                },
            },
            "required": ["job_name"],
        },
    ),
    Tool(
        name="get_job_dependencies",
        description="Get upstream and downstream dependencies of an Autosys job.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_name": {
                    "type": "string",
                    "description": "Name of the Autosys job.",
                },
            },
            "required": ["job_name"],
        },
    ),
]

# ---------------------------------------------------------------------------
# MCP handlers
# ---------------------------------------------------------------------------


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "get_job_status":
            return await _handle_get_job_status(arguments)
        elif name == "list_jobs":
            return await _handle_list_jobs(arguments)
        elif name == "get_job_history":
            return await _handle_get_job_history(arguments)
        elif name == "get_job_dependencies":
            return await _handle_get_job_dependencies(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as exc:
        logger.exception("Error in tool %s", name)
        return [TextContent(type="text", text=f"Error: {exc}")]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def _handle_get_job_status(args: dict[str, Any]) -> list[TextContent]:
    job_name: str = args["job_name"]
    base_url = _get_api_url()
    headers = _get_headers()

    client = await _get_client()
    try:
        resp = await client.get(f"{base_url}/jobs/{job_name}/status", headers=headers)
        resp.raise_for_status()
        data = resp.json()

        result = {
            "job_name": job_name,
            "status": data.get("status", "UNKNOWN"),
            "last_start": data.get("lastStart"),
            "last_end": data.get("lastEnd"),
            "exit_code": data.get("exitCode"),
            "machine": data.get("machine"),
            "run_num": data.get("runNum"),
        }
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    finally:
        await client.aclose()


async def _handle_list_jobs(args: dict[str, Any]) -> list[TextContent]:
    pattern: str = args.get("pattern", "*")
    status: str | None = args.get("status")
    base_url = _get_api_url()
    headers = _get_headers()

    params: dict[str, str] = {"pattern": pattern}
    if status:
        params["status"] = status

    client = await _get_client()
    try:
        resp = await client.get(f"{base_url}/jobs", params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        jobs = []
        for job in data.get("jobs", data if isinstance(data, list) else []):
            jobs.append({
                "job_name": job.get("jobName", job.get("name", "")),
                "status": job.get("status", "UNKNOWN"),
                "job_type": job.get("jobType", ""),
                "machine": job.get("machine", ""),
            })

        result = {"pattern": pattern, "status_filter": status, "jobs": jobs, "count": len(jobs)}
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    finally:
        await client.aclose()


async def _handle_get_job_history(args: dict[str, Any]) -> list[TextContent]:
    job_name: str = args["job_name"]
    days: int = args.get("days", 7)
    base_url = _get_api_url()
    headers = _get_headers()

    start_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    params = {"startDate": start_date}

    client = await _get_client()
    try:
        resp = await client.get(
            f"{base_url}/jobs/{job_name}/history", params=params, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()

        runs = []
        for run in data.get("runs", data if isinstance(data, list) else []):
            runs.append({
                "run_num": run.get("runNum"),
                "status": run.get("status"),
                "start_time": run.get("startTime"),
                "end_time": run.get("endTime"),
                "exit_code": run.get("exitCode"),
                "duration_seconds": run.get("durationSeconds"),
            })

        result = {
            "job_name": job_name,
            "days": days,
            "runs": runs,
            "run_count": len(runs),
        }
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    finally:
        await client.aclose()


async def _handle_get_job_dependencies(args: dict[str, Any]) -> list[TextContent]:
    job_name: str = args["job_name"]
    base_url = _get_api_url()
    headers = _get_headers()

    client = await _get_client()
    try:
        resp = await client.get(
            f"{base_url}/jobs/{job_name}/dependencies", headers=headers
        )
        resp.raise_for_status()
        data = resp.json()

        result = {
            "job_name": job_name,
            "upstream": data.get("upstream", data.get("conditions", [])),
            "downstream": data.get("downstream", data.get("dependents", [])),
        }
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
