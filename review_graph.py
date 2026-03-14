import json
import os
from typing import TypedDict, Literal
from langgraph.graph import StateGraph, END
import sys
import os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))
from tools import audit_logger


# ── State definition ──────────────────────────────────────────────────────────

class ReviewState(TypedDict):
    customer_id: str
    customer_name: str
    aml_recommendation: str
    risk_tier: str
    pep_hit: bool
    document_valid: bool
    aml_confidence: float
    red_flags: list
    recommended_actions: list
    sla_hours: int
    assigned_team: str
    review_outcome: str
    sar_required: bool
    documents_requested: list
    workflow_complete: bool
    audit_trail: list


# ── Node 1: Receive and validate the decision ─────────────────────────────────

def receive_decision(state: ReviewState) -> ReviewState:
    print(f"\n[NODE 1] Receiving decision for {state['customer_name']}")
    print(f"  AML Recommendation: {state['aml_recommendation']}")
    print(f"  Risk Tier: {state['risk_tier']}")
    print(f"  SLA: {state['sla_hours']} hours")

    audit_logger("WORKFLOW_STARTED", {
        "customer_id": state["customer_id"],
        "recommendation": state["aml_recommendation"],
        "sla_hours": state["sla_hours"]
    })

    state["audit_trail"] = [f"Workflow started for {state['customer_name']}"]
    state["sar_required"] = False
    state["documents_requested"] = []
    state["assigned_team"] = ""
    state["review_outcome"] = ""
    state["workflow_complete"] = False

    return state


# ── Node 2: Route by risk ─────────────────────────────────────────────────────

def route_by_risk(state: ReviewState) -> Literal["approve", "enhanced_review", "escalate"]:
    recommendation = state["aml_recommendation"]
    print(f"\n[NODE 2] Routing {state['customer_name']} — {recommendation}")

    if recommendation == "APPROVE":
        return "approve"
    elif recommendation == "ENHANCED REVIEW":
        return "enhanced_review"
    else:
        return "escalate"


# ── Node 3a: Approve path ─────────────────────────────────────────────────────

def process_approval(state: ReviewState) -> ReviewState:
    print(f"\n[NODE 3a] Processing approval for {state['customer_name']}")

    state["assigned_team"] = "Standard Onboarding Team"
    state["review_outcome"] = "APPROVED — Account opening authorised"
    state["audit_trail"].append("Case routed to Standard Onboarding Team")
    state["audit_trail"].append("Account opening authorised — no further action required")

    audit_logger("CASE_APPROVED", {
        "customer_id": state["customer_id"],
        "team": state["assigned_team"]
    })

    print(f"  Assigned to: {state['assigned_team']}")
    print(f"  Outcome: {state['review_outcome']}")

    state["workflow_complete"] = True
    return state


# ── Node 3b: Enhanced review path ────────────────────────────────────────────

def process_enhanced_review(state: ReviewState) -> ReviewState:
    print(f"\n[NODE 3b] Processing enhanced review for {state['customer_name']}")

    state["assigned_team"] = "Enhanced Due Diligence Team"

    # Determine which documents to request based on red flags
    docs_needed = []
    for flag in state["red_flags"]:
        flag_lower = flag.lower()
        if "document" in flag_lower or "identification" in flag_lower:
            docs_needed.append("Valid government-issued photo ID")
        if "beneficial ownership" in flag_lower or "ownership" in flag_lower:
            docs_needed.append("Companies House extract")
            docs_needed.append("Beneficial ownership declaration")
        if "source of wealth" in flag_lower or "income" in flag_lower:
            docs_needed.append("3 months bank statements")
            docs_needed.append("Employment contract or payslips")

    # Deduplicate
    state["documents_requested"] = list(set(docs_needed)) if docs_needed else [
        "Proof of address",
        "Source of funds declaration"
    ]

    state["review_outcome"] = f"ENHANCED REVIEW — {len(state['documents_requested'])} documents requested"
    state["audit_trail"].append(f"Case routed to {state['assigned_team']}")
    state["audit_trail"].append(f"Documents requested: {', '.join(state['documents_requested'])}")
    state["audit_trail"].append(f"SLA: {state['sla_hours']} hours for customer response")

    audit_logger("ENHANCED_REVIEW_INITIATED", {
        "customer_id": state["customer_id"],
        "documents_requested": state["documents_requested"],
        "sla_hours": state["sla_hours"]
    })

    print(f"  Assigned to: {state['assigned_team']}")
    print(f"  Documents requested: {state['documents_requested']}")

    state["workflow_complete"] = True
    return state


# ── Node 3c: Escalate path ────────────────────────────────────────────────────

def process_escalation(state: ReviewState) -> ReviewState:
    print(f"\n[NODE 3c] Processing escalation for {state['customer_name']}")

    state["assigned_team"] = "Financial Crime Investigation Team"

    # Determine if SAR is required
    sar_triggers = [
        "fraud", "sanctions", "pep", "layering",
        "suspicious", "investigation", "fictitious"
    ]
    sar_required = any(
        trigger in flag.lower()
        for flag in state["red_flags"]
        for trigger in sar_triggers
    )
    state["sar_required"] = sar_required

    state["review_outcome"] = "ESCALATED — Financial Crime Investigation Team assigned"
    if sar_required:
        state["review_outcome"] += " | SAR filing consideration required"

    state["audit_trail"].append(f"URGENT: Case escalated to {state['assigned_team']}")
    state["audit_trail"].append(f"PEP hit: {state['pep_hit']}")
    state["audit_trail"].append(f"Red flags identified: {len(state['red_flags'])}")
    if sar_required:
        state["audit_trail"].append("SAR filing under POCA 2002 — under consideration")
    state["audit_trail"].append("Account opening SUSPENDED pending investigation")

    audit_logger("CASE_ESCALATED", {
        "customer_id": state["customer_id"],
        "team": state["assigned_team"],
        "sar_required": sar_required,
        "red_flag_count": len(state["red_flags"])
    })

    print(f"  Assigned to: {state['assigned_team']}")
    print(f"  SAR required: {sar_required}")
    print(f"  Red flags: {len(state['red_flags'])}")

    state["workflow_complete"] = True
    return state


# ── Node 4: Final summary ─────────────────────────────────────────────────────

def final_summary(state: ReviewState) -> ReviewState:
    print(f"\n[NODE 4] Final summary for {state['customer_name']}")

    summary = {
        "customer_id": state["customer_id"],
        "customer_name": state["customer_name"],
        "aml_recommendation": state["aml_recommendation"],
        "assigned_team": state["assigned_team"],
        "review_outcome": state["review_outcome"],
        "sar_required": state["sar_required"],
        "documents_requested": state["documents_requested"],
        "sla_hours": state["sla_hours"],
        "audit_trail": state["audit_trail"]
    }

    print(json.dumps(summary, indent=2))

    # Save to outputs
    output_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'outputs'
    )
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(
        output_dir, f"{state['customer_id']}_workflow.json"
    )
    with open(output_file, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\nWorkflow saved to: {output_file}")

    audit_logger("WORKFLOW_COMPLETE", {
        "customer_id": state["customer_id"],
        "outcome": state["review_outcome"],
        "sar_required": state["sar_required"]
    })

    return state


# ── Build the graph ───────────────────────────────────────────────────────────

def build_review_graph():
    graph = StateGraph(ReviewState)

    # Add nodes
    graph.add_node("receive_decision", receive_decision)
    graph.add_node("process_approval", process_approval)
    graph.add_node("process_enhanced_review", process_enhanced_review)
    graph.add_node("process_escalation", process_escalation)
    graph.add_node("final_summary", final_summary)

    # Entry point
    graph.set_entry_point("receive_decision")

    # Conditional routing after receive_decision
    graph.add_conditional_edges(
        "receive_decision",
        route_by_risk,
        {
            "approve": "process_approval",
            "enhanced_review": "process_enhanced_review",
            "escalate": "process_escalation"
        }
    )

    # All paths converge at final_summary
    graph.add_edge("process_approval", "final_summary")
    graph.add_edge("process_enhanced_review", "final_summary")
    graph.add_edge("process_escalation", "final_summary")
    graph.add_edge("final_summary", END)

    return graph.compile()


# ── Run with test data ────────────────────────────────────────────────────────

if __name__ == "__main__":
    review_graph = build_review_graph()

    test_cases = [
        {
            "customer_id": "CUST-2026-001",
            "customer_name": "James Harrington",
            "aml_recommendation": "ESCALATE",
            "risk_tier": "High",
            "pep_hit": True,
            "document_valid": True,
            "aml_confidence": 0.98,
            "red_flags": [
                "Confirmed PEP/Sanctions match - financial fraud investigation",
                "Employer registered in future - fictitious business indicator",
                "Potential layering typology"
            ],
            "recommended_actions": [
                "Refer to Financial Crime Investigation Team",
                "Consider SAR filing under POCA 2002"
            ],
            "sla_hours": 4,
            "assigned_team": "",
            "review_outcome": "",
            "sar_required": False,
            "documents_requested": [],
            "workflow_complete": False,
            "audit_trail": []
        },
        {
            "customer_id": "CUST-2026-002",
            "customer_name": "Sarah Johnson",
            "aml_recommendation": "ENHANCED REVIEW",
            "risk_tier": "Medium",
            "pep_hit": False,
            "document_valid": False,
            "aml_confidence": 0.82,
            "red_flags": [
                "Invalid identity document",
                "Surname matches employer - potential beneficial ownership",
                "Source of wealth unconfirmed"
            ],
            "recommended_actions": [
                "Request valid photo ID",
                "Conduct beneficial ownership questionnaire"
            ],
            "sla_hours": 24,
            "assigned_team": "",
            "review_outcome": "",
            "sar_required": False,
            "documents_requested": [],
            "workflow_complete": False,
            "audit_trail": []
        },
        {
            "customer_id": "CUST-2026-003",
            "customer_name": "Emma Williams",
            "aml_recommendation": "ENHANCED REVIEW",
            "risk_tier": "Low",
            "pep_hit": False,
            "document_valid": True,
            "aml_confidence": 0.75,
            "red_flags": [
                "Surname matches employer - potential beneficial ownership",
                "Source of wealth verification incomplete"
            ],
            "recommended_actions": [
                "Conduct beneficial ownership questionnaire",
                "Request Companies House extract"
            ],
            "sla_hours": 24,
            "assigned_team": "",
            "review_outcome": "",
            "sar_required": False,
            "documents_requested": [],
            "workflow_complete": False,
            "audit_trail": []
        }
    ]

    print("\n" + "=" * 60)
    print("LANGGRAPH REVIEW WORKFLOW")
    print("=" * 60)

    for case in test_cases:
        print(f"\n{'=' * 60}")
        print(f"Processing: {case['customer_name']}")
        print(f"{'=' * 60}")
        result = review_graph.invoke(case)
        print(f"\nFinal outcome: {result['review_outcome']}")
        print(f"Assigned team: {result['assigned_team']}")
        print("\n")

    print("\n" + "=" * 60)
    print("ALL CASES PROCESSED")
    print("=" * 60)