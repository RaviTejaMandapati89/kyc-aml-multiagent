import sys
import os
import json
from google import genai

# ── Add tools folder to path so we can import our tools ──────────────────────
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))
from tools import (
    customer_intake,
    verify_document,
    check_pep_sanctions,
    calculate_risk_score,
    audit_logger,
    escalation_flagger
)

# ── Gemini client ─────────────────────────────────────────────────────────────
GEMINI_API_KEY = "REDACTED"
client = genai.Client(api_key=GEMINI_API_KEY)


# ── Main agent function ───────────────────────────────────────────────────────

def run_kyc_assessment(customer_file_path: str) -> dict:
    print("\n" + "=" * 60)
    print("KYC ASSESSMENT STARTING")
    print("=" * 60)

    # Step 1 — Customer Intake
    print("\n[1/6] Running customer intake...")
    intake_result = customer_intake(customer_file_path)

    if intake_result["status"] == "incomplete":
        print(f"STOPPED: Missing fields — {intake_result['missing_fields']}")
        return {"status": "failed", "reason": "incomplete customer profile"}

    customer = intake_result["customer"]
    print(f"Customer loaded: {customer['full_name']} ({customer['customer_id']})")
    audit_logger("INTAKE_COMPLETE", {"customer_id": customer["customer_id"]})

    # Step 2 — Document Verification
    print("\n[2/6] Verifying document...")
    doc_result = verify_document(
        doc_type=customer["id_document_type"],
        doc_number=customer["id_document_number"]
    )
    print(f"Document check: {doc_result}")
    audit_logger("DOCUMENT_VERIFICATION", {
        "customer_id": customer["customer_id"],
        "result": doc_result
    })

    # Step 3 — PEP Screening
    print("\n[3/6] Running PEP/sanctions screening...")
    pep_result = check_pep_sanctions(customer["full_name"])
    print(f"PEP check: {pep_result}")
    audit_logger("PEP_SCREENING", {
        "customer_id": customer["customer_id"],
        "result": pep_result
    })

    # Step 4 — Risk Scoring
    print("\n[4/6] Calculating risk score...")
    risk_result = calculate_risk_score(
        pep_hit=pep_result["pep_hit"],
        doc_valid=doc_result["doc_valid"]
    )
    print(f"Risk score: {risk_result}")
    audit_logger("RISK_SCORING", {
        "customer_id": customer["customer_id"],
        "result": risk_result
    })

    # Step 5 — Build context for Gemini reasoning
    print("\n[5/6] Sending to Gemini for AML reasoning...")
    context = f"""
You are an AML compliance analyst. Review this KYC assessment and provide a recommendation.

Customer Profile:
- Name: {customer['full_name']}
- Nationality: {customer['nationality']}
- Account Type: {customer['account_type']}
- Annual Income: £{customer['annual_income']}
- Employer: {customer['employer']}
- Employer Registered: {customer['employer_registered']}

Assessment Results:
- Document Valid: {doc_result['doc_valid']} ({doc_result['reason']})
- PEP/Sanctions Hit: {pep_result['pep_hit']} ({pep_result['reason']})
- Risk Tier: {risk_result['risk_tier']} (confidence: {risk_result['confidence']})

Based on these signals, provide:
1. A one-sentence recommendation (APPROVE / ENHANCED REVIEW / ESCALATE)
2. The key reason for your recommendation
3. Any additional risk factors you notice in the profile

Respond in this exact JSON format:
{{
    "recommendation": "APPROVE or ENHANCED REVIEW or ESCALATE",
    "reason": "one sentence explanation",
    "additional_flags": "any other concerns or NONE",
    "sla_hours": 72 for APPROVE, 24 for ENHANCED REVIEW, 4 for ESCALATE
}}
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=context
    )

    # Parse Gemini response
    raw_text = response.text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
    
    try:
        gemini_assessment = json.loads(raw_text.strip())
    except json.JSONDecodeError:
        gemini_assessment = {
            "recommendation": "ENHANCED REVIEW",
            "reason": "Could not parse Gemini response — manual review required",
            "additional_flags": raw_text,
            "sla_hours": 24
        }

    print(f"Gemini recommendation: {gemini_assessment['recommendation']}")
    print(f"Reason: {gemini_assessment['reason']}")

    # Step 6 — Escalation Check
    print("\n[6/6] Running escalation check...")
    escalation_result = escalation_flagger(
        risk_tier=risk_result["risk_tier"],
        confidence=risk_result["confidence"]
    )
    print(f"Escalation: {escalation_result}")

    # ── Final Assessment ──────────────────────────────────────────────────────
    final_assessment = {
        "customer_id": customer["customer_id"],
        "customer_name": customer["full_name"],
        "document_valid": doc_result["doc_valid"],
        "pep_hit": pep_result["pep_hit"],
        "pep_reason": pep_result["reason"],
        "risk_tier": risk_result["risk_tier"],
        "confidence": risk_result["confidence"],
        "gemini_recommendation": gemini_assessment["recommendation"],
        "gemini_reason": gemini_assessment["reason"],
        "additional_flags": gemini_assessment["additional_flags"],
        "sla_hours": gemini_assessment["sla_hours"],
        "escalate": escalation_result["escalate"],
        "escalation_priority": escalation_result["priority"]
    }

    audit_logger("KYC_ASSESSMENT_COMPLETE", {
        "customer_id": customer["customer_id"],
        "recommendation": gemini_assessment["recommendation"],
        "risk_tier": risk_result["risk_tier"]
    })

    print("\n" + "=" * 60)
    print("KYC ASSESSMENT COMPLETE")
    print("=" * 60)
    print(json.dumps(final_assessment, indent=2))

    return final_assessment


# ── Run it ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    customer_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'data', 'customer.json'
    )
    result = run_kyc_assessment(customer_path)