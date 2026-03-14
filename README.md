# KYC/AML Multi-Agent Compliance System

A multi-agent AI prototype for KYC and AML compliance screening, built to develop hands-on experience with agentic AI architectures. Not production software — a working prototype using synthetic data only.

## What it does

Takes a customer profile through a four-stage compliance pipeline:

1. **KYC Screening** — Google Gemini orchestrates 6 compliance tools (PEP screening, document verification, risk scoring, audit logging) and produces an initial recommendation
2. **AML Deep Reasoning** — AWS Bedrock Claude receives the KYC output via A2A handoff and applies FCA-regulated compliance reasoning, citing specific UK legislation (POCA 2002, MLR 2017, FCA SYSC)
3. **Post-Decision Workflow** — LangGraph routes the case to the right team (Standard Onboarding, Enhanced Due Diligence, or Financial Crime Investigation), triggers SAR consideration where required, and generates a document request list
4. **Document Verification** — Gemini Vision analyses uploaded identity document images for quality, authenticity, MRZ consistency, and tamper indicators

## Architecture
```
Customer Profile (JSON)
        │
        ▼
┌─────────────────────┐
│   Google Gemini      │  ← MCP Tool Layer (6 tools)
│   KYC Orchestrator   │    - customer_intake
│   google_agent.py    │    - verify_document
└─────────┬───────────┘    - check_pep_sanctions
          │                 - calculate_risk_score
          │ A2A Handoff     - audit_logger
          ▼                 - escalation_flagger
┌─────────────────────┐
│   AWS Bedrock Claude │  ← AML Deep Reasoning
│   AML Agent          │    FCA/POCA 2002/MLR 2017
│   aws_agent.py       │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   LangGraph          │  ← Post-Decision Workflow
│   Review Graph       │    Conditional routing
│   review_graph.py    │    SAR detection
└─────────┬───────────┘    Document requests
          │
          ▼
┌─────────────────────┐
│   Streamlit UI       │  ← Web interface
│   app.py             │    Real-time input
└─────────────────────┘    Document upload
```

## Tech Stack

| Component | Technology |
|---|---|
| KYC Orchestration | Google Gemini 2.5 Flash via AI Studio API |
| AML Reasoning | AWS Bedrock Claude Haiku 4.5 (eu-west-2) |
| Document Vision | Google Gemini 2.5 Flash Vision |
| Post-Decision Workflow | LangGraph |
| Tool Protocol | MCP (Model Context Protocol) |
| Frontend | Streamlit |
| Language | Python 3.13 |
| Cloud | Google Cloud + AWS (multi-cloud) |

## Project Structure
```
kyc-aml-multiagent/
├── google_agent.py          # KYC orchestration via Gemini
├── aws_agent.py             # AML deep reasoning via Bedrock Claude
├── orchestrator.py          # Full pipeline — connects both agents
├── review_graph.py          # LangGraph post-decision workflow
├── mcp_server.py            # MCP server wrapping all 6 tools
├── document_analyser.py     # Gemini Vision document verification
├── app.py                   # Streamlit UI
├── tools/
│   └── tools.py             # 6 compliance tools
├── data/
│   ├── customer.json                # High risk test profile
│   ├── medium_risk_customer.json    # Medium risk test profile
│   ├── low_risk_customer.json       # Low risk test profile
│   ├── demo_customer.json           # Demo profile
│   ├── pep_sanctions_list.csv       # Mock watchlist
│   └── specimen_passport.jpg        # Synthetic test document
└── outputs/                         # Decision JSON files (gitignored)
```

## Build Progress

| Component | Status |
|---|---|
| Tool layer — 6 compliance tools | ✅ Complete |
| Google Gemini KYC agent | ✅ Complete |
| AWS Bedrock AML agent | ✅ Complete |
| A2A orchestration pipeline | ✅ Complete |
| LangGraph review workflow | ✅ Complete |
| MCP server | ✅ Complete |
| Gemini Vision document verification | ✅ Complete |
| Streamlit UI with real-time input | ✅ Complete |

## Key Design Decisions

**Why two agents instead of one.** Separating KYC orchestration from AML reasoning creates a cleaner separation of concerns. Gemini handles tool coordination and initial screening. Claude handles regulatory reasoning — it produces more structured, legislation-aware compliance rationale.

**Why LangGraph sits post-decision.** The AI pipeline produces a recommendation. LangGraph models what the bank does with that recommendation — routing it to the right team, generating document requests, triggering SAR consideration. This mirrors how real compliance workflows operate.

**Why MCP for tools.** Direct Python imports work but MCP makes tools discoverable and callable by any agent dynamically. This is how production agentic platforms — including enterprise deployments — handle tool access.

**Why Gemini for document vision.** Gemini 2.5 Flash has stronger image understanding than Claude Haiku. For document quality assessment — MRZ validation, tamper detection, field consistency — the better vision model produces more reliable results.

## Important

This system uses synthetic data only. Never use with real customer data or genuine identity documents. Not intended for production deployment.

Built by Ravi Teja Mandapati — Product Owner, Agentic AI.
