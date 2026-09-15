import sys
import os
import json
import requests
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from google_agent import run_kyc_assessment
import a2a_identity
from review_graph import build_review_graph

A2A_SERVER_URL = "http://localhost:5001"


def check_server_health() -> bool:
    try:
        response = requests.get(f"{A2A_SERVER_URL}/health", timeout=5)
        return response.status_code == 200
    except requests.exceptions.ConnectionError:
        return False


def discover_agent() -> dict:
    try:
        response = requests.get(f"{A2A_SERVER_URL}/agent-card", timeout=5)
        if response.status_code == 200:
            return response.json()
    except requests.exceptions.ConnectionError:
        pass
    return {}


THIS_AGENT_ID = "kyc-orchestrator-gemini"
RECEIVER_AGENT_ID = "aml-reasoning-claude"
DELEGATION_SCOPE = "aml_assessment"


def send_task(payload: dict) -> dict:
    """Delegate the AML assessment over A2A, proving our identity.

    A token is minted per request rather than reused: it is bound to this exact
    payload and valid for sixty seconds, so there is nothing worth caching. The
    sender_agent_id field is retained for wire compatibility with the published
    task schema, but the receiver ignores it and derives identity from the
    token. Both are sent so that a mismatch is visible in the audit trail.
    """
    token = a2a_identity.mint_token(
        issuer_agent_id=THIS_AGENT_ID,
        audience_agent_id=RECEIVER_AGENT_ID,
        scope=DELEGATION_SCOPE,
        payload=payload,
    )
    task_message = {
        "sender_agent_id": THIS_AGENT_ID,
        "payload": payload
    }
    response = requests.post(
        f"{A2A_SERVER_URL}/tasks",
        json=task_message,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60
    )
    if response.status_code in (401, 403):
        body = response.json()
        print(f"[ORCHESTRATOR] Delegation refused by {RECEIVER_AGENT_ID}: "
              f"{body.get('reason', body.get('error'))}")
    return response.json()


def run_full_assessment(customer_file_path: str) -> dict:
    print("\n" + "=" * 60)
    print("ORCHESTRATOR STARTING")
    print("=" * 60)

    print("\n[ORCHESTRATOR] Running KYC via Google Gemini (Vertex AI)...")
    kyc_result = run_kyc_assessment(customer_file_path)

    if kyc_result.get("status") == "failed":
        print("[ORCHESTRATOR] KYC failed — aborting pipeline")
        return kyc_result

    handoff_payload = {
        "customer_id": kyc_result["customer_id"],
        "customer_name": kyc_result["customer_name"],
        "document_valid": kyc_result["document_valid"],
        "pep_hit": kyc_result["pep_hit"],
        "pep_reason": kyc_result["pep_reason"],
        "risk_tier": kyc_result["risk_tier"],
        "confidence": kyc_result["confidence"],
        "gemini_recommendation": kyc_result["gemini_recommendation"],
        "sla_hours": kyc_result["sla_hours"]
    }
    # gemini_reason and additional_flags are deliberately NOT forwarded. They
    # are prose the KYC model writes fresh each run, and the AML agent was
    # following their wording: a run whose reason happened to contain the word
    # "escalate" produced an ESCALATE determination, while the same customer
    # described as "requiring human review" came back as ENHANCED REVIEW. The
    # AML agent now receives the deterministic tool findings and the KYC
    # recommendation label, and forms its own judgement from those. This is the
    # same rule applied at the earlier stage: facts from tools, judgement from
    # the model, and one model's judgement is not another model's fact.

    # The AML stage runs only over A2A, and only if the delegation is authorised.
    #
    # This previously fell back to calling run_aml_assessment() in-process when
    # the server was unreachable. That was fail-open: an unavailable server
    # routed the pipeline around its own authorisation boundary, with no token,
    # no policy check and no audit entry. A control that a network error can
    # disable is not a control, so the direct path has been removed entirely -
    # not just the branch, but the import, so the capability no longer exists
    # here.
    #
    # A refused delegation (401/403) is likewise terminal. Continuing with an
    # empty AML result would produce a compliance decision containing a hole
    # where the AML reasoning should be, which is worse than no decision.
    print("\n[ORCHESTRATOR] Checking A2A server health...")
    if not check_server_health():
        print("[ORCHESTRATOR] AML agent unavailable. Halting: this pipeline does "
              "not run the AML stage outside the authorised A2A path.")
        return {
            "status": "failed",
            "stage": "aml_delegation",
            "reason": f"AML agent unreachable at {A2A_SERVER_URL}. Start it with: "
                      f"python3 a2a_server.py",
            "kyc_result": kyc_result,
        }

    print("[ORCHESTRATOR] A2A server is healthy.")
    agent_card = discover_agent()
    if agent_card:
        print(f"[ORCHESTRATOR] Agent discovered: {agent_card.get('agent_id', 'unknown')}")
        security = agent_card.get("security", {})
        if security:
            print(f"[ORCHESTRATOR] Auth scheme required: {security.get('scheme')} "
                  f"({security.get('algorithm')}), scope '{security.get('required_scope')}'")

    print("[ORCHESTRATOR] Sending task via A2A protocol...")
    task_result = send_task(handoff_payload)

    if task_result.get("status") != "completed":
        reason = task_result.get("reason") or task_result.get("error") or "unknown"
        print(f"[ORCHESTRATOR] Delegation did not complete: {reason}. Halting.")
        return {
            "status": "failed",
            "stage": "aml_delegation",
            "reason": f"AML delegation was not completed: {reason}",
            "kyc_result": kyc_result,
        }

    aml_result = task_result["result"]
    print(f"[ORCHESTRATOR] A2A task status: {task_result.get('status')}")
    print(f"[ORCHESTRATOR] Authorised by rule: "
          f"{task_result.get('authorisation', {}).get('rule')}")

    print("\n[ORCHESTRATOR] Running LangGraph review workflow...")
    graph = build_review_graph()
    workflow_result = graph.invoke({
        "customer_id": kyc_result["customer_id"],
        "customer_name": kyc_result["customer_name"],
        "aml_recommendation": aml_result.get("aml_recommendation", "UNKNOWN"),
        "risk_tier": kyc_result["risk_tier"],
        # The AML agent may shorten the SLA (4 hours on an escalation). That is
        # the binding deadline, so the workflow and the saved case file must use
        # it, not the KYC agent's default. Two artefacts from one run disagreeing
        # on a deadline is a compliance problem in itself.
        "sla_hours": aml_result.get("sla_hours", kyc_result["sla_hours"]),
        "pep_hit": kyc_result["pep_hit"],
        "red_flags": aml_result.get("red_flags", []),
        "document_valid": kyc_result["document_valid"],
        "recommended_actions": aml_result.get("recommended_actions", []),
        "audit_trail": [],
        "assigned_team": "",
        "review_outcome": "",
        "sar_required": False,
        "workflow_complete": False,
        "documents_requested": []
    })

    final_output = {
        "customer_id": kyc_result["customer_id"],
        "customer_name": kyc_result["customer_name"],
        "kyc_recommendation": kyc_result["gemini_recommendation"],
        "aml_recommendation": aml_result.get("aml_recommendation", "UNKNOWN"),
        "risk_tier": kyc_result["risk_tier"],
        "sla_hours": aml_result.get("sla_hours", kyc_result["sla_hours"]),
        "a2a_used": True,
        "a2a_task_id": task_result.get("task_id"),
        "workflow_status": "complete" if workflow_result.get("workflow_complete")
                           else "incomplete",
        "assigned_team": workflow_result.get("assigned_team"),
        "review_outcome": workflow_result.get("review_outcome"),
        "sar_required": workflow_result.get("sar_required"),
        "documents_requested": workflow_result.get("documents_requested", []),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    # Surface any citation deviation the AML agent's validator recorded, so it
    # is visible in the pipeline result and not only in that agent's output.
    if aml_result.get("citation_warnings"):
        final_output["citation_warnings"] = aml_result["citation_warnings"]

    print("\n" + "=" * 60)
    print("ORCHESTRATOR COMPLETE")
    print("=" * 60)
    print(json.dumps(final_output, indent=2))
    return final_output


if __name__ == "__main__":
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

    customers = [
        os.path.join(data_dir, 'customer.json'),
        os.path.join(data_dir, 'medium_risk_customer.json'),
        os.path.join(data_dir, 'low_risk_customer.json')
    ]

    for customer_file in customers:
        result = run_full_assessment(customer_file)
        print("\n")