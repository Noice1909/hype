"""MCP server for Google Dataplex — data catalog and quality metrics."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

app = Server("dataplex")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_project() -> str:
    project = os.environ.get("DATAPLEX_PROJECT")
    if not project:
        raise RuntimeError("DATAPLEX_PROJECT environment variable is required.")
    return project


def _get_location() -> str:
    return os.environ.get("DATAPLEX_LOCATION", "us-central1")


def _get_catalog_client():
    try:
        from google.cloud import dataplex_v1
    except ImportError:
        raise RuntimeError(
            "google-cloud-dataplex is not installed. "
            "Install it with: pip install google-cloud-dataplex"
        )
    return dataplex_v1.CatalogServiceClient()


def _get_dataplex_client():
    try:
        from google.cloud import dataplex_v1
    except ImportError:
        raise RuntimeError(
            "google-cloud-dataplex is not installed. "
            "Install it with: pip install google-cloud-dataplex"
        )
    return dataplex_v1.DataplexServiceClient()


def _get_quality_client():
    try:
        from google.cloud import dataplex_v1
    except ImportError:
        raise RuntimeError(
            "google-cloud-dataplex is not installed. "
            "Install it with: pip install google-cloud-dataplex"
        )
    return dataplex_v1.DataScanServiceClient()


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="search_catalog",
        description="Search the Dataplex data catalog for entries matching a query.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string.",
                },
                "project": {
                    "type": "string",
                    "description": "GCP project ID. Defaults to DATAPLEX_PROJECT env var.",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_quality_scores",
        description="Get data quality metrics for a table from Dataplex data quality scans.",
        inputSchema={
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Fully qualified table name (e.g. project.dataset.table).",
                },
            },
            "required": ["table_name"],
        },
    ),
    Tool(
        name="list_assets",
        description="List data assets in Dataplex, optionally filtered by zone.",
        inputSchema={
            "type": "object",
            "properties": {
                "zone": {
                    "type": "string",
                    "description": "Dataplex zone ID to filter by.",
                },
                "project": {
                    "type": "string",
                    "description": "GCP project ID. Defaults to DATAPLEX_PROJECT env var.",
                },
            },
            "required": [],
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
        if name == "search_catalog":
            return await _handle_search_catalog(arguments)
        elif name == "get_quality_scores":
            return await _handle_get_quality_scores(arguments)
        elif name == "list_assets":
            return await _handle_list_assets(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as exc:
        logger.exception("Error in tool %s", name)
        return [TextContent(type="text", text=f"Error: {exc}")]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def _handle_search_catalog(args: dict[str, Any]) -> list[TextContent]:
    from google.cloud import dataplex_v1

    query: str = args["query"]
    project: str = args.get("project") or _get_project()
    location = _get_location()

    client = _get_catalog_client()
    parent = f"projects/{project}/locations/{location}"

    request = dataplex_v1.SearchEntriesRequest(
        name=parent,
        query=query,
    )

    results = []
    for result in client.search_entries(request=request):
        entry = {
            "name": result.dataplex_entry.name if hasattr(result, "dataplex_entry") else str(result),
            "display_name": getattr(result.dataplex_entry, "display_name", ""),
            "entry_type": getattr(result.dataplex_entry, "entry_type", ""),
        }
        results.append(entry)

    output = {"query": query, "project": project, "results": results, "count": len(results)}
    return [TextContent(type="text", text=json.dumps(output, default=str))]


async def _handle_get_quality_scores(args: dict[str, Any]) -> list[TextContent]:
    from google.cloud import dataplex_v1

    table_name: str = args["table_name"]
    project = _get_project()
    location = _get_location()

    client = _get_quality_client()
    parent = f"projects/{project}/locations/{location}"

    # List data scans and find ones matching the table
    request = dataplex_v1.ListDataScansRequest(parent=parent)
    matching_scans = []
    for scan in client.list_data_scans(request=request):
        if hasattr(scan, "data") and table_name in getattr(scan.data, "resource", ""):
            matching_scans.append(scan.name)

    if not matching_scans:
        return [
            TextContent(
                type="text",
                text=json.dumps({
                    "table_name": table_name,
                    "error": "No data quality scans found for this table.",
                }),
            )
        ]

    # Get the latest scan job results for each matching scan
    quality_results = []
    for scan_name in matching_scans:
        jobs_request = dataplex_v1.ListDataScanJobsRequest(parent=scan_name)
        for job in client.list_data_scan_jobs(request=jobs_request):
            job_detail = client.get_data_scan_job(
                request=dataplex_v1.GetDataScanJobRequest(name=job.name)
            )
            if hasattr(job_detail, "data_quality_result"):
                dq = job_detail.data_quality_result
                quality_results.append({
                    "scan": scan_name,
                    "job": job.name,
                    "passed": getattr(dq, "passed", None),
                    "score": getattr(dq, "score", None),
                    "dimensions": [
                        {"dimension": d.dimension, "passed": d.passed, "score": d.score}
                        for d in getattr(dq, "dimensions", [])
                    ],
                })
            break  # only latest job per scan

    output = {"table_name": table_name, "quality_results": quality_results}
    return [TextContent(type="text", text=json.dumps(output, default=str))]


async def _handle_list_assets(args: dict[str, Any]) -> list[TextContent]:
    from google.cloud import dataplex_v1

    project: str = args.get("project") or _get_project()
    location = _get_location()
    zone: str | None = args.get("zone")

    client = _get_dataplex_client()

    # List lakes first, then zones, then assets
    lakes_parent = f"projects/{project}/locations/{location}"
    assets = []

    request = dataplex_v1.ListLakesRequest(parent=lakes_parent)
    for lake in client.list_lakes(request=request):
        zones_request = dataplex_v1.ListZonesRequest(parent=lake.name)
        for z in client.list_zones(request=zones_request):
            if zone and z.name.split("/")[-1] != zone:
                continue
            assets_request = dataplex_v1.ListAssetsRequest(parent=z.name)
            for asset in client.list_assets(request=assets_request):
                assets.append({
                    "name": asset.name,
                    "display_name": asset.display_name,
                    "lake": lake.display_name,
                    "zone": z.display_name,
                    "state": str(asset.state),
                    "resource_spec": str(asset.resource_spec),
                })

    output = {
        "project": project,
        "zone_filter": zone,
        "assets": assets,
        "count": len(assets),
    }
    return [TextContent(type="text", text=json.dumps(output, default=str))]


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
