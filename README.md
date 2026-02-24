# KYC/AML Multi-Agent Assessment System

Built by a PM who spent years telling engineers what to build and finally decided to find out what that actually feels like.

**[The product thinking behind this is on my LinkedIn](https://www.linkedin.com/in/ravi-teja-mandapati)** — why two agents, why MCP and A2A, and honestly whether the Azure agent even needs to be an agent (spoiler: only sometimes).

---

## What this does

New customer applies to open a financial account. The system runs a KYC/AML assessment automatically — checks sanctions lists, verifies documents, scores risk — and hands a structured recommendation to a compliance officer in Dynamics 365. The whole thing runs in under 30 seconds.

Two AI agents on two different cloud platforms handle this. Google Vertex AI does the KYC orchestration. Azure AI Foundry handles the AML reasoning. They talk to each other using A2A. Both call shared tools via MCP.

Clear-cut cases (obvious PEP match, clean customer) don't really need an agent — a rules engine is faster and cheaper for those, and in a real production system that's what you'd use. The Azure agent earns its place on the ambiguous 20%: no PEP match but multiple weak signals, contradictory documentation, borderline risk scores where a compliance officer needs an actual explanation, not a rule code. That's where LLM reasoning adds something deterministic logic can't.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    CUSTOMER APPLICATION                          │
│              (via Streamlit UI / Dynamics 365)                  │
└─────────────────────────┬───────────────────────────────────────┘
                           │ Customer profile JSON
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              GOOGLE VERTEX AI — ORCHESTRATOR AGENT              │
│                        (Gemini)                                  │
│                                                                  │
│  Calls via MCP:                                                  │
│  ├── PEP / Sanctions Screening Tool                              │
│  ├── Document Verification Tool                                  │
│  ├── Risk Scoring Tool                                           │
│  └── Audit Logger                                                │
│                                                                  │
│  Output → { risk_tier, pep_hit, doc_valid, confidence, reason } │
└─────────────────────────┬───────────────────────────────────────┘
                           │ A2A handoff
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              AZURE AI FOUNDRY — AML REASONING AGENT             │
│                     (Azure OpenAI)                               │
│                                                                  │
│  Handles the cases rules get wrong. Generates rationale that    │
│  compliance officers can actually act on, not just rule codes.  │
│                                                                  │
│  Output → { recommendation, sla_hours, assigned_tier,           │
│             confidence, rationale, escalate_flag }              │
└─────────────────────────┬───────────────────────────────────────┘
                           │
          ┌────────────────┴────────────────┐
          ▼                                  ▼
┌──────────────────┐              ┌──────────────────────┐
│  DYNAMICS 365    │              │    STREAMLIT UI       │
│  KYC Case        │              │    (demo / LinkedIn)  │
│  updated via     │              │                       │
│  REST API        │              │                       │
└──────────────────┘              └──────────────────────┘
```

---

## How it runs

1. Customer profile comes in via the Streamlit form or D365
2. `customer_intake` tool parses and validates the JSON
3. Google agent calls PEP screening, document verification, and risk scoring via MCP
4. Produces a risk classification with confidence score and reason
5. Hands off to Azure agent via A2A with a structured JSON task
6. Azure agent applies AML pattern reasoning, assigns SLA, writes a plain English rationale
7. Result goes back to orchestrator, gets written to D365, audit trail logged
8. Streamlit shows the full decision chain

---

## Stack

| | |
|---|---|
| Google Agent | Vertex AI Agent Builder + Gemini |
| Azure Agent | Azure AI Foundry + Azure OpenAI |
| Tool Protocol | MCP (Model Context Protocol) |
| Agent Protocol | A2A (Agent-to-Agent Protocol) |
| CRM | Dynamics 365 via REST API |
| UI | Streamlit |
| Language | Python 3.12 |

---

## Running it locally

```bash
git clone https://github.com/ravitejamandapati/kyc-aml-multiagent.git
cd kyc-aml-multiagent
pip install -r requirements.txt
cp .env.example .env
# add your Google and Azure API keys to .env
python orchestrator.py
```

For the UI:
```bash
streamlit run app.py
```

D365 is optional. The pipeline works fine without it for a local demo — the Streamlit UI shows the full decision flow.

---

## Test cases

Three mock customers in `/data`:

| File | What they are | Expected output |
|---|---|---|
| `high_risk_customer.json` | PEP match, valid docs | ESCALATE, 4hr SLA |
| `medium_risk_customer.json` | No PEP, but weak signals | ENHANCED REVIEW, 24hr SLA |
| `low_risk_customer.json` | Clean, nothing flagged | APPROVE, 72hr SLA |

---

## Project structure

```
kyc-aml-multiagent/
├── data/
│   ├── high_risk_customer.json
│   ├── medium_risk_customer.json
│   ├── low_risk_customer.json
│   └── pep_sanctions_list.csv
├── tools/
│   ├── customer_intake.py
│   ├── pep_screening.py
│   ├── document_verification.py
│   ├── risk_scoring.py
│   ├── audit_logger.py
│   └── escalation_flagger.py
├── agents/
│   ├── google_agent.py
│   └── azure_agent.py
├── orchestrator.py
├── app.py
├── requirements.txt
├── .env.example
└── README.md
```

---

## Build progress

| Week | What | Status |
|---|---|---|
| Week 1 | Environment, MCP tool layer | 🔨 In progress |
| Week 2 | Google Vertex AI agent | Not started |
| Week 3 | Azure AML agent + A2A pipeline | Not started |
| Week 4 | D365 + Streamlit + publish | Not started |

---

## What I learned

*(Writing this at the end — honestly, not the version that makes it sound like everything went to plan)*

---

## About

I'm Ravi Teja, a Product Manager at Lloyds Banking Group working on the Agentic AI platform. I built this because I've been making architectural decisions for AI systems for a few years now and wanted to actually feel what those decisions cost. Turns out quite a lot.

[LinkedIn](https://www.linkedin.com/in/ravi-teja-mandapati) · [Email](mailto:reachravitejamandapati@gmail.com)

