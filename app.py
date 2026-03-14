import streamlit as st
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from google_agent import run_kyc_assessment
from aws_agent import run_aml_assessment
from review_graph import build_review_graph

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="KYC/AML Assessment Platform",
    page_icon="🏦",
    layout="wide"
)

# ── Header ────────────────────────────────────────────────────────────────────

st.title("🏦 KYC/AML Multi-Agent Assessment Platform")
st.markdown("*Powered by Google Gemini. AWS Bedrock Claude. LangGraph.*")
st.divider()

# ── Sidebar — customer selection ──────────────────────────────────────────────

st.sidebar.title("Customer Selection")
st.sidebar.markdown("Choose a customer profile to assess.")

data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

customer_files = {
    "James Harrington — High Risk": "customer.json",
    "Sarah Johnson — Medium Risk": "medium_risk_customer.json",
    "Emma Williams — Low Risk": "low_risk_customer.json"
}

selected_label = st.sidebar.selectbox(
    "Select Customer",
    options=list(customer_files.keys())
)

selected_file = os.path.join(data_dir, customer_files[selected_label])

# Show customer profile
with open(selected_file) as f:
    customer_data = json.load(f)

st.sidebar.divider()
st.sidebar.markdown("**Customer Profile**")
st.sidebar.json(customer_data)

# ── Run button ────────────────────────────────────────────────────────────────

run_button = st.button("Run Full KYC/AML Assessment", type="primary", use_container_width=True)

if run_button:

    # ── Stage 1: KYC ─────────────────────────────────────────────────────────
    with st.spinner("Stage 1: Running KYC assessment via Google Gemini..."):
        kyc_result = run_kyc_assessment(selected_file)

    st.success("Stage 1 Complete — KYC Assessment")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Customer", kyc_result["customer_name"])
    col2.metric("Risk Tier", kyc_result["risk_tier"])
    col3.metric("PEP Hit", "Yes" if kyc_result["pep_hit"] else "No")
    col4.metric("Document Valid", "Yes" if kyc_result["document_valid"] else "No")

    st.markdown("**Gemini Recommendation**")
    rec = kyc_result["gemini_recommendation"]
    if rec == "ESCALATE":
        st.error(f"🚨 {rec} — {kyc_result['gemini_reason']}")
    elif rec == "ENHANCED REVIEW":
        st.warning(f"⚠️ {rec} — {kyc_result['gemini_reason']}")
    else:
        st.success(f"✅ {rec} — {kyc_result['gemini_reason']}")

    if kyc_result.get("additional_flags") and kyc_result["additional_flags"] != "NONE":
        st.info(f"**Additional flags:** {kyc_result['additional_flags']}")

    st.divider()

    # ── Stage 2: AML ──────────────────────────────────────────────────────────
    with st.spinner("Stage 2: Running AML deep reasoning via AWS Bedrock Claude..."):
        aml_result = run_aml_assessment(kyc_result)

    st.success("Stage 2 Complete — AML Deep Reasoning")

    col1, col2, col3 = st.columns(3)
    col1.metric("AML Recommendation", aml_result["aml_recommendation"])
    col2.metric("Confidence", f"{int(aml_result['aml_confidence'] * 100)}%")
    col3.metric("SLA", f"{aml_result['sla_hours']} hours")

    st.markdown("**AML Rationale**")
    st.info(aml_result["aml_rationale"])

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("**Red Flags**")
        for flag in aml_result["red_flags"]:
            st.markdown(f"- 🚩 {flag}")

    with col_right:
        st.markdown("**Recommended Actions**")
        for action in aml_result["recommended_actions"]:
            st.markdown(f"- ✅ {action}")

    st.divider()

    # ── Stage 3: LangGraph workflow ───────────────────────────────────────────
    with st.spinner("Stage 3: Running post-decision workflow via LangGraph..."):
        review_graph = build_review_graph()

        graph_input = {
            "customer_id": aml_result["customer_id"],
            "customer_name": aml_result["customer_name"],
            "aml_recommendation": aml_result["aml_recommendation"],
            "risk_tier": aml_result["risk_tier"],
            "pep_hit": aml_result["pep_hit"],
            "document_valid": aml_result["document_valid"],
            "aml_confidence": aml_result["aml_confidence"],
            "red_flags": aml_result["red_flags"],
            "recommended_actions": aml_result["recommended_actions"],
            "sla_hours": aml_result["sla_hours"],
            "assigned_team": "",
            "review_outcome": "",
            "sar_required": False,
            "documents_requested": [],
            "workflow_complete": False,
            "audit_trail": []
        }

        workflow_result = review_graph.invoke(graph_input)

    st.success("Stage 3 Complete — Post-Decision Workflow")

    col1, col2, col3 = st.columns(3)
    col1.metric("Assigned Team", workflow_result["assigned_team"])
    col2.metric("SAR Required", "Yes" if workflow_result["sar_required"] else "No")
    col3.metric("Documents Requested", len(workflow_result["documents_requested"]))

    final_rec = workflow_result["review_outcome"]
    st.markdown("**Final Outcome**")
    if "ESCALATED" in final_rec:
        st.error(f"🚨 {final_rec}")
    elif "ENHANCED" in final_rec:
        st.warning(f"⚠️ {final_rec}")
    else:
        st.success(f"✅ {final_rec}")

    if workflow_result["documents_requested"]:
        st.markdown("**Documents Requested from Customer**")
        for doc in workflow_result["documents_requested"]:
            st.markdown(f"- 📄 {doc}")

    st.divider()

    # ── Audit Trail ───────────────────────────────────────────────────────────
    with st.expander("View Audit Trail"):
        for i, entry in enumerate(workflow_result["audit_trail"], 1):
            st.markdown(f"{i}. {entry}")

    # ── Full JSON output ──────────────────────────────────────────────────────
    with st.expander("View Full Decision JSON"):
        final_json = {
            "customer_id": kyc_result["customer_id"],
            "customer_name": kyc_result["customer_name"],
            "pipeline": "Google Gemini → A2A → AWS Bedrock Claude → LangGraph",
            "kyc": {
                "recommendation": kyc_result["gemini_recommendation"],
                "risk_tier": kyc_result["risk_tier"],
                "pep_hit": kyc_result["pep_hit"],
                "document_valid": kyc_result["document_valid"]
            },
            "aml": {
                "recommendation": aml_result["aml_recommendation"],
                "confidence": aml_result["aml_confidence"],
                "rationale": aml_result["aml_rationale"],
                "red_flags": aml_result["red_flags"]
            },
            "workflow": {
                "assigned_team": workflow_result["assigned_team"],
                "outcome": workflow_result["review_outcome"],
                "sar_required": workflow_result["sar_required"],
                "documents_requested": workflow_result["documents_requested"]
            }
        }
        st.json(final_json)