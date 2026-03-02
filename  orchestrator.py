import os
import json
import sys

# ── Import both agents ────────────────────────────────────────────────────────
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from google_agent import run_kyc_assessment
from aws_agent import run_aml_assessment


# ── Full pipeline ─────────────────────────────────────────────────────────────

def run_full_assessment(customer_file_path: str) -> dict:
    print("\n" + "#" * 60)
    print("FULL KYC/AML PIPELINE STARTING")
    print("#" * 60)
    print(f"Customer file: {os.path.basename(customer_file_path)}")

    # ── Stage 1: Google Agent — KYC Assessment ────────────────────────────────
    print("\n>>> STAGE 1: Google Gemini — KYC Orchestration")
    kyc_result = run_kyc_assessment(customer_file_path)

    # ── A2A Handoff ───────────────────────────────────────────────────────────
    print("\n>>> A2A HANDOFF: Google → AWS Bedrock")
    print(f"Passing assessment for {kyc_result['customer_name']} to AML agent...")

    # ── Stage 2: AWS Agent — AML Deep Reasoning ───────────────────────────────
    print("\n>>> STAGE 2: AWS Bedrock Claude — AML Deep Reasoning")
    aml_result = run_aml_assessment(kyc_result)

    # ── Final Combined Output ─────────────────────────────────────────────────
    final_decision = {
        "customer_id": kyc_result["customer_id"],
        "customer_name": kyc_result["customer_name"],
        "pipeline": "Google Gemini → A2A → AWS Bedrock Claude",
        "stage_1_kyc": {
            "recommendation": kyc_result["gemini_recommendation"],
            "risk_tier": kyc_result["risk_tier"],
            "pep_hit": kyc_result["pep_hit"],
            "document_valid": kyc_result["document_valid"],
            "confidence": kyc_result["confidence"]
        },
        "stage_2_aml": {
            "recommendation": aml_result["aml_recommendation"],
            "confidence": aml_result["aml_confidence"],
            "rationale": aml_result["aml_rationale"],
            "red_flags": aml_result["red_flags"],
            "recommended_actions": aml_result["recommended_actions"]
        },
        "final_recommendation": aml_result["aml_recommendation"],
        "sla_hours": aml_result["sla_hours"],
        "escalate": aml_result["aml_recommendation"] != "APPROVE"
    }

    print("\n" + "#" * 60)
    print("PIPELINE COMPLETE — FINAL DECISION")
    print("#" * 60)
    print(json.dumps(final_decision, indent=2))

    # Save output to file
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(
        output_dir,
        f"{kyc_result['customer_id']}_decision.json"
    )
    with open(output_file, 'w') as f:
        json.dump(final_decision, f, indent=2)
    print(f"\nDecision saved to: {output_file}")

    return final_decision


# ── Run all three customers ───────────────────────────────────────────────────

if __name__ == "__main__":
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

    customers = [
        os.path.join(data_dir, 'customer.json'),
        os.path.join(data_dir, 'medium_risk_customer.json'),
        os.path.join(data_dir, 'low_risk_customer.json')
    ]

    results = []
    for customer_file in customers:
        result = run_full_assessment(customer_file)
        results.append(result)
        print("\n\n")

    # Summary table
    print("\n" + "=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    print(f"{'Customer':<25} {'KYC':<20} {'AML':<20} {'SLA'}")
    print("-" * 60)
    for r in results:
        print(f"{r['customer_name']:<25} {r['stage_1_kyc']['recommendation']:<20} {r['final_recommendation']:<20} {r['sla_hours']}hrs")