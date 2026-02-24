import csv
import os

# ── Tool 1: PEP / Sanctions Screening ────────────────────────────────────────

def check_pep_sanctions(full_name: str) -> dict:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    list_path = os.path.join(base_dir, '..', 'data', 'pep_sanctions_list.csv')
    print(f"Looking for CSV at: {os.path.abspath(list_path)}")

    with open(list_path, newline='', encoding='utf-8') as f:
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


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing check_pep_sanctions...")
    result = check_pep_sanctions("James Harrington")
    print(f"James Harrington: {result}")

    result = check_pep_sanctions("Sarah Johnson")
    print(f"Sarah Johnson: {result}")

    print("\nTesting calculate_risk_score...")
    result = calculate_risk_score(pep_hit=True, doc_valid=True)
    print(f"PEP hit + valid docs: {result}")

    result = calculate_risk_score(pep_hit=False, doc_valid=True)
    print(f"No PEP + valid docs: {result}")