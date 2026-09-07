"""
google_agent.py - KYC orchestrator as a real tool-calling loop.

What changed and why
--------------------
The previous version was a fixed six-step script that happened to make one
Gemini call at the end. That is a pipeline, not an agent: the control flow was
hard-coded by me, not decided by the model. This version is model-driven. Gemini
is given the tool schemas (discovered from the MCP server) and chooses which
tool to call, with what arguments, in what order, and when it has enough to
stop. The Python here is a dispatch loop, not a workflow.

How it fits the rest of the system
----------------------------------
This process is an MCP *client*. It launches mcp_server.py over stdio, lists the
tools, and dispatches every chosen call back through that server - which is where
authorisation is actually enforced (the PEP). There is also a client-side policy
pre-check below: it is advisory only, a way to avoid a round trip for a call the
server would refuse anyway. The server remains the authority. Defence in depth,
with a single source of truth for the decision (policy.py).

Two decisions to be ready to defend
-----------------------------------
1. Automatic function calling is DISABLED. The google-genai SDK will happily
   execute Python functions for you. We turn that off so that every tool call is
   forced through the MCP client and therefore through the PEP. Convenience that
   bypasses enforcement is not convenience we want.
2. There is a step budget (MAX_STEPS). A model-driven loop can loop; a bounded
   agent is a safer agent.
"""

from __future__ import annotations

import os
import sys
import json
import asyncio

from google import genai
from google.genai import types

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import policy
from observability import log_pipeline_event, trace_agent_call

PROJECT = "kyc-aml-project-488918"
LOCATION = "us-central1"
MODEL = "gemini-2.5-flash"
MAX_STEPS = 12

# Identity this agent runs as. Passed to the MCP server at spawn (so the PEP can
# resolve the principal) and reused for the advisory client-side pre-check.
AGENT_PRINCIPAL = policy.Principal("agent://kyc-orchestrator", "kyc_orchestrator")

client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)

SYSTEM_INSTRUCTION = (
    "You are a KYC/AML compliance orchestrator for a UK bank, working only with "
    "synthetic data. You have tools; decide which to use. A sound assessment "
    "usually needs: load the customer (customer_intake), a document format check "
    "(verify_document), PEP/sanctions screening (check_pep_sanctions), a risk score "
    "(calculate_risk_score), and an escalation decision (escalation_flagger). Call "
    "analyse_id_document ONLY when verify_document is inconclusive - for example the "
    "format passes but authenticity is unconfirmed, or it fails in a way that could "
    "be a scan artefact. You choose the order and whether each step is needed. When "
    "you have enough evidence, STOP calling tools and reply with a single JSON object: "
    '{"recommendation":"APPROVE|ENHANCED REVIEW|ESCALATE","reason":"...",'
    '"risk_tier":"...","escalate":true|false,"additional_flags":"... or NONE",'
    '"sla_hours":72}.'
)


def _to_gemini_schema(js: dict) -> types.Schema:
    """Convert an MCP tool inputSchema (JSON-Schema subset) into a google-genai
    types.Schema. The two formats overlap but are not identical - Gemini needs a
    typed Schema with an enum of allowed primitive types and ignores JSON-Schema
    keywords it does not model. Unsupported keywords are simply dropped."""
    T = types.Type
    mapping = {
        "object": T.OBJECT, "string": T.STRING, "number": T.NUMBER,
        "integer": T.INTEGER, "boolean": T.BOOLEAN, "array": T.ARRAY,
    }
    kind = mapping.get(js.get("type", "object"), T.OBJECT)
    kwargs: dict = {"type": kind}
    if "description" in js:
        kwargs["description"] = js["description"]
    if "enum" in js:
        kwargs["enum"] = js["enum"]
    if kind == T.OBJECT:
        props = {k: _to_gemini_schema(v) for k, v in js.get("properties", {}).items()}
        if props:
            kwargs["properties"] = props
        if js.get("required"):
            kwargs["required"] = js["required"]
    if kind == T.ARRAY and "items" in js:
        kwargs["items"] = _to_gemini_schema(js["items"])
    return types.Schema(**kwargs)


def _server_params() -> StdioServerParameters:
    env = {**os.environ,
           "MCP_AGENT_ROLE": AGENT_PRINCIPAL.role,
           "MCP_AGENT_ID": AGENT_PRINCIPAL.id}
    server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
    return StdioServerParameters(command=sys.executable, args=[server_path], env=env)


async def _dispatch(session: ClientSession, name: str, args: dict) -> dict:
    """Send one tool call through the MCP server (the enforcement path).
    A client-side pre-check short-circuits calls the server would deny anyway;
    this is an optimisation, not the authority."""
    pre = policy.evaluate(AGENT_PRINCIPAL, name, args)
    if not pre.allowed:
        return {"authorised": False, "tool": name, "reason": pre.reason,
                "note": "blocked by client-side pre-check (server would also deny)"}
    result = await session.call_tool(name, args)
    # The server returns one TextContent whose text is our JSON envelope.
    try:
        return json.loads(result.content[0].text)
    except (IndexError, json.JSONDecodeError):
        return {"authorised": True, "tool": name, "result": "<unparseable server response>"}


async def _run(customer_file_path: str) -> dict:
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_tools = (await session.list_tools()).tools
            declarations = [
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description or "",
                    parameters=_to_gemini_schema(t.inputSchema),
                )
                for t in mcp_tools
            ]
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[types.Tool(function_declarations=declarations)],
                # Force every call through our dispatch loop / the PEP.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                temperature=0,
            )

            contents = [types.Content(
                role="user",
                parts=[types.Part.from_text(
                    text=f"Assess the customer in this file: {customer_file_path}")],
            )]

            trace: list[dict] = []
            for _ in range(MAX_STEPS):
                resp = client.models.generate_content(
                    model=MODEL, contents=contents, config=config)
                candidate = resp.candidates[0]
                parts = candidate.content.parts or []
                calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

                if not calls:
                    # No tool call -> the model is done. Its text is the verdict.
                    final_text = (resp.text or "").strip()
                    return _finalise(customer_file_path, final_text, trace)

                # Record the model's turn (the function_call parts) verbatim.
                contents.append(candidate.content)

                # Execute each requested call and feed the results back.
                # NOTE: function responses are returned in a Content with role="user"
                # per the google-genai manual-calling pattern. If your SDK version
                # rejects that role, switch it to role="tool".
                response_parts = []
                for fc in calls:
                    args = dict(fc.args or {})
                    outcome = await _dispatch(session, fc.name, args)
                    trace.append({"tool": fc.name, "args": args,
                                  "authorised": outcome.get("authorised"),
                                  "result": outcome.get("result")})
                    response_parts.append(
                        types.Part.from_function_response(name=fc.name, response=outcome))
                contents.append(types.Content(role="user", parts=response_parts))

            return _finalise(customer_file_path, "", trace,
                             note=f"stopped after MAX_STEPS={MAX_STEPS}")


# Downstream contract. orchestrator.py, orchestrator_a2a.py, app.py and the A2A
# handoff into aws_agent.py all read these keys. The old pipeline produced them
# by construction; the loop has to reconstruct them. Rule: FACTS come from the
# tool results recorded in the trace (deterministic, auditable), JUDGEMENT comes
# from the model's final JSON. Downstream never has to trust the model's
# restatement of pep_hit or confidence.
CONTRACT_KEYS = (
    "customer_id", "customer_name", "document_valid", "pep_hit", "pep_reason",
    "risk_tier", "confidence", "gemini_recommendation", "gemini_reason",
    "additional_flags", "sla_hours", "escalate", "escalation_priority",
)


def _last_result(trace: list[dict], tool: str) -> dict:
    """Most recent successful result of a given tool in the trace, else {}."""
    for step in reversed(trace):
        if step["tool"] == tool and step.get("authorised") and isinstance(step.get("result"), dict):
            return step["result"]
    return {}


def _finalise(customer_file_path: str, final_text: str, trace: list[dict],
              note: str = "") -> dict:
    # --- judgement: parse the model's verdict ---
    try:
        raw = final_text
        if raw.startswith("```"):
            raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        verdict = json.loads(raw.strip()) if raw.strip() else {}
    except json.JSONDecodeError:
        verdict = {}
    if not verdict.get("recommendation"):
        verdict = {"recommendation": "ENHANCED REVIEW",
                   "reason": "model did not return a parseable verdict; manual review required",
                   "additional_flags": (final_text or note or "NONE")[:500],
                   "sla_hours": 24}

    # --- facts: pull from the tool results, not from the model ---
    intake = _last_result(trace, "customer_intake")
    if intake.get("status") == "incomplete":
        return {"status": "failed", "reason": "incomplete customer profile",
                "missing_fields": intake.get("missing_fields"), "_tool_trace": trace}
    customer = intake.get("customer", {})
    doc = _last_result(trace, "verify_document")
    pep = _last_result(trace, "check_pep_sanctions")
    risk = _last_result(trace, "calculate_risk_score")
    esc = _last_result(trace, "escalation_flagger")

    final_assessment = {
        "customer_id": customer.get("customer_id", os.path.splitext(os.path.basename(customer_file_path))[0]),
        "customer_name": customer.get("full_name", "UNKNOWN"),
        "document_valid": bool(doc.get("doc_valid", False)),
        "pep_hit": bool(pep.get("pep_hit", False)),
        "pep_reason": pep.get("reason", "check_pep_sanctions not run"),
        "risk_tier": risk.get("risk_tier", verdict.get("risk_tier", "High")),
        "confidence": risk.get("confidence", 0.0),
        "gemini_recommendation": verdict.get("recommendation"),
        "gemini_reason": verdict.get("reason", ""),
        "additional_flags": verdict.get("additional_flags", "NONE"),
        "sla_hours": verdict.get("sla_hours", 72),
        # escalation: tool result is authoritative; model's flag is the fallback
        "escalate": bool(esc.get("escalate", verdict.get("escalate", True))),
        "escalation_priority": esc.get("priority", "HIGH"),
        # new, additive: what the model actually did
        "_tool_trace": trace,
    }
    if note:
        final_assessment["_note"] = note

    # Tools the model skipped are visible here. That is a feature: the trace
    # shows model-driven control flow, and gaps are surfaced, not hidden.
    skipped = [t for t in ("customer_intake", "verify_document", "check_pep_sanctions",
                           "calculate_risk_score", "escalation_flagger")
               if not _last_result(trace, t)]
    if skipped:
        final_assessment["_skipped_tools"] = skipped

    log_pipeline_event("KYC_COMPLETE", final_assessment["customer_id"], {
        "recommendation": final_assessment["gemini_recommendation"],
        "risk_tier": final_assessment["risk_tier"],
        "pep_hit": final_assessment["pep_hit"],
        "document_valid": final_assessment["document_valid"],
        "steps": len(trace),
        "skipped_tools": skipped,
    })
    return final_assessment


@trace_agent_call("kyc-gemini-agent")
def run_kyc_assessment(customer_file_path: str) -> dict:
    """Sync entrypoint (keeps the existing observability decorator and callers)."""
    result = asyncio.run(_run(customer_file_path))
    print(json.dumps(result, indent=2, default=str))
    return result


if __name__ == "__main__":
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    for name in ("customer.json", "medium_risk_customer.json", "low_risk_customer.json"):
        run_kyc_assessment(os.path.join(data_dir, name))
        print("\n")
