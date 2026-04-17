"""Adapt MCP tools to LangChain BaseTool for use with LangChain agents."""

from __future__ import annotations

import json
from typing import Any, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, create_model

from diva.mcp.client import MCPClientManager


def _build_args_schema(tool_schema: dict) -> Type[BaseModel]:
    """Dynamically create a Pydantic model from an MCP tool's inputSchema."""
    properties = tool_schema.get("properties", {})
    required = set(tool_schema.get("required", []))

    fields: dict[str, Any] = {}
    for name, prop in properties.items():
        py_type = _json_type_to_python(prop.get("type", "string"))
        if name in required:
            fields[name] = (py_type, ...)
        else:
            fields[name] = (py_type | None, None)

    return create_model("MCPToolArgs", **fields)


def _json_type_to_python(json_type: str) -> type:
    mapping = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    return mapping.get(json_type, str)


class MCPToolAdapter(BaseTool):
    """Wraps an MCP tool as a LangChain BaseTool."""

    server_id: str
    mcp_tool_name: str
    mcp_manager: MCPClientManager

    class Config:
        arbitrary_types_allowed = True

    def _run(self, **kwargs: Any) -> str:
        raise NotImplementedError("Use async _arun")

    async def _arun(self, **kwargs: Any) -> str:
        result = await self.mcp_manager.call_tool(
            self.server_id, self.mcp_tool_name, kwargs
        )
        # MCP returns content list — extract text
        if hasattr(result, "content"):
            texts = [c.text for c in result.content if hasattr(c, "text")]
            return "\n".join(texts) if texts else str(result.content)
        return str(result)


def adapt_mcp_tools(
    server_id: str,
    mcp_tools: list,
    mcp_manager: MCPClientManager,
) -> list[BaseTool]:
    """Convert MCP tool definitions to LangChain BaseTools."""
    adapted = []
    for tool in mcp_tools:
        schema = tool.inputSchema if hasattr(tool, "inputSchema") else {}
        args_model = _build_args_schema(schema)

        adapted.append(
            MCPToolAdapter(
                name=tool.name,
                description=tool.description or f"MCP tool: {tool.name}",
                args_schema=args_model,
                server_id=server_id,
                mcp_tool_name=tool.name,
                mcp_manager=mcp_manager,
            )
        )
    return adapted
