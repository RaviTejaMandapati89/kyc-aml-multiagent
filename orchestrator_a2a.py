import sys
import os
import json
import requests
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from google_agent import run_kyc_assessment
from aws_agent import run_aml_assessment
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


def send_task(payload: dict) -> dict:
    task_message = {
        "sender_agent_id": "kyc-orchestrator-gemini",
        "payload": payload
    }
    response = requests.post(
        f"{A2A_SERVER_URL}/tasks",
        json=task_message,
        timeout=60
    )
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
        "gemini_reason": kyc_result["gemini_reason"],
        "additional_flags": kyc_result["additional_flags"],
        "sla_hours": kyc_result["sla_hours"]
    }

    print("\n[ORCHESTRATOR] Checking A2A server health...")
    server_healthy = check_server_health()

    if server_healthy:
        print("[ORCHESTRATOR] A2A server is healthy.")
        agent_card = discover_agent()
        if agent_card:
            print(f"[ORCHESTRATOR] Agent discovered: {agent_card.get('agent_id', 'unknown')}")
            print(f"[ORCHESTRATOR] Capability: {agent_card.get('capability', 'unknown')}")
        print("[ORCHESTRATOR] Sending task via A2A protocol...")
        task_result = send_task(handoff_payload)
        aml_result = task_result.get("result", {})
        print(f"[ORCHESTRATOR] A2A task status: {task_result.get('status')}")
    else:
        print("[ORCHESTRATOR] A2A server unavailable — falling back to direct call")
        aml_result = run_aml_assessment(handoff_payload)

    print("\n[ORCHESTRATOR] Running LangGraph review workflow...")
    graph = build_review_graph()
    workflow_result = graph.invoke({
        "customer_id": kyc_result["customer_id"],
        "customer_name": kyc_result["customer_name"],
        "aml_recommendation": aml_result.get("aml_recommendation", "UNKNOWN"),
        "risk_tier": kyc_result["risk_tier"],
        "sla_hours": kyc_result["sla_hours"],
        "pep_hit": kyc_result["pep_hit"],
        "red_flags": aml_result.get("red_flags", []),
        "kyc_result": kyc_result,
        "aml_result": aml_result,
        "workflow_status": "pending",
        "final_summary": "",
        "audit_trail": [],
        "assigned_team": "",
        "review_outcome": "",
        "documents_requested": []
    })

    final_output = {
        "customer_id": kyc_result["customer_id"],
        "customer_name": kyc_result["customer_name"],
        "kyc_recommendation": kyc_result["gemini_recommendation"],
        "aml_recommendation": aml_result.get("aml_recommendation", "UNKNOWN"),
        "risk_tier": kyc_result["risk_tier"],
        "sla_hours": kyc_result["sla_hours"],
        "a2a_used": server_healthy,
        "workflow_status": workflow_result.get("workflow_status"),
        "final_summary": workflow_result.get("final_summary"),
        "timestamp": datetime.utcnow().isoformat()
    }

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