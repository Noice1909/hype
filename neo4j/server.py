"""Neo4j FastMCP server — graph database tools over SSE / stdio / HTTP.

Run modes:
  SSE (default, port 3006):
      python mcp_servers/neo4j/server.py --transport sse --port 3006

  stdio:
      python mcp_servers/neo4j/server.py --transport stdio

  Streamable HTTP:
      python mcp_servers/neo4j/server.py --transport http --port 3006

Env vars:
  NEO4J_URI          bolt://localhost:7687
  NEO4J_USER         neo4j
  NEO4J_PASSWORD     (required)
  NEO4J_DATABASE     neo4j

The server exposes 7 tools:
  run_cypher, get_schema, get_relationship_patterns, search,
  get_node_by_id, get_neighbors, count_nodes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import time
from typing import Annotated, Any

from contextlib import asynccontextmanager
from typing import AsyncIterator

from pydantic import Field
from fastmcp import FastMCP  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# ── Neo4j driver (module-level singleton) ────────────────────────────────────

_driver: Any = None
_database: str = "neo4j"


async def _get_driver() -> Any:
    """Lazily create and return the Neo4j async driver.

    The neo4j Python driver handles neo4j+s:// and bolt+s:// natively.
    We set SSL_CERT_FILE to certifi's CA bundle so that Aura's certificate
    chain is trusted even when the system CA store is incomplete (common on
    Windows).
    """
    global _driver, _database
    if _driver is not None:
        return _driver

    # Ensure SSL works for neo4j+s:// URIs (Aura) on all platforms
    if "SSL_CERT_FILE" not in os.environ:
        try:
            import certifi
            os.environ["SSL_CERT_FILE"] = certifi.where()
        except ImportError:
            pass  # Fall back to system certs

    import neo4j  # type: ignore[import-untyped]

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    _database = os.environ.get("NEO4J_DATABASE", "neo4j")

    drv = neo4j.AsyncGraphDatabase.driver(
        uri,
        auth=(user, password),
        max_connection_pool_size=50,
        connection_acquisition_timeout=60.0,
        connection_timeout=30.0,
        keep_alive=True,
    )
    # Verify connectivity before committing
    async with drv.session(database=_database) as session:
        await session.run("RETURN 1")
    _driver = drv
    logger.info("Neo4j connected uri=%s db=%s", uri, _database)
    return _driver


# ── Cypher validation helpers ────────────────────────────────────────────────

_WRITE_PATTERN = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|CALL\s+db\.)\b",
    re.IGNORECASE,
)

_DANGLING_WITH = re.compile(
    r"\bWITH\b(?!.*\b(RETURN|MATCH|WHERE|CREATE|MERGE|UNWIND|CALL|SET|DELETE|REMOVE|FOREACH|FINISH)\b)",
    re.IGNORECASE | re.DOTALL,
)

_BAD_QUOTE = re.compile(r"'[^']*[a-zA-Z]'[a-zA-Z][^']*'")
_UNBOUNDED_VAR_PATH = re.compile(r"\[[\s*]*\*[\s*]*\]")
_DEPRECATED_ID = re.compile(r"\bid\s*\(", re.IGNORECASE)

# Valid labels and relationship types populated by get_schema cache
_valid_labels: set[str] = set()
_valid_relationships: set[str] = set()
_valid_direction_triples: set[tuple[str, str, str]] = set()
# Map from label → set of valid property names on that label (populated with the
# schema cache). Used to offer "did you mean" hints when a query references a
# property name that doesn't exist (e.g. camelCase vs snake_case typos).
_valid_properties_per_label: dict[str, set[str]] = {}

# ── Regex patterns for Cypher direction extraction ────────────────────────────
# Matches (var:Label)-[var:TYPE]->(var:Label) in Cypher queries
_CYPHER_FORWARD_DIR = re.compile(
    r"\(\w*:(\w+)\)-\[\w*:(\w+)\]->\(\w*:(\w+)\)"
)
# Matches (var:Label)<-[var:TYPE]-(var:Label) in Cypher queries
_CYPHER_REVERSE_DIR = re.compile(
    r"\(\w*:(\w+)\)<-\[\w*:(\w+)\]-\(\w*:(\w+)\)"
)
# Matches relationships without explicit type: -[]-> or -[r]->
_UNTYPED_REL = re.compile(r"-\[\s*\w*\s*\]-?>")
# Extracts direction triples from schema pattern lines like (:Src)-[:REL]->(:Tgt)
_SCHEMA_DIRECTION_RE = re.compile(r"\(:\s*(\w+)\s*\)-\[\s*:(\w+)\s*\]->\(\s*:(\w+)\s*\)")

# ── Schema section headers (DRY constants) ──────────────────────────────────
_HDR_NODE_LABELS = "## Node Labels & Properties"
_HDR_REL_TYPES = "\n## Relationship Types & Properties"
_HDR_REL_DIRECTIONS = "\n## Relationship Direction Patterns"


_CYPHER_KEYWORDS = frozenset({
    "MATCH", "WHERE", "RETURN", "WITH", "ORDER", "LIMIT", "SKIP",
    "OPTIONAL", "UNWIND", "CASE", "WHEN", "THEN", "ELSE", "END",
    "AND", "OR", "NOT", "IN", "AS", "BY", "DESC", "ASC", "NULL",
    "TRUE", "FALSE", "DISTINCT", "COLLECT", "COUNT", "EXISTS",
    "STRING", "INTEGER", "FLOAT", "BOOLEAN",
})


def _check_cartesian_product(stripped: str) -> str | None:
    """Detect disconnected MATCH patterns that create a Cartesian product."""
    if not re.search(r"\bMATCH\b[^,]*\([^)]+\)\s*,\s*\([^)]+\)", stripped, re.IGNORECASE):
        return None
    match_section = stripped.split("RETURN")[0] if "RETURN" in stripped else stripped
    if re.search(r"-\[.*?\]-", match_section):
        return None
    return (
        "Warning: disconnected MATCH patterns create a Cartesian product. "
        "Connect the patterns with a relationship."
    )


def _check_label_validity(stripped: str) -> str | None:
    """Validate that labels used in the query exist in the schema.

    Bracket expressions like ``[r:REL_TYPE]`` are stripped first so that
    relationship types are not mistaken for node labels. Node labels only
    appear inside parentheses.
    """
    if not _valid_labels:
        return None
    # Remove relationship brackets so [r:REL_TYPE] isn't parsed as a label.
    label_scope = re.sub(r"\[[^\]]*\]", "", stripped)
    used_labels = set(re.findall(r"[:(]\s*:?(\w+)\s*[){]", label_scope))
    used_labels |= set(re.findall(r"\w+:(\w+)", label_scope))
    used_labels -= _CYPHER_KEYWORDS
    used_labels = {la for la in used_labels if la[0].isupper()}
    invalid = used_labels - _valid_labels
    if not invalid:
        return None
    return (
        f"Warning: label(s) {', '.join(sorted(invalid))} not found in schema. "
        f"Valid labels: {', '.join(sorted(_valid_labels))}."
    )


_REL_NAME_PREFIXES = ("HAS_", "IS_", "HAD_", "USES_", "USE_")
_REL_NAME_SUFFIXES = ("_BY", "_OF", "_FOR", "_TO", "_FROM")


def _strip_rel_affixes(name: str) -> str:
    """Strip one common verb prefix and one suffix from a relationship name."""
    for prefix in _REL_NAME_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    for suffix in _REL_NAME_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name


def _find_matching_properties(core_lower: str, core_flat: str) -> list[tuple[str, str]]:
    """Return (label, property) pairs whose property name matches the core."""
    matches: list[tuple[str, str]] = []
    for label, props in _valid_properties_per_label.items():
        for prop in props:
            p_lower = prop.lower()
            if p_lower == core_lower or p_lower.replace("_", "") == core_flat:
                matches.append((label, prop))
    return matches


def _format_property_hint(invalid_rel: str, matches: list[tuple[str, str]]) -> str:
    """Format a hint saying the invented relationship is actually a property."""
    by_prop: dict[str, list[str]] = {}
    for label, prop in matches:
        by_prop.setdefault(prop, []).append(label)
    msgs = [
        f"`{prop}` is a property on :{'/'.join(sorted(labels))} "
        f"(access as `n.{prop}`, NOT as a relationship)"
        for prop, labels in sorted(by_prop.items())
    ]
    return f"'{invalid_rel}' is not a relationship. " + "; ".join(msgs) + "."


def _format_label_hint(invalid_rel: str, label: str) -> str:
    """Format a hint saying the invented relationship name is actually a label."""
    return (
        f"'{invalid_rel}' is not a relationship. `{label}` is a node label "
        f"— there is no direct `{invalid_rel}` edge. Inspect the actual "
        f"relationships between the source node and :{label} first."
    )


def _relationship_name_hint(invalid_rel: str) -> str | None:
    """Return a corrective hint when the agent invents a relationship that is
    actually a property or a label.

    Agents often write ``[:HAS_CODE_REPOSITORY]`` when ``code_repository`` is
    a string property on the node, or ``[:HAS_SANITIZED_TABLE]`` when
    ``SanitizedTable`` is a neighbouring *label* rather than a relationship.
    We strip common prefixes/suffixes and look the remainder up in the schema.
    """
    core = _strip_rel_affixes(invalid_rel)
    if not core:
        return None
    core_lower = core.lower()
    core_flat = core_lower.replace("_", "")

    property_matches = _find_matching_properties(core_lower, core_flat)
    if property_matches:
        return _format_property_hint(invalid_rel, property_matches)

    label_candidates = {la for la in _valid_labels if la.lower() == core_flat}
    if label_candidates:
        return _format_label_hint(invalid_rel, next(iter(label_candidates)))

    return None


def _check_relationship_validity(stripped: str) -> str | None:
    """Validate that relationship types in the query exist in the schema.

    On failure, attempts to give a corrective hint when the invented relationship
    name actually corresponds to a property or a neighbouring label.
    """
    if not _valid_relationships:
        return None
    used_rels = set(re.findall(r"\[:(\w+)", stripped))
    if not used_rels:
        return None
    invalid = used_rels - _valid_relationships
    if not invalid:
        return None

    hints = [h for h in (_relationship_name_hint(r) for r in sorted(invalid)) if h]
    valid_list = ", ".join(sorted(_valid_relationships)[:20])
    base = (
        f"Warning: relationship type(s) {', '.join(sorted(invalid))} "
        f"not found in schema. Valid types include: {valid_list}."
    )
    if hints:
        return base + " | " + " | ".join(hints)
    return base


def _check_relationship_directions(stripped: str) -> str | None:
    """Validate relationship directions against schema direction patterns."""
    if not _valid_direction_triples:
        return None

    issues: list[str] = []

    # Check forward arrows: (a:Source)-[:REL]->(b:Target)
    for m in _CYPHER_FORWARD_DIR.finditer(stripped):
        src, rel, tgt = m.group(1), m.group(2), m.group(3)
        if (src, rel, tgt) in _valid_direction_triples:
            continue
        if (tgt, rel, src) in _valid_direction_triples:
            issues.append(
                f"(:{src})-[:{rel}]->(:{tgt}) — direction is reversed. "
                f"Schema shows (:{tgt})-[:{rel}]->(:{src})"
            )

    # Check reverse arrows: (a:Source)<-[:REL]-(b:Target) means (Target)-[:REL]->(Source)
    for m in _CYPHER_REVERSE_DIR.finditer(stripped):
        src, rel, tgt = m.group(1), m.group(2), m.group(3)
        actual_src, actual_tgt = tgt, src
        if (actual_src, rel, actual_tgt) in _valid_direction_triples:
            continue
        if (actual_tgt, rel, actual_src) in _valid_direction_triples:
            issues.append(
                f"(:{src})<-[:{rel}]-(:{tgt}) — direction is reversed. "
                f"Schema shows (:{src})-[:{rel}]->(:{tgt})"
            )

    if not issues:
        return None
    return "Warning: wrong relationship direction. " + " | ".join(issues)


def _is_introspection_query(stripped: str) -> bool:
    """Return True if the query is discovering graph structure, not traversing it.

    Introspection queries legitimately use untyped relationships because the
    whole point is to *find out* which relationship types exist on a node.
    Signals: the RETURN clause asks for relationship/label metadata
    (``type(r)``, ``labels(m)``, ``keys(r)``, ``properties(r)``).
    """
    return bool(re.search(
        r"\b(?:type|labels|keys|properties)\s*\(\s*\w+\s*\)",
        stripped,
        re.IGNORECASE,
    ))


def _check_untyped_relationships(stripped: str) -> str | None:
    """Warn when relationships lack explicit type specifiers.

    Skipped for introspection queries — those intentionally use ``[r]`` to
    discover which relationship types exist, and blocking them would prevent
    the agent from self-recovering after a wrong-relationship guess.
    """
    if _is_introspection_query(stripped):
        return None
    untyped = [
        m.group() for m in _UNTYPED_REL.finditer(stripped)
        if ":" not in m.group() and "*" not in m.group()
    ]
    if not untyped:
        return None
    return (
        "Warning: untyped relationship(s) found. Always specify the relationship type "
        "e.g. -[:REL_TYPE]-> to avoid matching unintended relationships."
    )


def _validate_cypher(query: str) -> str | None:
    """Return an error string if the query has a detectable syntax issue, else None."""
    stripped = query.strip().rstrip(";")

    if not re.search(r"\bRETURN\b", stripped, re.IGNORECASE):
        if _DANGLING_WITH.search(stripped):
            return (
                "Syntax error: query ends with WITH but has no RETURN clause. "
                "Add a RETURN clause after the WITH."
            )
        return "Syntax error: query has no RETURN clause. Read-only queries must end with RETURN."

    if _BAD_QUOTE.search(stripped):
        return (
            "Syntax error: unescaped single quote inside a string literal. "
            "Use query parameters instead."
        )

    if _UNBOUNDED_VAR_PATH.search(stripped):
        return (
            "Warning: unbounded variable-length path [*] can explore the entire graph. "
            "Always set an upper bound, e.g. [*..5]."
        )

    if _DEPRECATED_ID.search(stripped) and not re.search(r"\belementId\s*\(", stripped):
        return "Warning: id() is deprecated in Neo4j 5+. Use elementId() instead."

    return (
        _check_cartesian_product(stripped)
        or _check_label_validity(stripped)
        or _check_relationship_validity(stripped)
        or _check_relationship_directions(stripped)
        or _check_untyped_relationships(stripped)
    )


def _validate_label(label: str | None) -> str | None:
    """Return an error string if the label doesn't exist in the schema, else None."""
    if not label:
        return None
    if not _valid_labels:
        return None
    if label in _valid_labels:
        return None
    return (
        f"Error: Label '{label}' does not exist in the database. "
        f"Valid labels: {', '.join(sorted(_valid_labels))}."
    )


# ── Neo4j type serialization ────────────────────────────────────────────────

def _serialize_neo4j(obj: Any) -> Any:
    """Serialize Neo4j-specific types to JSON-safe values."""
    if hasattr(obj, "items"):
        return dict(obj)
    if hasattr(obj, "nodes"):
        return {
            "nodes": [_serialize_neo4j(n) for n in obj.nodes],
            "relationships": [_serialize_neo4j(r) for r in obj.relationships],
        }
    if isinstance(obj, list):
        return [_serialize_neo4j(i) for i in obj]
    return obj


# ── Query introspection (0-row hints) ────────────────────────────────────────
#
# When a Cypher query returns 0 rows, we run a cheap follow-up query against
# the named entities referenced in the original query and return a factual
# summary of their real neighborhood + property names. This replaces the
# agent's "guess again" loop with ground-truth data from the graph, cutting
# down on wrong-relationship / wrong-direction / wrong-property hallucinations.
#
# Fully self-contained in this MCP server — the orchestrator never sees this.

# Matches (var:Label {prop: 'value'}) or (var:Label {prop: $paramName})
# Captures: (1) label, (2) prop, (3) literal value, (4) $param name
_NAMED_ENTITY_RE = re.compile(
    r"\(\s*\w*\s*:\s*(\w+)\s*\{\s*(\w+)\s*:\s*(?:'([^']*)'|\$(\w+))\s*\}"
)
# Matches var.propName references inside the query
_PROP_ACCESS_RE = re.compile(r"\b(\w+)\.(\w+)\b")
# Matches (var:Label) binding so we know which var → which label
_VAR_LABEL_RE = re.compile(r"\(\s*(\w+)\s*:\s*(\w+)")


def _extract_named_entities(
    query: str, params: dict | None,
) -> list[tuple[str, str, str]]:
    """Pull (label, prop, value) triples out of the query for introspection.

    Resolves $paramName against *params* when possible. Returns at most 3
    entities to keep the follow-up introspection cheap.
    """
    out: list[tuple[str, str, str]] = []
    for m in _NAMED_ENTITY_RE.finditer(query):
        label, prop = m.group(1), m.group(2)
        literal = m.group(3)
        param_name = m.group(4)
        if literal is not None:
            value = literal
        elif param_name and params and param_name in params:
            value = str(params[param_name])
        else:
            continue
        out.append((label, prop, value))
        if len(out) >= 3:
            break
    return out


def _extract_var_label_map(query: str) -> dict[str, str]:
    """Build a {var: Label} map from (var:Label) bindings in the query."""
    return {m.group(1): m.group(2) for m in _VAR_LABEL_RE.finditer(query)}


def _find_property_typos(query: str) -> list[str]:
    """Return hint lines for property names that don't exist on their label.

    Looks for ``var.prop`` references where ``var`` is bound to a known label
    and ``prop`` doesn't exist on that label. Suggests close variants
    (case-insensitive or snake_case/camelCase flips) from the schema.
    """
    if not _valid_properties_per_label:
        return []
    var_to_label = _extract_var_label_map(query)
    if not var_to_label:
        return []

    hints: list[str] = []
    seen: set[tuple[str, str]] = set()
    for m in _PROP_ACCESS_RE.finditer(query):
        var, prop = m.group(1), m.group(2)
        label = var_to_label.get(var)
        if not label:
            continue
        valid = _valid_properties_per_label.get(label)
        if not valid or prop in valid:
            continue
        key = (label, prop)
        if key in seen:
            continue
        seen.add(key)
        prop_lower = prop.lower()
        candidates = [
            p for p in valid
            if p.lower() == prop_lower
            or p.replace("_", "").lower() == prop_lower.replace("_", "")
        ]
        if candidates:
            hints.append(
                f"Property '{prop}' not found on :{label}. "
                f"Did you mean: {', '.join(sorted(candidates))}? "
                f"(Neo4j property names are case-sensitive.)"
            )
        else:
            hints.append(
                f"Property '{prop}' not found on :{label}. "
                f"Available properties: {', '.join(sorted(valid))}."
            )
    return hints


async def _introspect_entity(
    session: Any, label: str, prop: str, value: Any,
) -> list[str]:
    """Run a neighborhood scan for a single named entity and format lines.

    Returns a list of human-readable lines describing the entity's real
    outgoing and incoming edges, or an empty list on failure.
    """
    try:
        cypher = (
            f"MATCH (n:`{label}` {{`{prop}`: $value}})-[r]-(m) "
            "RETURN "
            "  CASE WHEN startNode(r) = n THEN 'out' ELSE 'in' END AS direction, "
            "  type(r) AS rel_type, "
            "  labels(m) AS neighbor_labels, "
            "  count(*) AS n "
            "ORDER BY n DESC LIMIT 20"
        )
        result = await session.run(cypher, {"value": value})
        rows = await result.data()
    except Exception as exc:
        logger.debug("Introspection failed for (:%s {%s: %r}): %s", label, prop, value, exc)
        return []

    if not rows:
        return [f"(:{label} {{{prop}: {value!r}}}) — no such node, or node has no edges."]

    lines = [f"Real edges of (:{label} {{{prop}: {value!r}}}):"]
    for row in rows:
        direction = row.get("direction", "?")
        rel_type = row.get("rel_type", "?")
        neighbor_labels = row.get("neighbor_labels") or []
        neighbor = neighbor_labels[0] if neighbor_labels else "?"
        n = row.get("n", 0)
        if direction == "out":
            lines.append(f"  (:{label})-[:{rel_type}]->(:{neighbor})  x{n}")
        else:
            lines.append(f"  (:{neighbor})-[:{rel_type}]->(:{label})  x{n}")
    return lines


async def _build_zero_result_hint(
    session: Any, query: str, params: dict | None,
) -> str:
    """Build a diagnostic hint to append to a 0-row query response.

    Combines property-name typo detection (local, no DB call) with per-entity
    neighborhood introspection (one small query per referenced entity).
    """
    hint_lines: list[str] = []

    prop_hints = _find_property_typos(query)
    hint_lines.extend(prop_hints)

    entities = _extract_named_entities(query, params)
    for label, prop, value in entities:
        lines = await _introspect_entity(session, label, prop, value)
        if lines:
            hint_lines.extend(lines)

    if not hint_lines:
        return ""
    return "\n\nHint - ground truth from the graph:\n" + "\n".join(hint_lines)


# ── Query runner with retry ──────────────────────────────────────────────────

async def _run_query(query: str, params: dict | None = None) -> str:
    """Execute a read-only Cypher query with retry and return JSON results."""
    driver = await _get_driver()
    max_attempts = 5
    base_delay = 1.0

    for attempt in range(1, max_attempts + 1):
        try:
            async with driver.session(database=_database) as session:
                t0 = time.perf_counter()
                result = await session.run(query, params or {})
                records = [
                    {k: _serialize_neo4j(v) for k, v in rec.items()}
                    for rec in await result.data()
                ]
                elapsed = (time.perf_counter() - t0) * 1000
                logger.debug("Query %.0fms rows=%d: %s", elapsed, len(records), query[:80])

                if not records:
                    # Auto-introspection: feed the agent ground-truth about the
                    # entities in its query so it can self-correct instead of
                    # guessing (or hallucinating).
                    hint = await _build_zero_result_hint(session, query, params)
                    return f"Query returned 0 results.\nCypher: {query}{hint}"
                return json.dumps(records, indent=2, default=str)

        except Exception as exc:
            if attempt >= max_attempts:
                return f"Error executing query: {exc}"
            delay = min(base_delay * (2 ** (attempt - 1)), 10.0)
            logger.warning("neo4j retry #%d/%d after %.1fs: %s", attempt, max_attempts, delay, exc)
            await asyncio.sleep(delay)

    return "Error: max retries exceeded"


# ── Schema cache ─────────────────────────────────────────────────────────────

_schema_cache: str | None = None
_schema_cache_time: float = 0.0
_SCHEMA_TTL: int = 86400  # 24 hours


async def _fetch_schema_via_apoc(session: Any) -> dict[str, Any] | None:
    """Try apoc.meta.schema() — returns full schema metadata without scanning every node."""
    try:
        result = await session.run("CALL apoc.meta.schema() YIELD value RETURN value")
        record = await result.single()
        return record["value"] if record else None
    except Exception as exc:
        logger.debug("apoc.meta.schema() unavailable: %s", exc)
        return None


def _format_properties(label: str, props: dict[str, Any]) -> list[str]:
    """Format property metadata into schema lines like '  :Label.prop: TYPE'."""
    return [
        f"  :{label}.{name}: {meta.get('type', 'STRING')}"
        for name, meta in sorted(props.items())
    ]


def _format_outgoing_directions(label: str, relationships: dict[str, Any]) -> list[str]:
    """Format outgoing relationship directions from a node label."""
    lines: list[str] = []
    for rel_type, rel_meta in sorted(relationships.items()):
        if rel_meta.get("direction", "out") != "out":
            continue
        for tgt_label in sorted(rel_meta.get("labels", {})):
            lines.append(f"  (:{label})-[:{rel_type}]->(:{tgt_label})")
    return lines


def _parse_apoc_node(label: str, meta: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Parse a single APOC node entry into property lines and direction lines."""
    prop_lines = _format_properties(label, meta.get("properties", {}))
    dir_lines = _format_outgoing_directions(label, meta.get("relationships", {}))
    return prop_lines, dir_lines


def _parse_apoc_relationship(label: str, meta: dict[str, Any]) -> list[str]:
    """Parse a single APOC relationship entry into property lines."""
    props = meta.get("properties", {})
    if not props:
        return [f"  :{label} (no properties)"]
    return _format_properties(label, props)


def _parse_apoc_schema(schema: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """Parse apoc.meta.schema() output into node labels, rel types, and direction patterns."""
    node_lines = [_HDR_NODE_LABELS]
    rel_type_lines = [_HDR_REL_TYPES]
    direction_lines = [_HDR_REL_DIRECTIONS]

    for label, meta in sorted(schema.items()):
        entry_type = meta.get("type", "")
        if entry_type == "node":
            props, dirs = _parse_apoc_node(label, meta)
            node_lines.extend(props)
            direction_lines.extend(dirs)
        elif entry_type == "relationship":
            rel_type_lines.extend(_parse_apoc_relationship(label, meta))

    return node_lines, rel_type_lines, direction_lines


async def _fetch_node_labels(session: Any) -> list[str]:
    """Fetch node label and property schema lines (fallback when APOC unavailable)."""
    try:
        result = await session.run(
            "CALL db.schema.nodeTypeProperties() "
            "YIELD nodeLabels, propertyName, propertyTypes "
            "UNWIND nodeLabels AS label "
            "RETURN DISTINCT label, propertyName, propertyTypes[0] AS propType "
            "ORDER BY label, propertyName"
        )
        records = await result.data()
        if records:
            return [_HDR_NODE_LABELS] + [
                f"  :{r['label']}.{r['propertyName']}: {r['propType']}" for r in records
            ]
    except Exception:
        logger.debug("db.schema.nodeTypeProperties() failed, trying full scan")
    try:
        result = await session.run(
            "MATCH (n) UNWIND labels(n) AS label "
            "WITH DISTINCT label "
            "MATCH (n) WHERE label IN labels(n) "
            "WITH label, keys(n) AS props "
            "UNWIND props AS prop "
            "RETURN DISTINCT label, prop ORDER BY label, prop"
        )
        records = await result.data()
        return [_HDR_NODE_LABELS] + [
            f"  :{r['label']}.{r['prop']}" for r in records
        ]
    except Exception:
        result = await session.run("CALL db.labels()")
        labels = [r["label"] for r in await result.data()]
        return [f"## Labels: {', '.join(labels)}"]


async def _fetch_relationship_types(session: Any) -> list[str]:
    """Fetch relationship type and property schema lines (fallback when APOC unavailable)."""
    try:
        result = await session.run(
            "CALL db.schema.relTypeProperties() "
            "YIELD relType, propertyName, propertyTypes "
            "WITH replace(relType, ':`', '') AS rt, propertyName, propertyTypes "
            "WITH replace(rt, '`', '') AS relType, propertyName, propertyTypes "
            "RETURN DISTINCT relType, propertyName, "
            "CASE WHEN propertyTypes IS NOT NULL AND size(propertyTypes) > 0 "
            "  THEN propertyTypes[0] ELSE null END AS propType "
            "ORDER BY relType, propertyName"
        )
        records = await result.data()
        if records:
            lines = [_HDR_REL_TYPES]
            for r in records:
                if r["propertyName"]:
                    lines.append(f"  :{r['relType']}.{r['propertyName']}: {r.get('propType', '')}")
                else:
                    lines.append(f"  :{r['relType']} (no properties)")
            return lines
    except Exception:
        logger.debug("db.schema.relTypeProperties() failed, trying full scan")
    try:
        result = await session.run(
            "MATCH ()-[r]->() "
            "WITH DISTINCT type(r) AS relType "
            "MATCH ()-[r]->() WHERE type(r) = relType "
            "WITH relType, keys(r) AS props "
            "UNWIND CASE WHEN size(props) = 0 THEN [null] ELSE props END AS prop "
            "RETURN DISTINCT relType, prop ORDER BY relType, prop"
        )
        records = await result.data()
        lines = [_HDR_REL_TYPES]
        for r in records:
            if r["prop"]:
                lines.append(f"  :{r['relType']}.{r['prop']}")
            else:
                lines.append(f"  :{r['relType']} (no properties)")
        return lines
    except Exception:
        result = await session.run("CALL db.relationshipTypes()")
        types = [r["relationshipType"] for r in await result.data()]
        return [f"\n## Relationship Types: {', '.join(types)}"]


def _node_element_id(node: Any) -> Any:
    """Get element_id from a Neo4j node, falling back to .id for older drivers."""
    return node.element_id if hasattr(node, "element_id") else node.id


def _build_nodes_map(nodes: list[Any]) -> dict[Any, list[str]]:
    """Build a mapping of element_id -> list of labels from schema visualization nodes."""
    nodes_map: dict[Any, list[str]] = {}
    for node in nodes:
        eid = _node_element_id(node)
        nodes_map.setdefault(eid, []).extend(node.labels)
    return nodes_map


def _build_direction_patterns(record: Any) -> set[str]:
    """Build direction pattern strings from a schema visualization record."""
    nodes_map = _build_nodes_map(record["nodes"])
    patterns: set[str] = set()
    for rel in record["relationships"]:
        start_id = _node_element_id(rel.start_node)
        end_id = _node_element_id(rel.end_node)
        for src_label in nodes_map.get(start_id, ["Unknown"]):
            for tgt_label in nodes_map.get(end_id, ["Unknown"]):
                patterns.add(f"  (:{src_label})-[:{rel.type}]->(:{tgt_label})")
    return patterns


async def _fetch_directions_via_visualization(session: Any) -> list[str] | None:
    """Try db.schema.visualization() for direction patterns. Returns None on failure."""
    try:
        result = await session.run("CALL db.schema.visualization()")
        record = await result.single()
        if not record:
            return None
        patterns = _build_direction_patterns(record)
        if patterns:
            return [_HDR_REL_DIRECTIONS] + sorted(patterns)
    except Exception as exc:
        logger.debug("db.schema.visualization() failed: %s", exc)
    return None


async def _fetch_directions_via_scan(session: Any) -> list[str] | None:
    """Fallback: sampled scan for direction patterns. Returns None on failure."""
    try:
        result = await session.run(
            "MATCH (a)-[r]->(b) "
            "UNWIND labels(a) AS src "
            "UNWIND labels(b) AS tgt "
            "WITH DISTINCT src, type(r) AS rel, tgt "
            "RETURN src, rel, tgt ORDER BY src, rel, tgt"
        )
        records = await result.data()
        if records:
            return [_HDR_REL_DIRECTIONS] + [
                f"  (:{r['src']})-[:{r['rel']}]->(:{r['tgt']})" for r in records
            ]
    except Exception as exc:
        logger.warning("Relationship direction fetch failed: %s", exc)
    return None


async def _fetch_relationship_directions(session: Any) -> list[str]:
    """Fetch (Source)-[TYPE]->(Target) direction patterns (fallback when APOC unavailable).

    Uses db.schema.visualization() first (instant metadata lookup), then falls back
    to a sampled query if unavailable.
    """
    return (
        await _fetch_directions_via_visualization(session)
        or await _fetch_directions_via_scan(session)
        or []
    )


async def _get_schema() -> str:
    """Return database schema (cached with TTL).

    Strategy:
      1. apoc.meta.schema() — instant metadata, no full scan, all directions
      2. db.schema.* procedures — lightweight per-section fallback
      3. Full-scan Cypher — last resort
    """
    global _schema_cache, _schema_cache_time, _valid_labels, _valid_relationships, _valid_direction_triples, _valid_properties_per_label

    now = time.time()
    if _schema_cache and (now - _schema_cache_time) < _SCHEMA_TTL:
        return _schema_cache

    driver = await _get_driver()
    parts: list[str] = []

    async with driver.session(database=_database) as session:
        count_result = await session.run("MATCH (n) RETURN count(n) AS c")
        count_record = await count_result.single()
        node_count = count_record["c"] if count_record else 0

        if node_count == 0:
            parts.append("## Database is empty — no nodes or relationships exist.")
        else:
            # Try APOC first — single call returns everything
            apoc = await _fetch_schema_via_apoc(session)
            if apoc:
                node_lines, rel_lines, dir_lines = _parse_apoc_schema(apoc)
                parts.extend(node_lines)
                parts.extend(rel_lines)
                parts.extend(dir_lines)
                logger.info(
                    "Schema via apoc.meta.schema(): %d node props, %d rel props, %d direction patterns",
                    len(node_lines) - 1, len(rel_lines) - 1, len(dir_lines) - 1,
                )
            else:
                # Fallback to individual procedures / scans
                parts.extend(await _fetch_node_labels(session))
                parts.extend(await _fetch_relationship_types(session))
                parts.extend(await _fetch_relationship_directions(session))

    _schema_cache = "\n".join(parts)
    _schema_cache_time = now
    _valid_labels = {
        m.group(1) for m in re.finditer(r"^\s+:(\w+)\.", _schema_cache, re.MULTILINE)
    }
    _valid_relationships = {
        m.group(1)
        for m in re.finditer(r"\[:(\w+)\]", _schema_cache)
    }
    _valid_direction_triples = {
        (m.group(1), m.group(2), m.group(3))
        for m in _SCHEMA_DIRECTION_RE.finditer(_schema_cache)
    }
    # Parse "  :Label.prop: TYPE" lines into a label→properties map so we can
    # offer "did you mean" hints when the agent queries a non-existent property.
    _valid_properties_per_label = {}
    for m in re.finditer(r"^\s+:(\w+)\.(\w+):", _schema_cache, re.MULTILINE):
        _valid_properties_per_label.setdefault(m.group(1), set()).add(m.group(2))
    logger.info(
        "Schema cached: %d chars, %d valid labels, %d valid relationship types, "
        "%d direction triples, %d labels with property metadata",
        len(_schema_cache), len(_valid_labels), len(_valid_relationships),
        len(_valid_direction_triples), len(_valid_properties_per_label),
    )
    return _schema_cache


# ── Search helpers ───────────────────────────────────────────────────────────

_FULLTEXT_INDEX = "universalDiscoveryIndex"
_KEY_PROPERTIES = {"name", "title", "displayname", "label", "fullname"}
_LEVEL_BASE_SCORES = {1: 100, 2: 80, 3: 60, 4: 40, 5: 20}

_GENERIC_SUFFIXES = frozenset({
    "application", "applications", "app", "apps",
    "system", "systems", "service", "services",
    "project", "projects", "platform", "platforms",
    "tool", "tools", "server", "servers",
    "database", "db", "environment", "env",
    "module", "component", "instance",
    "solution", "solutions", "product", "products",
    "resource", "resources", "item", "items",
    "type", "types", "list", "details", "info",
})


def _extract_entity_name(search_term: str) -> str | None:
    """Strip generic suffixes to get the core entity name."""
    tokens = search_term.strip().split()
    core = [t for t in tokens if t.lower() not in _GENERIC_SUFFIXES]
    if not core or len(core) == len(tokens):
        return None
    return " ".join(core)


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"\b\w+\b", text.lower()) if t]


def _score(result: dict, level: int) -> float:
    base = _LEVEL_BASE_SCORES.get(level, 10)
    matched = result.get("matched_tokens", [])
    bonus = len(matched) * 10
    multiplier = 1.0
    props = result.get("properties", {})
    for pname, pval in props.items():
        if pname.lower() in _KEY_PROPERTIES:
            val = str(pval).lower()
            if any(t in val for t in matched):
                multiplier = 2.0
                break
    return (base + bonus) * multiplier


async def _query_raw(cypher: str, params: dict) -> list[dict]:
    """Execute Cypher and return raw record dicts."""
    driver = await _get_driver()
    async with driver.session(database=_database) as session:
        result = await session.run(cypher, params)
        return [dict(rec) for rec in await result.data()]


def _row(rec: dict, level: int, tokens: list[str]) -> dict:
    return {
        "elementId": rec["id"],
        "labels": rec["labels"],
        "properties": rec["props"],
        "level": level,
        "matched_tokens": tokens,
    }


async def _run_progressive_level(
    level: int, full_query: str, tokens: list[str],
    label: str | None, limit: int, min_match: int = 1,
) -> list[dict]:
    """Run one level of progressive search."""
    label_clause = f"MATCH (n:`{label}`)" if label else "MATCH (n)"
    results: list[dict] = []
    try:
        if level == 1:
            q = (
                f"{label_clause} "
                "WHERE any(k IN keys(n) WHERE "
                "  n[k] IS :: STRING AND "
                "  toLower(n[k]) CONTAINS toLower($full_query)) "
                "RETURN DISTINCT elementId(n) AS id, labels(n) AS labels, "
                "properties(n) AS props LIMIT $limit"
            )
            records = await _query_raw(q, {"full_query": full_query, "limit": limit})
            for r in records:
                results.append(_row(r, level, tokens))
        elif level == 2:
            q = (
                f"{label_clause} "
                "WHERE all(token IN $tokens WHERE "
                "  any(k IN keys(n) WHERE "
                "    n[k] IS :: STRING AND "
                "    toLower(n[k]) CONTAINS toLower(token))) "
                "RETURN DISTINCT elementId(n) AS id, labels(n) AS labels, "
                "properties(n) AS props LIMIT $limit"
            )
            records = await _query_raw(q, {"tokens": tokens, "limit": limit})
            for r in records:
                results.append(_row(r, level, tokens))
        else:
            q = (
                f"{label_clause} "
                "WHERE any(token IN $tokens WHERE "
                "  any(k IN keys(n) WHERE "
                "    n[k] IS :: STRING AND "
                "    toLower(n[k]) CONTAINS toLower(token))) "
                "WITH DISTINCT n, "
                "  [t IN $tokens WHERE "
                "    any(k IN keys(n) WHERE "
                "      n[k] IS :: STRING AND "
                "      toLower(n[k]) CONTAINS toLower(t))] AS matched_tokens "
                "WHERE size(matched_tokens) >= $min_match "
                "RETURN elementId(n) AS id, labels(n) AS labels, "
                "properties(n) AS props, matched_tokens "
                "ORDER BY size(matched_tokens) DESC LIMIT $limit"
            )
            records = await _query_raw(
                q, {"tokens": tokens, "min_match": min_match, "limit": limit},
            )
            for r in records:
                results.append({
                    "elementId": r["id"],
                    "labels": r["labels"],
                    "properties": r["props"],
                    "level": level,
                    "matched_tokens": r["matched_tokens"],
                })
    except Exception as exc:
        logger.warning("Progressive search level %d failed: %s", level, exc)
    return results


async def _single_token_search(token: str, label: str | None, limit: int) -> str | None:
    """Progressive search optimized for a single token."""
    label_clause = f"MATCH (n:`{label}`)" if label else "MATCH (n)"
    q = (
        f"{label_clause} "
        "WHERE any(k IN keys(n) WHERE "
        "  n[k] IS :: STRING AND "
        "  toLower(n[k]) CONTAINS toLower($term)) "
        "RETURN DISTINCT elementId(n) AS id, labels(n) AS labels, "
        "properties(n) AS props LIMIT $limit"
    )
    records = await _query_raw(q, {"term": token, "limit": limit})
    if not records:
        return None
    results = [{**_row(r, 1, [token]), "score": 100.0} for r in records]
    return json.dumps(results[:limit], indent=2, default=str)


def _dedup_and_score(level_results: list[Any], limit: int) -> str | None:
    """Deduplicate, score, and serialize progressive search results."""
    all_results: list[dict] = []
    seen_ids: set[str] = set()
    for hits in level_results:
        if isinstance(hits, BaseException):
            continue
        for h in hits:
            if h["elementId"] not in seen_ids:
                seen_ids.add(h["elementId"])
                all_results.append(h)

    for r in all_results:
        r["score"] = _score(r, r["level"])
    all_results.sort(key=lambda x: x["score"], reverse=True)

    final = all_results[:limit]
    if not final:
        return None
    return json.dumps(final, indent=2, default=str)


async def _progressive_search(search_term: str, label: str | None, limit: int) -> str | None:
    """Run 5-level progressive multi-word search."""
    tokens = _tokenize(search_term)
    if not tokens:
        return None

    if len(tokens) == 1:
        return await _single_token_search(tokens[0], label, limit)

    num_tokens = len(tokens)
    levels = [(1, num_tokens), (2, num_tokens)]
    if num_tokens >= 3:
        levels.append((3, max(num_tokens - 1, 2)))
        levels.append((4, 2))
    levels.append((5, 1))

    level_results = await asyncio.gather(
        *(_run_progressive_level(lvl, search_term, tokens, label, limit, mm)
          for lvl, mm in levels),
        return_exceptions=True,
    )

    return _dedup_and_score(level_results, limit)


def _has_results(result: str | None) -> bool:
    if not result:
        return False
    return (
        "0 results" not in result
        and "No results found" not in result
        and "No valid" not in result
        and "Error" not in result
    )


async def _try_fulltext(term: str, limit: int) -> str | None:
    try:
        cypher = (
            "CALL db.index.fulltext.queryNodes($index, $query) YIELD node, score "
            "RETURN elementId(node) AS elementId, labels(node) AS labels, "
            "properties(node) AS props, score "
            "ORDER BY score DESC LIMIT $limit"
        )
        result = await _run_query(cypher, {"index": _FULLTEXT_INDEX, "query": term, "limit": limit})
        if _has_results(result):
            return result
    except Exception:
        pass
    return None


async def _try_contains(term: str, label: str | None, limit: int) -> str | None:
    try:
        label_clause = f":{label}" if label else ""
        query = (
            f"MATCH (n{label_clause}) "
            "WITH n, [k IN keys(n) WHERE toLower(toString(n[k])) CONTAINS toLower($term)] AS matched "
            "WHERE size(matched) > 0 "
            "RETURN elementId(n) AS elementId, labels(n) AS labels, "
            "properties(n) AS props, matched AS matched_properties LIMIT $limit"
        )
        result = await _run_query(query, {"term": term, "limit": limit})
        if _has_results(result):
            return result
    except Exception:
        pass
    return None


async def _try_fuzzy(term: str, label: str | None, limit: int) -> str | None:
    try:
        label_clause = f":{label}" if label else ""
        query = (
            f"MATCH (n{label_clause}) "
            "WITH n, [k IN keys(n) WHERE apoc.text.levenshteinDistance("
            "toLower(toString(n[k])), toLower($term)) <= $threshold] AS fuzzy_matched "
            "WHERE size(fuzzy_matched) > 0 "
            "RETURN elementId(n) AS elementId, labels(n) AS labels, "
            "properties(n) AS props, fuzzy_matched LIMIT $limit"
        )
        result = await _run_query(query, {"term": term, "threshold": 3, "limit": limit})
        if _has_results(result):
            return result
    except Exception:
        pass
    return None


# ── FastMCP server ───────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Warm schema cache and Neo4j driver within mcp.run()'s event loop."""
    try:
        await _get_driver()
        await _get_schema()
        logger.info("Neo4j lifespan: schema warmed (%d labels)", len(_valid_labels))
    except Exception as e:
        logger.warning("Neo4j lifespan: schema warm-up failed: %s", e)
    yield {}
    # Shutdown: close driver
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None
        logger.info("Neo4j driver closed")


mcp = FastMCP(
    "neo4j",
    instructions=(
        "Neo4j graph database tools: Cypher queries, schema exploration, "
        "node/relationship search and traversal."
    ),
    lifespan=_lifespan,
)


@mcp.tool()
async def run_cypher(
    query: Annotated[str, Field(description=(
        "Cypher query (read-only, must have RETURN clause). "
        "Use $param placeholders for values, not inline strings."
    ))],
    params: Annotated[dict[str, Any] | None, Field(description=(
        "Query parameters — use this for ALL string/numeric values. "
        'Example: {"name": "Tom Hanks", "limit": 5}'
    ))] = None,
) -> str:
    """Execute a read-only Cypher query. IMPORTANT: Always use $params for \
string values (never inline quotes). Query MUST contain a RETURN clause."""
    if _WRITE_PATTERN.search(query):
        return "Error: Write operations are not allowed. Only read-only queries permitted."
    validation_err = _validate_cypher(query)
    if validation_err:
        return f"Error: {validation_err}"
    return await _run_query(query, params)


@mcp.tool()
async def get_schema() -> str:
    """Get the database schema: labels, relationship types, and properties."""
    return await _get_schema()


@mcp.tool()
async def get_relationship_patterns() -> str:
    """Get all (SourceLabel)-[TYPE]->(TargetLabel) patterns with directions. \
Use BEFORE writing Cypher to verify correct relationship directions. \
Direction matters: (Person)-[:ACTED_IN]->(Movie) is NOT the same as \
(Movie)-[:ACTED_IN]->(Person)."""
    query = (
        "MATCH (a)-[r]->(b) "
        "UNWIND labels(a) AS from_label "
        "UNWIND labels(b) AS to_label "
        "WITH DISTINCT from_label, type(r) AS rel_type, to_label "
        "RETURN from_label, rel_type, to_label "
        "ORDER BY from_label, rel_type, to_label"
    )
    return await _run_query(query)


@mcp.tool()
async def search(
    search_term: Annotated[str, Field(description="Text to search for")],
    label: Annotated[str | None, Field(description="Node label to filter (optional)")] = None,
    limit: Annotated[int, Field(description="Max results", default=25)] = 25,
) -> str:
    """Search for nodes by name, keyword, or phrase. Automatically picks the \
best search strategy (fulltext index, substring match, fuzzy/typo-tolerant, \
multi-word progressive). Returns scored results with match metadata. \
This is the ONLY search tool — do not look for alternatives."""
    if err := _validate_label(label):
        return err

    variants = [search_term]
    core = _extract_entity_name(search_term)
    if core:
        variants.append(core)

    # For multi-word queries, also try individual significant words
    tokens = _tokenize(search_term)
    if len(tokens) >= 2:
        for token in tokens:
            if token not in _GENERIC_SUFFIXES and len(token) >= 3 and token not in variants:
                variants.append(token)

    # Variant-first ordering: try shorter/core terms before full phrase.
    # Within each variant, contains is cheapest and most general — try first.
    # Fulltext requires an index and progressive is expensive, so try last.
    strategies = [
        ("contains", lambda v: _try_contains(v, label, limit)),
        ("fulltext", lambda v: _try_fulltext(v, limit)),
        ("fuzzy", lambda v: _try_fuzzy(v, label, limit)),
        ("progressive", lambda v: _progressive_search(v, label, limit)),
    ]

    # Try core/short variants first (more likely to match), then full phrase
    ordered_variants = sorted(set(variants), key=lambda v: len(v))

    for variant in ordered_variants:
        for strategy_name, strategy_fn in strategies:
            result = await strategy_fn(variant)
            if _has_results(result):
                logger.info("[SEARCH] Found results via %s for '%s'", strategy_name, variant)
                return result  # type: ignore[return-value]

    return f"No results found for: {search_term}"


@mcp.tool()
async def get_node_by_id(
    element_id: Annotated[str, Field(description="Neo4j elementId of the node")],
) -> str:
    """Get a node by its elementId, returning all properties and labels."""
    query = (
        "MATCH (n) WHERE elementId(n) = $eid "
        "RETURN elementId(n) AS elementId, labels(n) AS labels, properties(n) AS props"
    )
    return await _run_query(query, {"eid": element_id})


@mcp.tool()
async def get_neighbors(
    element_id: Annotated[str, Field(description="Neo4j elementId of center node")],
    limit: Annotated[int, Field(description="Max results", default=50)] = 50,
) -> str:
    """Get all nodes connected to a node — neighbors, relationships, directions."""
    query = (
        "MATCH (n)-[r]-(m) WHERE elementId(n) = $eid "
        "RETURN type(r) AS rel_type, "
        "CASE WHEN startNode(r) = n THEN 'outgoing' ELSE 'incoming' END AS direction, "
        "elementId(m) AS neighbor_id, labels(m) AS neighbor_labels, "
        "properties(m) AS neighbor_props LIMIT $limit"
    )
    return await _run_query(query, {"eid": element_id, "limit": limit})


@mcp.tool()
async def count_nodes(
    label: Annotated[str, Field(description="Node label (optional, counts all if empty)")] = "",
) -> str:
    """Count nodes, optionally filtered by label."""
    if err := _validate_label(label or None):
        return err
    label_clause = f":{label}" if label else ""
    query = f"MATCH (n{label_clause}) RETURN count(n) AS count"
    return await _run_query(query)


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Neo4j FastMCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "http"],
        default="sse",
        help="Transport type (default: sse)",
    )
    parser.add_argument("--port", type=int, default=3006, help="Port (default: 3006)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    args = parser.parse_args()

    # Schema warm-up now happens in _lifespan() within mcp.run()'s event loop
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
