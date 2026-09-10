"""
policy.py - Policy Decision Point (PDP) for tool authorisation.

This module answers exactly one question:
    "May this principal invoke this tool with these arguments, right now?"

It returns a Decision. It executes nothing and knows nothing about MCP, Gemini,
Flask, or transport. That isolation is deliberate: the enforcement point (the
PEP, in mcp_server.py) calls this decision function, so the *same* policy
governs every path that reaches a tool. A second, advisory call from the agent
(google_agent.py) is an optimisation - it is never the authority.

Model
-----
Attribute-Based Access Control (ABAC) with a default-DENY (allowlist) posture.
A rule matches on (principal role, action=tool-name) and may carry:
  - conditions:  argument-level checks (e.g. a path must stay inside data/)
  - obligations: things the PEP must do if the call is permitted
                 (e.g. run a PII scan, do not retain the image, write an audit line)

Default-deny matters: a tool a principal was never explicitly granted is refused,
not silently allowed. That is the safe failure mode for a system that touches
sanctions screening and identity documents.

Where this goes in production
-----------------------------
The RULES below are policy-as-data, separated from enforcement code. That is the
seam where a real platform drops in OPA/Rego or AWS Cedar / Verified Permissions.
The Decision contract here mirrors those engines (permit/deny + obligations), so
the swap is mechanical rather than a rewrite.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# Root used to resolve/validate any filesystem arguments (path-traversal guard).
POLICY_ROOT = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Principal:
    """The identity making the request. For an agent this is a *non-human*
    identity: a workload/service identity, not a person. `role` drives the
    allowlist; `attributes` is where richer ABAC signals live (tenant,
    environment, assurance level) in a fuller implementation."""
    id: str
    role: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    rule_id: str | None = None
    obligations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "rule_id": self.rule_id,
            "obligations": list(self.obligations),
        }


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------
# Maps a cryptographically verified agent identity to the role policy reasons
# about. Kept separate from the rules so that adding an agent does not mean
# editing policy. In production this is the agent registry (at Lloyds, agents
# registered on Backstage), and an agent absent from it has no role and is
# therefore refused by default.
AGENT_ROLES: dict[str, str] = {
    "kyc-orchestrator-gemini": "kyc_orchestrator",
}


def principal_for_agent(agent_id: str) -> Principal:
    """Build a Principal from a VERIFIED agent identity. An unregistered agent
    gets a role that no rule grants anything to, so it is refused by default
    rather than raising - denial is a policy outcome, not an error."""
    return Principal(id=agent_id,
                     role=AGENT_ROLES.get(agent_id, "unregistered_agent"))


# ---------------------------------------------------------------------------
# Policy content (the PAP - policy-as-data, not code)
# ---------------------------------------------------------------------------
# First rule that matches (role, tool) AND passes its conditions decides.
# If no rule matches, the default effect (deny) applies.
POLICY_VERSION = "2026-09-01"

RULES: list[dict[str, Any]] = [
    {
        # The KYC orchestrator's own identity: full read/compute + audit-write.
        "id": "kyc-core-tools",
        "roles": ["kyc_orchestrator"],
        "tools": [
            "customer_intake",
            "verify_document",
            "check_pep_sanctions",
            "calculate_risk_score",
            "escalation_flagger",
            "audit_logger",
        ],
        "effect": "permit",
        "obligations": ["audit"],
    },
    {
        # Vision analysis touches identity-document images -> extra obligations,
        # and an argument constraint: the image path must stay inside data/.
        # This is the ABAC bit: we authorise on *what* is being touched, not
        # only on who is asking.
        "id": "kyc-vision-constrained",
        "roles": ["kyc_orchestrator"],
        "tools": ["analyse_id_document"],
        "effect": "permit",
        "conditions": [
            {"arg": "image_path", "op": "path_within", "value": "data"},
        ],
        "obligations": ["audit", "pii_scan_required", "no_image_retention"],
    },
    {
        # A deliberately weaker identity, to prove enforcement is real:
        # it may read and compute, but has NO grant for audit_logger (a write)
        # or analyse_id_document. Those fall through to default-deny.
        "id": "readonly-reviewer",
        "roles": ["read_only_reviewer"],
        "tools": [
            "customer_intake",
            "verify_document",
            "check_pep_sanctions",
            "calculate_risk_score",
            "escalation_flagger",
        ],
        "effect": "permit",
        "obligations": ["audit"],
    },
    {
        # Agent-to-agent delegation. The action namespace is prefixed 'a2a:' so
        # one policy engine governs both boundaries: tool calls and agent calls
        # are the same question (may this principal invoke this action?) asked
        # about different resources.
        #
        # The condition enforces data minimisation: the KYC agent may hand the
        # AML agent only the assessment fields, not the customer's full record.
        # Attaching an address, income or document image is refused.
        "id": "a2a-aml-delegation",
        "roles": ["kyc_orchestrator"],
        "tools": ["a2a:aml_assessment"],
        "effect": "permit",
        "conditions": [
            {"arg": "payload", "op": "keys_within", "value": [
                "customer_id", "customer_name", "document_valid", "pep_hit",
                "pep_reason", "risk_tier", "confidence", "gemini_recommendation",
                "gemini_reason", "additional_flags", "sla_hours",
            ]},
        ],
        "obligations": ["audit"],
    },
]


# ---------------------------------------------------------------------------
# Condition operators
# ---------------------------------------------------------------------------
def _path_within(arg_value: Any, base_rel: str) -> bool:
    """True iff arg_value resolves to a path inside POLICY_ROOT/base_rel.
    Uses realpath so ../ traversal and symlinks cannot escape the sandbox."""
    if not isinstance(arg_value, str) or not arg_value:
        return False
    base = os.path.realpath(os.path.join(POLICY_ROOT, base_rel))
    target = os.path.realpath(
        arg_value if os.path.isabs(arg_value)
        else os.path.join(POLICY_ROOT, arg_value)
    )
    return target == base or target.startswith(base + os.sep)


def _keys_within(arg_value: Any, allowed: list) -> bool:
    """True iff a dict argument carries no keys outside the allowlist.
    This enforces data minimisation at a delegation boundary: the receiving
    agent gets the fields its task needs and nothing more, so a caller cannot
    quietly widen what it forwards."""
    if not isinstance(arg_value, dict):
        return False
    return set(arg_value.keys()) <= set(allowed)


_CONDITION_OPS = {
    "path_within": _path_within,
    "keys_within": _keys_within,
}


def _conditions_pass(conditions: list[dict[str, Any]], arguments: dict[str, Any]) -> tuple[bool, str]:
    for cond in conditions:
        op = _CONDITION_OPS.get(cond["op"])
        if op is None:
            # Unknown operator -> fail closed. Never fail open on policy.
            return False, f"unknown condition operator '{cond['op']}'"
        arg_value = arguments.get(cond["arg"])
        if not op(arg_value, cond["value"]):
            return False, f"condition failed: {cond['arg']} not {cond['op']} {cond['value']!r}"
    return True, "ok"


# ---------------------------------------------------------------------------
# The decision function (this is the PDP)
# ---------------------------------------------------------------------------
def evaluate(principal: Principal, tool: str, arguments: dict[str, Any] | None = None) -> Decision:
    arguments = arguments or {}

    matched_any = False
    for rule in RULES:
        if principal.role not in rule["roles"]:
            continue
        if tool not in rule["tools"]:
            continue
        matched_any = True

        if rule.get("effect") == "deny":
            return Decision(False, f"explicit deny by rule '{rule['id']}'", rule["id"])

        ok, why = _conditions_pass(rule.get("conditions", []), arguments)
        if not ok:
            # A matching permit whose condition fails is a deny with a reason.
            return Decision(False, f"{rule['id']}: {why}", rule["id"])

        return Decision(
            True,
            f"permitted by rule '{rule['id']}'",
            rule["id"],
            tuple(rule.get("obligations", ())),
        )

    # Default-deny. Distinguish "no grant" from "grant existed but blocked".
    reason = (
        f"no rule grants role '{principal.role}' access to tool '{tool}'"
        if not matched_any else "denied by default"
    )
    return Decision(False, reason)


if __name__ == "__main__":
    # Quick self-check of the decision surface.
    kyc = Principal("agent://kyc-orchestrator", "kyc_orchestrator")
    ro = Principal("agent://reviewer", "read_only_reviewer")

    cases = [
        (kyc, "check_pep_sanctions", {"full_name": "James Harrington"}),
        (kyc, "audit_logger", {"event": "X", "data": {}}),
        (kyc, "analyse_id_document", {"image_path": "data/specimen_passport.jpg", "declared_doc_type": "passport"}),
        (kyc, "analyse_id_document", {"image_path": "../../etc/passwd", "declared_doc_type": "passport"}),
        (ro, "audit_logger", {"event": "X", "data": {}}),
        (kyc, "unknown_tool", {}),
        (kyc, "a2a:aml_assessment", {"payload": {"customer_id": "C1", "risk_tier": "High"}}),
        (kyc, "a2a:aml_assessment", {"payload": {"customer_id": "C1", "annual_income": 85000}}),
        (principal_for_agent("some-other-agent"), "a2a:aml_assessment", {"payload": {}}),
    ]
    for p, t, a in cases:
        d = evaluate(p, t, a)
        print(f"{p.role:18} {t:22} -> allowed={d.allowed!s:5}  {d.reason}")
