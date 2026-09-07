"""
mcp_server.py - MCP server exposing the compliance tools, and the single
Policy Enforcement Point (PEP) for the system.

Speaks the real Model Context Protocol over stdio using the official `mcp`
SDK (pinned <2 for the stable low-level API). Every tool call arrives through
one handler, `call_tool`, and that handler is the choke point where
authorisation happens. Because enforcement lives with the resource - not in the
agent that wants the resource - a misbehaving, buggy, or compromised client
cannot bypass it. That is the reason to front local functions with a protocol
server at all in a governance context: it gives you one inspectable boundary to
police.

Design notes worth defending
-----------------------------
* Default-deny is enforced here by delegating to policy.evaluate (the PDP).
  This file never decides policy; it only enforces the decision.
* The principal (a non-human/workload identity) is bound when the server is
  spawned, via environment. Over stdio there is no per-request auth header, so
  the client passes identity in the spawn environment. Over an HTTP/SSE
  transport this same principal would instead be derived from a validated
  OAuth 2.1 access token or mTLS client cert - the PEP code would not change,
  only how the principal is resolved.
* Obligations returned by the PDP (audit, pii_scan_required, no_image_retention)
  are actioned here, not left as advice.
* The server has NO cloud dependency at import time. document_analyser (which
  builds a Vertex client) is imported lazily inside its tool, so the server
  starts and the six deterministic tools work even where Vertex is not
  configured. Observability/Cloud clients live at the agent layer, not here.
"""

from __future__ import annotations

import os
import sys
import json
import asyncio
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

import policy

# Make the six local tools importable.
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
from tools import (  # noqa: E402
    customer_intake,
    verify_document,
    check_pep_sanctions,
    calculate_risk_score,
    audit_logger,
    escalation_flagger,
)

SERVER_NAME = "kyc-compliance-tools"


# ---------------------------------------------------------------------------
# Tool registry: schema (for discovery) + underlying callable (for dispatch).
# Schemas are the JSON-Schema subset that survives conversion to Gemini's
# function-declaration format (object/string/number/boolean/enum only).
# ---------------------------------------------------------------------------
def _analyse_id_document(image_path: str, declared_doc_type: str) -> dict:
    """Vision tool. Imported lazily so the server has no cloud dependency to
    start. Honours the PII / no-retention obligations inline so enforcement is
    visible at this boundary rather than buried in the model call."""
    from document_analyser import check_for_real_pii, analyse_document

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    # Obligation: pii_scan_required. Refuse real PII before any analysis.
    pii = check_for_real_pii(image_bytes)
    if pii.get("contains_real_pii") and pii.get("confidence", 0) > 0.7:
        return {
            "blocked": True,
            "overall_result": "BLOCKED",
            "block_reason": f"Image appears to contain real PII. {pii.get('reason','')} "
                            f"This system accepts synthetic/specimen documents only.",
        }

    result = analyse_document(image_bytes, declared_doc_type)
    del image_bytes  # Obligation: no_image_retention.
    result["blocked"] = False
    return result


TOOLS: dict[str, dict[str, Any]] = {
    "customer_intake": {
        "fn": customer_intake,
        "description": "Load a customer profile from a JSON file and validate required fields.",
        "schema": {
            "type": "object",
            "properties": {"filepath": {"type": "string", "description": "Path to the customer JSON file."}},
            "required": ["filepath"],
        },
    },
    "verify_document": {
        "fn": verify_document,
        "description": "Deterministic format check of an ID document number for a declared type. Use the customer's id_document_type and id_document_number fields from customer_intake.",
        "schema": {
            "type": "object",
            "properties": {
                "doc_type": {"type": "string", "enum": ["passport", "driving_licence", "national_id"]},
                "doc_number": {"type": "string"},
            },
            "required": ["doc_type", "doc_number"],
        },
    },
    "check_pep_sanctions": {
        "fn": check_pep_sanctions,
        "description": "Screen a full name against the PEP / sanctions watchlist.",
        "schema": {
            "type": "object",
            "properties": {"full_name": {"type": "string"}},
            "required": ["full_name"],
        },
    },
    "calculate_risk_score": {
        "fn": calculate_risk_score,
        "description": "Combine PEP and document signals into a risk tier and confidence.",
        "schema": {
            "type": "object",
            "properties": {"pep_hit": {"type": "boolean"}, "doc_valid": {"type": "boolean"}},
            "required": ["pep_hit", "doc_valid"],
        },
    },
    "escalation_flagger": {
        "fn": escalation_flagger,
        "description": "Decide whether a case must be escalated for human review.",
        "schema": {
            "type": "object",
            "properties": {
                "risk_tier": {"type": "string", "enum": ["High", "Medium", "Low"]},
                "confidence": {"type": "number"},
            },
            "required": ["risk_tier", "confidence"],
        },
    },
    "audit_logger": {
        "fn": audit_logger,
        "description": "Append a structured entry to the tamper-evident audit trail.",
        "schema": {
            "type": "object",
            "properties": {"event": {"type": "string"}, "data": {"type": "object"}},
            "required": ["event", "data"],
        },
    },
    "analyse_id_document": {
        "fn": _analyse_id_document,
        "description": (
            "Vision analysis of an identity-document image for quality, apparent "
            "authenticity and tamper indicators. Call this ONLY when the deterministic "
            "verify_document check is inconclusive - e.g. the format passes but "
            "authenticity is unconfirmed, or it fails in a way that may be a scan/format "
            "artefact rather than a genuine problem. Accepts synthetic/specimen images only."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "Path to a specimen/synthetic ID image."},
                "declared_doc_type": {"type": "string"},
            },
            "required": ["image_path", "declared_doc_type"],
        },
    },
}


def _resolve_principal() -> policy.Principal:
    """Bind the caller's identity from the spawn environment. See module docstring
    for how this changes under an authenticated HTTP transport."""
    return policy.Principal(
        id=os.environ.get("MCP_AGENT_ID", "agent://unknown"),
        role=os.environ.get("MCP_AGENT_ROLE", "read_only_reviewer"),
    )


server = Server(SERVER_NAME)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(name=name, description=spec["description"], inputSchema=spec["schema"])
        for name, spec in TOOLS.items()
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    """The PEP. One path to every tool, so authorisation cannot be skipped."""
    principal = _resolve_principal()

    # 1. Decision (PDP). This file does not decide; it asks policy.evaluate.
    decision = policy.evaluate(principal, name, arguments)

    # 2. Audit the *attempt* regardless of outcome. A denied call is a security
    #    event and must leave a trace.
    audit_logger(
        "TOOL_CALL_DENIED" if not decision.allowed else "TOOL_CALL_AUTHORISED",
        {"principal": principal.id, "role": principal.role, "tool": name,
         "rule": decision.rule_id, "reason": decision.reason},
    )

    if not decision.allowed:
        payload = {"authorised": False, "tool": name, "reason": decision.reason,
                   "principal": principal.id}
        return [types.TextContent(type="text", text=json.dumps(payload))]

    # 3. Enforce, then execute. (Vision obligations are actioned inside the tool.)
    if name not in TOOLS:
        return [types.TextContent(type="text",
                text=json.dumps({"authorised": True, "error": f"unknown tool {name}"}))]

    try:
        result = TOOLS[name]["fn"](**arguments)
    except Exception as exc:  # surface tool errors as data, not protocol faults
        result = {"error": type(exc).__name__, "detail": str(exc)}

    envelope = {"authorised": True, "tool": name,
                "obligations": list(decision.obligations), "result": result}
    return [types.TextContent(type="text", text=json.dumps(envelope, default=str))]


async def _main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
