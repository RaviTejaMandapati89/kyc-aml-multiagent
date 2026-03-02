import boto3
import json
import os
import sys

# ── Bedrock client ────────────────────────────────────────────────────────────
bedrock = boto3.client('bedrock-runtime', region_name='eu-west-2')
MODEL_ID = 'eu.anthropic.claude-haiku-4-5-20251001-v1:0'


# ── Main AML reasoning function ───────────────────────────────────────────────

def run_aml_assessment(kyc_assessment: dict) -> dict:
    print("\n" + "=" * 60)
    print("AML DEEP REASONING — AWS BEDROCK")
    print("=" * 60)
    print(f"Received KYC assessment for: {kyc_assessment['customer_name']}")
    print(f"Incoming recommendation: {kyc_assessment['gemini_recommendation']}")
    print(f"Risk tier: {kyc_assessment['risk_tier']}")

    # Build the AML reasoning prompt
    prompt = f"""You are a senior AML compliance analyst at a UK bank regulated by the FCA.

You have received a KYC pre-assessment for a customer and must provide a final AML determination.

KYC Assessment Summary:
- Customer: {kyc_assessment['customer_name']}
- Document Valid: {kyc_assessment['document_valid']}
- PEP/Sanctions Hit: {kyc_assessment['pep_hit']}
- PEP Reason: {kyc_assessment['pep_reason']}
- Risk Tier: {kyc_assessment['risk_tier']} (confidence: {kyc_assessment['confidence']})
- Initial Recommendation: {kyc_assessment['gemini_recommendation']}
- Initial Reason: {kyc_assessment['gemini_reason']}
- Additional Flags: {kyc_assessment['additional_flags']}

Your task:
1. Review the assessment for AML red flags beyond the initial screening
2. Consider UK financial crime typologies — layering, smurfing, trade-based money laundering
3. Assess whether the risk signals are consistent or contradictory
4. Determine if the initial recommendation is appropriate or needs escalating/de-escalating
5. Write a compliance-grade rationale that a human officer can act on

Respond in this exact JSON format with no additional text:
{{
    "final_recommendation": "APPROVE or ENHANCED REVIEW or ESCALATE",
    "confidence": 0.0 to 1.0,
    "aml_rationale": "2-3 sentence compliance-grade explanation",
    "red_flags": ["list", "of", "specific", "concerns"] or [],
    "recommended_actions": ["list", "of", "next", "steps"] or ["No further action required"],
    "sla_hours": 72 for APPROVE, 24 for ENHANCED REVIEW, 4 for ESCALATE
}}"""

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        })
    )

    raw_response = json.loads(response['body'].read())
    raw_text = raw_response['content'][0]['text'].strip()

    # Clean JSON if wrapped in code blocks
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]

    try:
        aml_result = json.loads(raw_text.strip())
    except json.JSONDecodeError:
        aml_result = {
            "final_recommendation": "ENHANCED REVIEW",
            "confidence": 0.5,
            "aml_rationale": "Could not parse AML response — manual review required",
            "red_flags": ["Parse error — review raw output"],
            "recommended_actions": ["Manual compliance review required"],
            "sla_hours": 24
        }

    # Build final combined output
    final_output = {
        "customer_id": kyc_assessment['customer_id'],
        "customer_name": kyc_assessment['customer_name'],
        "kyc_recommendation": kyc_assessment['gemini_recommendation'],
        "aml_recommendation": aml_result['final_recommendation'],
        "aml_confidence": aml_result['confidence'],
        "aml_rationale": aml_result['aml_rationale'],
        "red_flags": aml_result['red_flags'],
        "recommended_actions": aml_result['recommended_actions'],
        "sla_hours": aml_result['sla_hours'],
        "risk_tier": kyc_assessment['risk_tier'],
        "pep_hit": kyc_assessment['pep_hit'],
        "document_valid": kyc_assessment['document_valid']
    }

    print(f"\nAML Final Recommendation: {aml_result['final_recommendation']}")
    print(f"Confidence: {aml_result['confidence']}")
    print(f"Rationale: {aml_result['aml_rationale']}")
    print("\nRed Flags:")
    for flag in aml_result['red_flags']:
        print(f"  - {flag}")
    print("\nRecommended Actions:")
    for action in aml_result['recommended_actions']:
        print(f"  - {action}")

    print("\n" + "=" * 60)
    print("AML ASSESSMENT COMPLETE")
    print("=" * 60)
    print(json.dumps(final_output, indent=2))

    return final_output


# ── Test with mock KYC assessments ────────────────────────────────────────────

if __name__ == "__main__":

    # Test Case 1 — High risk customer (James Harrington)
    high_risk_kyc = {
        "customer_id": "CUST-2026-001",
        "customer_name": "James Harrington",
        "document_valid": True,
        "pep_hit": True,
        "pep_reason": "Matches flagged entity - financial fraud investigation",
        "risk_tier": "High",
        "confidence": 0.95,
        "gemini_recommendation": "ESCALATE",
        "gemini_reason": "PEP hit plus future employer registration date",
        "additional_flags": "Employer registration date in the future — potential fraudulent entity",
        "sla_hours": 4,
        "escalate": True,
        "escalation_priority": "URGENT"
    }

    # Test Case 2 — Medium risk customer (Sarah Johnson)
    medium_risk_kyc = {
        "customer_id": "CUST-2026-002",
        "customer_name": "Sarah Johnson",
        "document_valid": False,
        "pep_hit": False,
        "pep_reason": "No match found on sanctions or PEP list",
        "risk_tier": "Medium",
        "confidence": 0.75,
        "gemini_recommendation": "ENHANCED REVIEW",
        "gemini_reason": "Invalid document number",
        "additional_flags": "Surname matches employer name — potential undisclosed ownership",
        "sla_hours": 24,
        "escalate": True,
        "escalation_priority": "STANDARD"
    }

    # Test Case 3 — Low risk customer (Emma Williams)
    low_risk_kyc = {
        "customer_id": "CUST-2026-003",
        "customer_name": "Emma Williams",
        "document_valid": True,
        "pep_hit": False,
        "pep_reason": "No match found on sanctions or PEP list",
        "risk_tier": "Low",
        "confidence": 0.9,
        "gemini_recommendation": "ENHANCED REVIEW",
        "gemini_reason": "Surname matches employer name",
        "additional_flags": "Potential undisclosed beneficial ownership",
        "sla_hours": 24,
        "escalate": False,
        "escalation_priority": "NONE"
    }

    print("\nRunning AML assessment for all 3 customers...\n")

    result1 = run_aml_assessment(high_risk_kyc)
    print("\n\n")
    result2 = run_aml_assessment(medium_risk_kyc)
    print("\n\n")
    result3 = run_aml_assessment(low_risk_kyc)