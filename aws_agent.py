import boto3
import json
import re
import os
import sys
from observability import log_pipeline_event, log_pipeline_error, trace_agent_call

bedrock = boto3.client('bedrock-runtime', region_name='eu-west-2')
MODEL_ID = 'eu.anthropic.claude-haiku-4-5-20251001-v1:0'


# ---------------------------------------------------------------------------
# Citation control
# ---------------------------------------------------------------------------
# A model asked for "compliance-grade rationale" will volunteer regulatory
# references, and some of them will not exist. An early run of this agent cited
# "FCA BCBS guidelines", conflating the FCA with the Basel Committee, which is
# not a real instrument. In a compliance tool an invented citation is worse than
# no citation: it looks authoritative and a reader may act on it.
#
# Two controls, because a prompt instruction alone is guidance, not enforcement:
#   1. The prompt supplies the permitted instruments and forbids any others.
#   2. Output is validated against the allowlist, and anything outside it is
#      recorded in `citation_warnings` on the result rather than passed through
#      silently.
#
# The allowlist is deliberately coarse, naming instruments rather than asserting
# section numbers, and would be set by a compliance SME in a real deployment.
# This does not make a citation correct; it bounds the space the model can draw
# from and surfaces anything that escapes it.
PERMITTED_CITATIONS = [
    "POCA 2002 (Proceeds of Crime Act 2002) - money laundering offences and "
    "suspicious activity reporting",
    "MLR 2017 (Money Laundering, Terrorist Financing and Transfer of Funds "
    "Regulations 2017) - customer due diligence and enhanced due diligence",
    "FCA SYSC - senior management arrangements, systems and controls",
    "FCA FCG (Financial Crime Guide)",
    "JMLSG Guidance",
]

# Detecting a fabricated citation is not the same as finding a capitalised word.
# A first version of this check flagged any all-caps token, which fired on the
# model's own decision labels ("ENHANCED REVIEW") and made the warning
# meaningless. A control that raises false alarms on correct output trains people
# to ignore it, so the check now looks for citation *context*: an acronym used
# as a reference, next to words like "under", "pursuant to", "guidelines" or
# "requirements".
_ALLOWED_ACRONYMS = {
    "POCA", "MLR", "FCA", "SYSC", "FCG", "JMLSG", "NCA", "HMRC",
}

# Vocabulary the system itself uses. Never a citation, whatever the casing.
_DOMAIN_TERMS = {
    "APPROVE", "ENHANCED", "REVIEW", "ESCALATE", "NONE", "HIGH", "MEDIUM",
    "LOW", "URGENT", "STANDARD", "AML", "KYC", "CDD", "EDD", "PEP", "SAR",
    "SARS", "ID", "UK", "SLA", "JSON", "NFA",
}

# An acronym counts as a citation only in one of these positions.
_BEFORE = r"(?:under|per|as per|pursuant to|in accordance with|consistent with|" \
          r"required by|requirements of|obligations under|set out in|per the)\s+"
_AFTER = r"\s+(?:guidelines?|guidance|rules?|requirements?|standards?|principles?|" \
         r"regulations?|provisions?|obligations?|framework)"

_CITATION_PATTERNS = [
    re.compile(_BEFORE + r"((?:[A-Z]{2,}\s*)+)", re.IGNORECASE),
    re.compile(r"\b((?:[A-Z]{2,}\s*)+)" + _AFTER),
]
_TOKEN = re.compile(r"\b[A-Z]{2,}\b")


def validate_citations(assessment: dict) -> list:
    """Return references that look like citations but fall outside the permitted set.

    Reports rather than rewrites: silently deleting a fabricated citation would
    hide the fact that the model produced one, and that signal is worth keeping
    for whoever reviews the case.
    """
    fields = [assessment.get("aml_rationale", "")]
    fields += list(assessment.get("red_flags", []) or [])
    fields += list(assessment.get("recommended_actions", []) or [])

    warnings = []
    for text in fields:
        text = str(text)
        for pattern in _CITATION_PATTERNS:
            for match in pattern.finditer(text):
                for token in _TOKEN.findall(match.group(1)):
                    if token in _DOMAIN_TERMS or token in _ALLOWED_ACRONYMS:
                        continue
                    warnings.append(
                        f"'{token}' is cited as a regulatory reference but is not "
                        f"in the permitted set; verify before relying on it")
    return sorted(set(warnings))


@trace_agent_call("aml-bedrock-agent")
def run_aml_assessment(kyc_assessment: dict) -> dict:
    print("\n" + "=" * 60)
    print("AML DEEP REASONING — AWS BEDROCK")
    print("=" * 60)
    print(f"Received KYC assessment for: {kyc_assessment['customer_name']}")
    print(f"Incoming recommendation: {kyc_assessment['gemini_recommendation']}")
    print(f"Risk tier: {kyc_assessment['risk_tier']}")

    permitted = "\n".join(f"  - {c}" for c in PERMITTED_CITATIONS)
    prompt = f"""You are a senior AML compliance analyst at a UK bank regulated by the FCA.

You have received a KYC pre-assessment for a customer and must provide a final AML determination.

KYC Assessment Summary:
- Customer: {kyc_assessment['customer_name']}
- Document Valid: {kyc_assessment['document_valid']}
- PEP/Sanctions Hit: {kyc_assessment['pep_hit']}
- PEP Reason: {kyc_assessment['pep_reason']}
- Risk Tier: {kyc_assessment['risk_tier']} (confidence: {kyc_assessment['confidence']})
- Initial Recommendation: {kyc_assessment['gemini_recommendation']}

The initial recommendation is the KYC stage's label, not a finding. Reach your
own determination from the screening results above; do not defer to it.

Your task:
1. Review the assessment for AML red flags beyond the initial screening
2. Consider UK financial crime typologies — layering, smurfing, trade-based money laundering
3. Assess whether the risk signals are consistent or contradictory
4. Determine if the initial recommendation is appropriate or needs escalating/de-escalating
5. Write a compliance-grade rationale that a human officer can act on

Citation rules, which are strict:
- You may reference ONLY the following instruments:
{permitted}
- Do not cite any other body, sourcebook, standard or guideline. If none of the
  above applies, write the rationale with no citation at all.
- Do not invent section, regulation or rule numbers. Reference an instrument by
  name only unless you are certain of the specific provision.

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
            # Pinned to 0. Left at the default, the same customer produced
            # different determinations across runs: one assessment routed to
            # Enhanced Due Diligence with a document request, the next escalated
            # the same case to Financial Crime Investigation and suspended the
            # account. A compliance decision that changes depending on when it
            # was run cannot be defended to a regulator or reproduced in an
            # audit, so the sampling is removed. The KYC agent is pinned for the
            # same reason.
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]
        })
    )

    raw_response = json.loads(response['body'].read())
    raw_text = raw_response['content'][0]['text'].strip()

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

    # Validate citations before the result travels any further. A warning here
    # means the model referenced something outside the permitted set; the
    # assessment is still returned, but the deviation is recorded rather than
    # passed downstream unnoticed.
    citation_warnings = validate_citations(aml_result)
    if citation_warnings:
        final_output["citation_warnings"] = citation_warnings
        log_pipeline_event("AML_CITATION_WARNING", kyc_assessment['customer_id'], {
            "warnings": citation_warnings,
        })
        print("\n[CITATION CHECK] Unpermitted references detected:")
        for w in citation_warnings:
            print(f"  - {w}")

    log_pipeline_event("AML_COMPLETE", kyc_assessment['customer_id'], {
        "recommendation": aml_result['final_recommendation'],
        "confidence": aml_result['confidence'],
        "red_flag_count": len(aml_result['red_flags']),
        "risk_tier": kyc_assessment['risk_tier']
    })

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


if __name__ == "__main__":
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