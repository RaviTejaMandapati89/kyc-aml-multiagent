import csv
import os
import json
import datetime

# ── Tool 1: PEP / Sanctions Screening ────────────────────────────────────────

def check_pep_sanctions(full_name: str) -> dict:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    list_path = os.path.join(base_dir, '..', 'data', 'pep_sanctions_list.csv')

    with open(list_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['name'].strip().lower() == full_name.strip().lower():
                return {
                    "pep_hit": True,
                    "reason": row['reason'],
                    "risk_level": row['risk_level']
                }

    return {
        "pep_hit": False,
        "reason": "No match found on sanctions or PEP list",
        "risk_level": "NONE"
    }


# ── Tool 2: Risk Scoring ──────────────────────────────────────────────────────

def calculate_risk_score(pep_hit: bool, doc_valid: bool) -> dict:
    if pep_hit and doc_valid:
        return {"risk_tier": "High", "confidence": 0.95}
    elif pep_hit and not doc_valid:
        return {"risk_tier": "High", "confidence": 0.98}
    elif not pep_hit and not doc_valid:
        return {"risk_tier": "Medium", "confidence": 0.75}
    else:
        return {"risk_tier": "Low", "confidence": 0.90}


# ── Tool 3: Document Verification ────────────────────────────────────────────

def verify_document(doc_type: str, doc_number: str) -> dict:
    valid_formats = {
        "passport": 12,
        "driving_licence": 8,
        "national_id": 10
    }

    if doc_type.lower() not in valid_formats:
        return {
            "doc_valid": False,
            "reason": f"Unrecognised document type: {doc_type}"
        }

    expected_length = valid_formats[doc_type.lower()]
    cleaned_number = doc_number.replace("-", "").replace(" ", "")

    if len(cleaned_number) < expected_length:
        return {
            "doc_valid": False,
            "reason": f"Document number too short for {doc_type}"
        }

    return {
        "doc_valid": True,
        "reason": "Document format valid"
    }


# ── Tool 4: Customer Intake ───────────────────────────────────────────────────

def customer_intake(filepath: str) -> dict:
    with open(filepath, 'r', encoding='utf-8') as f:
        customer = json.load(f)

    required_fields = [
        "customer_id", "full_name", "date_of_birth",
        "nationality", "id_document_type", "id_document_number"
    ]

    missing = [f for f in required_fields if f not in customer]

    if missing:
        return {
            "status": "incomplete",
            "missing_fields": missing,
            "customer": None
        }

    return {
        "status": "complete",
        "missing_fields": [],
        "customer": customer
    }


# ── Tool 5: Audit Logger ──────────────────────────────────────────────────────

def audit_logger(event: str, data: dict) -> dict:
    log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', 'audit_trail.log'
    )

    timestamp = datetime.datetime.utcnow().isoformat()
    log_entry = f"[{timestamp}] EVENT: {event} | DATA: {data}\n"

    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(log_entry)

    return {
        "logged": True,
        "timestamp": timestamp,
        "event": event
    }


# ── Tool 6: Escalation Flagger ────────────────────────────────────────────────

def escalation_flagger(risk_tier: str, confidence: float) -> dict:
    escalation_threshold = 0.80

    if risk_tier == "High":
        return {
            "escalate": True,
            "reason": "High risk tier — mandatory escalation",
            "priority": "URGENT"
        }

    if risk_tier == "Medium" and confidence < escalation_threshold:
        return {
            "escalate": True,
            "reason": "Medium risk with low confidence — escalate for human review",
            "priority": "STANDARD"
        }

    return {
        "escalate": False,
        "reason": "Risk within acceptable parameters",
        "priority": "NONE"
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    print("=" * 60)
    print("TOOL 1: PEP / Sanctions Screening")
    print("=" * 60)
    result = check_pep_sanctions("James Harrington")
    print(f"James Harrington (on list): {result}")
    result = check_pep_sanctions("Sarah Johnson")
    print(f"Sarah Johnson (not on list): {result}")

    print()
    print("=" * 60)
    print("TOOL 2: Risk Scoring")
    print("=" * 60)
    result = calculate_risk_score(pep_hit=True, doc_valid=True)
    print(f"PEP hit + valid docs: {result}")
    result = calculate_risk_score(pep_hit=False, doc_valid=True)
    print(f"No PEP + valid docs: {result}")
    result = calculate_risk_score(pep_hit=False, doc_valid=False)
    print(f"No PEP + invalid docs: {result}")

    print()
    print("=" * 60)
    print("TOOL 3: Document Verification")
    print("=" * 60)
    result = verify_document("passport", "GBR-490122887")
    print(f"Valid passport: {result}")
    result = verify_document("passport", "GBR-123")
    print(f"Short number: {result}")
    result = verify_document("visa", "XYZ-999")
    print(f"Unknown type: {result}")

    print()
    print("=" * 60)
    print("TOOL 4: Customer Intake")
    print("=" * 60)
    customer_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', 'data', 'customer.json'
    )
    result = customer_intake(customer_path)
    print(f"Customer intake result: {result}")

    print()
    print("=" * 60)
    print("TOOL 5: Audit Logger")
    print("=" * 60)
    result = audit_logger("PEP_SCREENING_COMPLETE", {
        "customer_id": "CUST-2026-001",
        "pep_hit": True,
        "risk_level": "HIGH"
    })
    print(f"Log entry created: {result}")

    print()
    print("=" * 60)
    print("TOOL 6: Escalation Flagger")
    print("=" * 60)
    result = escalation_flagger(risk_tier="High", confidence=0.95)
    print(f"High risk: {result}")
    result = escalation_flagger(risk_tier="Medium", confidence=0.72)
    print(f"Medium risk low confidence: {result}")
    result = escalation_flagger(risk_tier="Low", confidence=0.90)
    print(f"Low risk: {result}")

    print()
    print("=" * 60)
    print("ALL TOOLS TESTED")
    print("=" * 60)