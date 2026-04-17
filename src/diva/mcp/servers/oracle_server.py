"""MCP server for Oracle Database — read-only SQL access."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

app = Server("oracle")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)


def _assert_readonly(query: str) -> None:
    """Reject anything that is not a SELECT statement."""
    if not _SELECT_RE.match(query):
        raise ValueError(
            "Only SELECT queries are allowed. "
            "The submitted query does not start with SELECT."
        )


async def _get_connection():
    """Create an async Oracle connection from environment variables."""
    try:
        import oracledb
    except ImportError:
        raise RuntimeError(
            "oracledb is not installed. Install it with: pip install oracledb"
        )

    dsn = os.environ.get("ORACLE_DSN")
    user = os.environ.get("ORACLE_USER")
    password = os.environ.get("ORACLE_PASSWORD")

    if not all([dsn, user, password]):
        raise RuntimeError(
            "Oracle connection requires ORACLE_DSN, ORACLE_USER, and "
            "ORACLE_PASSWORD environment variables."
        )

    try:
        conn = await oracledb.connect_async(
            dsn=dsn, user=user, password=password
        )
        return conn
    except Exception as exc:
        raise RuntimeError(f"Failed to connect to Oracle: {exc}") from exc


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="query_oracle",
        description=(
            "Execute a read-only SQL SELECT query against Oracle Database. "
            "Only SELECT statements are permitted."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "SQL SELECT query to execute.",
                },
                "max_rows": {
                    "type": "integer",
                    "description": "Maximum number of rows to return (default 100).",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="list_tables",
        description="List all tables in an Oracle schema with their column counts.",
        inputSchema={
            "type": "object",
            "properties": {
                "schema_name": {
                    "type": "string",
                    "description": "Oracle schema name (owner).",
                },
            },
            "required": ["schema_name"],
        },
    ),
    Tool(
        name="describe_table",
        description=(
            "Describe an Oracle table — columns, data types, and constraints."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Table name.",
                },
                "schema_name": {
                    "type": "string",
                    "description": "Schema (owner). Uses connection default if omitted.",
                },
            },
            "required": ["table_name"],
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
        if name == "query_oracle":
            return await _handle_query_oracle(arguments)
        elif name == "list_tables":
            return await _handle_list_tables(arguments)
        elif name == "describe_table":
            return await _handle_describe_table(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as exc:
        logger.exception("Error in tool %s", name)
        return [TextContent(type="text", text=f"Error: {exc}")]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def _handle_query_oracle(args: dict[str, Any]) -> list[TextContent]:
    query: str = args["query"]
    max_rows: int = args.get("max_rows", 100)

    _assert_readonly(query)

    conn = await _get_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute(query)
            columns = [col[0] for col in cur.description] if cur.description else []
            rows = await cur.fetchmany(max_rows)

        result = {
            "columns": columns,
            "rows": [list(row) for row in rows],
            "row_count": len(rows),
            "truncated": len(rows) == max_rows,
        }
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    finally:
        await conn.close()


async def _handle_list_tables(args: dict[str, Any]) -> list[TextContent]:
    schema_name: str = args["schema_name"].upper()

    query = (
        "SELECT t.TABLE_NAME, COUNT(c.COLUMN_NAME) AS COLUMN_COUNT "
        "FROM ALL_TABLES t "
        "LEFT JOIN ALL_TAB_COLUMNS c "
        "  ON t.OWNER = c.OWNER AND t.TABLE_NAME = c.TABLE_NAME "
        "WHERE t.OWNER = :schema_name "
        "GROUP BY t.TABLE_NAME "
        "ORDER BY t.TABLE_NAME"
    )

    conn = await _get_connection()
    try:
        async with conn.cursor() as cur:
            await cur.execute(query, {"schema_name": schema_name})
            rows = await cur.fetchall()

        tables = [
            {"table_name": row[0], "column_count": row[1]} for row in rows
        ]
        result = {"schema": schema_name, "tables": tables, "table_count": len(tables)}
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    finally:
        await conn.close()


async def _handle_describe_table(args: dict[str, Any]) -> list[TextContent]:
    table_name: str = args["table_name"].upper()
    schema_name: str | None = args.get("schema_name")
    schema_name = schema_name.upper() if schema_name else None

    conn = await _get_connection()
    try:
        # Columns
        col_query = (
            "SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, NULLABLE, DATA_DEFAULT "
            "FROM ALL_TAB_COLUMNS "
            "WHERE TABLE_NAME = :table_name"
        )
        params: dict[str, Any] = {"table_name": table_name}
        if schema_name:
            col_query += " AND OWNER = :schema_name"
            params["schema_name"] = schema_name
        col_query += " ORDER BY COLUMN_ID"

        async with conn.cursor() as cur:
            await cur.execute(col_query, params)
            col_rows = await cur.fetchall()

        columns = [
            {
                "name": r[0],
                "type": r[1],
                "length": r[2],
                "nullable": r[3] == "Y",
                "default": r[4],
            }
            for r in col_rows
        ]

        # Constraints
        cons_query = (
            "SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE, SEARCH_CONDITION "
            "FROM ALL_CONSTRAINTS "
            "WHERE TABLE_NAME = :table_name"
        )
        cons_params: dict[str, Any] = {"table_name": table_name}
        if schema_name:
            cons_query += " AND OWNER = :schema_name"
            cons_params["schema_name"] = schema_name

        async with conn.cursor() as cur:
            await cur.execute(cons_query, cons_params)
            cons_rows = await cur.fetchall()

        constraints = [
            {
                "name": r[0],
                "type": r[1],
                "condition": r[2],
            }
            for r in cons_rows
        ]

        result = {
            "table_name": table_name,
            "schema": schema_name,
            "columns": columns,
            "constraints": constraints,
        }
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
