"""
Proves the AML agent's citation control, with no cloud credentials.
Run:  python tests/test_citation_control.py

Why this exists
---------------
Asked for a "compliance-grade rationale", a model will volunteer regulatory
references, and some of them will not exist. An early run of this agent cited
"FCA BCBS guidelines", conflating the Financial Conduct Authority with the Basel
Committee on Banking Supervision. In a compliance tool an invented citation is
worse than none: it reads as authoritative.

The prompt now supplies a permitted set, but a prompt instruction is guidance,
not a control. The validator is the control, and these tests exercise it against
text taken from real runs.
"""
import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Stub observability so importing the agent needs no GCP credentials.
_obs = types.ModuleType("observability")
_obs.log_pipeline_event = _obs.log_pipeline_error = lambda *a, **k: None
_obs.trace_agent_call = lambda name: (lambda f: f)
sys.modules.setdefault("observability", _obs)

from aws_agent import validate_citations, PERMITTED_CITATIONS  # noqa: E402


def test_fabricated_body_is_flagged():
    """The actual failure that prompted this control."""
    assessment = {
        "aml_rationale": "Customer presents low AML risk with clean screening.",
        "red_flags": [],
        "recommended_actions": [
            "Conduct routine periodic review within 12 months as per FCA BCBS guidelines"],
    }
    warnings = validate_citations(assessment)
    assert any("BCBS" in w for w in warnings), warnings


def test_decision_labels_are_not_citations():
    """The model writes its own recommendation labels in capitals inside the
    rationale. An earlier version of this check flagged them, which made the
    warning meaningless. Text taken verbatim from a real run."""
    assessment = {
        "aml_rationale": "The initial recommendation of ENHANCED REVIEW is appropriate "
                         "and should be maintained. The invalid document format prevents "
                         "completion of customer due diligence as required under MLR 2017; "
                         "this is consistent with FCA SYSC expectations.",
        "red_flags": ["Unable to verify customer identity to required standard under MLR 2017"],
        "recommended_actions": ["Reassess risk tier once valid documentation obtained"],
    }
    assert validate_citations(assessment) == []


def test_novel_fabrication_is_flagged():
    """The control must catch references it has never seen, not just BCBS."""
    assessment = {
        "aml_rationale": "Escalation is required under FATF OECD provisions.",
        "red_flags": [], "recommended_actions": [],
    }
    warnings = validate_citations(assessment)
    assert any("FATF" in w for w in warnings) and any("OECD" in w for w in warnings)


def test_permitted_references_pass():
    """Real instruments from the allowlist must not trip the validator."""
    assessment = {
        "aml_rationale": "Escalation is required under POCA 2002 and the customer "
                         "due diligence obligations in MLR 2017, consistent with FCA SYSC.",
        "red_flags": ["Identity verification chain incomplete"],
        "recommended_actions": ["Refer to JMLSG Guidance on enhanced due diligence"],
    }
    assert validate_citations(assessment) == []


def test_clean_rationale_with_no_citation_passes():
    assessment = {
        "aml_rationale": "Risk signals are consistent and no further action is needed.",
        "red_flags": [],
        "recommended_actions": ["Proceed with account opening"],
    }
    assert validate_citations(assessment) == []


def test_every_field_is_scanned():
    """A fabricated reference must be caught wherever it appears, not only in
    the rationale."""
    for field in ("aml_rationale", "red_flags", "recommended_actions"):
        assessment = {"aml_rationale": "", "red_flags": [], "recommended_actions": []}
        text = "Assessed under FATCA thresholds"
        assessment[field] = text if field == "aml_rationale" else [text]
        assert validate_citations(assessment), f"not scanned: {field}"


def test_allowlist_is_not_empty():
    assert len(PERMITTED_CITATIONS) >= 3


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(tests)} checks passed, no cloud credentials used.")
