import streamlit as st
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from google_agent import run_kyc_assessment
from aws_agent import run_aml_assessment
from review_graph import build_review_graph
from document_analyser import run_document_check

st.set_page_config(
    page_title="KYC/AML Assessment Platform",
    page_icon="🏦",
    layout="wide"
)

st.title("🏦 KYC/AML Multi-Agent Assessment Platform")
st.markdown("*Powered by Google Gemini. AWS Bedrock Claude. LangGraph.*")
st.divider()

GEMINI_API_KEY = "REDACTED"

tab1, tab2 = st.tabs(["🔍 KYC/AML Assessment", "📄 Document Verification"])

# ── Tab 1: Full KYC/AML Pipeline ──────────────────────────────────────────────

with tab1:
    st.sidebar.title("Customer Selection")
    input_mode = st.sidebar.radio(
        "Choose how to enter customer data",
        ["Select existing profile", "Enter new customer"]
    )

    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
    customer_file = None

    if input_mode == "Select existing profile":
        customer_files = {
            "James Harrington — High Risk": "customer.json",
            "Sarah Johnson — Medium Risk": "medium_risk_customer.json",
            "Emma Williams — Low Risk": "low_risk_customer.json"
        }
        selected_label = st.sidebar.selectbox(
            "Select Customer",
            options=list(customer_files.keys())
        )
        customer_file = os.path.join(data_dir, customer_files[selected_label])
        with open(customer_file) as f:
            customer_data = json.load(f)
        st.sidebar.divider()
        st.sidebar.markdown("**Customer Profile**")
        st.sidebar.json(customer_data)

    else:
        st.sidebar.divider()
        st.sidebar.markdown("**Enter Customer Details**")
        with st.sidebar.form("customer_form"):
            full_name = st.text_input("Full Name", placeholder="e.g. John Smith")
            customer_id = st.text_input("Customer ID", placeholder="e.g. CUST-2026-004")
            date_of_birth = st.text_input("Date of Birth", placeholder="YYYY-MM-DD")
            nationality = st.text_input("Nationality", placeholder="e.g. British")
            address = st.text_input("Address", placeholder="e.g. 10 Downing Street, London")
            doc_type = st.selectbox("Document Type", ["passport", "driving_licence", "national_id"])
            doc_number = st.text_input("Document Number", placeholder="e.g. GBR-123456789012")
            account_type = st.selectbox("Account Type", ["personal", "business"])
            annual_income = st.number_input("Annual Income (£)", min_value=0, value=50000, step=1000)
            employer = st.text_input("Employer", placeholder="e.g. Smith & Co Ltd")
            employer_registered = st.text_input("Employer Registered Date", placeholder="YYYY-MM-DD")
            submitted = st.form_submit_button("Save Customer Profile")

        if submitted:
            if not full_name or not customer_id or not doc_number:
                st.sidebar.error("Please fill in Full Name, Customer ID and Document Number.")
            else:
                new_customer = {
                    "customer_id": customer_id,
                    "full_name": full_name,
                    "date_of_birth": date_of_birth,
                    "nationality": nationality,
                    "address": address,
                    "id_document_type": doc_type,
                    "id_document_number": doc_number,
                    "account_type": account_type,
                    "annual_income": annual_income,
                    "employer": employer,
                    "employer_registered": employer_registered
                }
                temp_file = os.path.join(data_dir, f"{customer_id}.json")
                with open(temp_file, 'w') as f:
                    json.dump(new_customer, f, indent=2)
                customer_file = temp_file
                st.sidebar.success(f"Profile saved for {full_name}")
                st.sidebar.json(new_customer)
                st.session_state["custom_customer_file"] = temp_file

        if "custom_customer_file" in st.session_state:
            customer_file = st.session_state["custom_customer_file"]

    if customer_file and os.path.exists(customer_file):
        run_button = st.button("Run Full KYC/AML Assessment", type="primary", use_container_width=True)

        if run_button:
            with st.spinner("Stage 1: Running KYC assessment via Google Gemini..."):
                kyc_result = run_kyc_assessment(customer_file)

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

            with st.expander("View Audit Trail"):
                for i, entry in enumerate(workflow_result["audit_trail"], 1):
                    st.markdown(f"{i}. {entry}")

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

# ── Tab 2: Document Verification ─────────────────────────────────────────────

with tab2:
    st.header("📄 Document Verification")
    st.markdown("Upload a synthetic or sample identity document for AI-powered quality and authenticity checking.")

    st.warning("""
⚠️ **Important — Synthetic Data Only**

This tool must only be used with synthetic, sample, or specimen documents.
Never upload real identity documents containing actual personal data.
By proceeding you confirm the document contains no real PII.
""")

    confirmed = st.checkbox("I confirm this document contains no real personal data and is synthetic or a sample only.")

    if confirmed:
        col1, col2 = st.columns(2)

        with col1:
            declared_type = st.selectbox(
                "Declared Document Type",
                ["passport", "driving_licence", "national_id"],
                key="doc_type_select"
            )

        with col2:
            uploaded_file = st.file_uploader(
                "Upload Document Image",
                type=["jpg", "jpeg", "png"],
                help="Upload a synthetic or specimen document image only"
            )

        if uploaded_file is not None:
            st.image(uploaded_file, caption="Uploaded document", width=400)

            analyse_button = st.button("Analyse Document", type="primary", use_container_width=True)

            if analyse_button:
                with st.spinner("Analysing document via Google Gemini Vision..."):
                    result = run_document_check(uploaded_file, declared_type, GEMINI_API_KEY)

                if result.get("blocked"):
                    st.error(f"🚫 **Blocked:** {result['block_reason']}")

                else:
                    overall = result.get("overall_result", "FAIL")

                    if overall == "PASS":
                        st.success(f"✅ Document Result: {overall}")
                    elif overall == "REVIEW":
                        st.warning(f"⚠️ Document Result: {overall}")
                    else:
                        st.error(f"❌ Document Result: {overall}")

                    st.info(f"**Summary:** {result.get('overall_reason', 'No summary available')}")

                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Image Quality", result.get("image_quality", "Unknown"))
                    col2.metric("Appears Genuine", "Yes" if result.get("appears_genuine") else "No")
                    col3.metric("Type Match", "Yes" if result.get("document_type_match") else "No")
                    col4.metric("Photo Present", "Yes" if result.get("photograph_present") else "No")

                    col_left, col_right = st.columns(2)

                    with col_left:
                        st.markdown("**Quality Issues**")
                        st.write(result.get("quality_issues", "None"))

                        st.markdown("**Authenticity Concerns**")
                        st.write(result.get("authenticity_concerns", "None"))

                    with col_right:
                        if result.get("risk_indicators"):
                            st.markdown("**Risk Indicators**")
                            for indicator in result["risk_indicators"]:
                                st.markdown(f"- 🚩 {indicator}")
                        else:
                            st.markdown("**Risk Indicators**")
                            st.write("None identified")

                    st.caption("✓ Image data was not retained after analysis.")

    else:
        st.info("Please confirm the document contains no real personal data to proceed.")
