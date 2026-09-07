# KYC/AML Multi-Agent Compliance System

A multi-agent AI prototype for KYC and AML compliance screening, built to develop hands-on experience with agentic AI architectures. Not production software, a working prototype using synthetic data only.

## What it does

Takes a customer profile through a four-stage compliance pipeline:

1. **KYC Screening** — Google Gemini runs a tool-calling loop against an MCP server hosting seven compliance tools (customer intake, document verification, PEP and sanctions screening, risk scoring, escalation, audit logging, and vision document analysis). The model chooses which tools to call and in what order. Every call is authorised at the server before it executes.
2. **AML Deep Reasoning** — AWS Bedrock Claude receives the KYC output via A2A handoff and applies FCA-regulated compliance reasoning, citing UK legislation (POCA 2002, MLR 2017, FCA SYSC)
3. **Post-Decision Workflow** — LangGraph routes the case to the right team (Standard Onboarding, Enhanced Due Diligence, or Financial Crime Investigation), triggers SAR consideration where required, and generates a document request list
4. **Document Verification** — Gemini Vision analyses uploaded identity document images for quality, field consistency and tamper indicators, with a PII guardrail that refuses images appearing to contain real personal data

## Architecture
```
Customer profile (JSON)
        |
        v
+----------------------------+     lists tools, receives schemas
|  google_agent.py           | ------------------------------+
|  Gemini tool-calling loop  |                               |
|  (MCP client)              | <-- tool results -------------+
+-------------+--------------+
              | every tool call (stdio, MCP protocol)
              v
+----------------------------+
|  mcp_server.py             |  <- Policy Enforcement Point (PEP)
|  7 tools, one dispatch     |     calls policy.evaluate before any tool runs
|  handler                   |     audits every attempt, allowed and denied
+-------------+--------------+
              | permit                    ^
              v                           | decision (permit/deny + obligations)
   tools/tools.py (6 tools)      +--------+-------------------+
   document_analyser.py (vision) |  policy.py                 |
                                 |  Policy Decision Point     |
                                 |  ABAC, default-deny        |
                                 +----------------------------+
              |
              | A2A handoff
              v
+----------------------------+
|  AWS Bedrock Claude        |  <- AML deep reasoning
|  aws_agent.py              |     FCA / POCA 2002 / MLR 2017
+-------------+--------------+
              |
              v
+----------------------------+
|  LangGraph                 |  <- Post-decision workflow
|  review_graph.py           |     Conditional routing, SAR detection
+-------------+--------------+
              |
              v
+----------------------------+
|  Streamlit UI              |  <- Web interface
|  app.py                    |     Customer input, document upload
+----------------------------+
```

## Tech Stack

| Component | Technology |
|---|---|
| KYC Orchestration | Google Gemini 2.5 Flash via Vertex AI |
| AML Reasoning | AWS Bedrock Claude Haiku 4.5 (eu-west-2) |
| Document Vision | Google Gemini 2.5 Flash Vision via Vertex AI |
| Post-Decision Workflow | LangGraph |
| Tool Protocol | MCP (Model Context Protocol), official `mcp` SDK, stdio transport |
| Tool Authorisation | Attribute-based access control, default-deny, PDP/PEP split |
| Agent Communication | A2A Protocol |
| Observability | OpenTelemetry traces and structured logs to Cloud Trace and Cloud Logging |
| Frontend | Streamlit |
| Language | Python 3.13 |
| Cloud | Google Cloud (Vertex AI) + AWS (multi-cloud) |

## Project Structure
```
kyc-aml-multiagent/
├── google_agent.py          # KYC tool-calling loop, MCP client
├── mcp_server.py            # MCP server over stdio, Policy Enforcement Point
├── policy.py                # Policy Decision Point, ABAC with default-deny
├── aws_agent.py             # AML deep reasoning via Bedrock Claude
├── orchestrator.py          # Full pipeline, connects both agents
├── orchestrator_a2a.py      # A2A protocol orchestrator
├── a2a_server.py            # A2A HTTP server wrapping AWS agent
├── review_graph.py          # LangGraph post-decision workflow
├── document_analyser.py     # Gemini Vision document verification
├── observability.py         # OpenTelemetry tracing and structured logging
├── app.py                   # Streamlit UI
├── tests/
│   └── test_authorisation.py    # Proves the PEP over real MCP, no cloud creds
├── tools/
│   └── tools.py             # 6 compliance tools
├── data/
│   ├── customer.json                # High risk test profile
│   ├── medium_risk_customer.json    # Medium risk test profile
│   ├── low_risk_customer.json       # Low risk test profile
│   ├── CUST-2026-DEMO.json          # Demo profile
│   ├── pep_sanctions_list.csv       # Mock watchlist
│   ├── specimen_passport.jpg        # Synthetic test document
│   └── synthetic_passport_pass.jpg  # Synthetic test document
└── outputs/                         # Decision JSON files (gitignored)
```

## Running it

The authorisation layer runs with no cloud credentials at all:

```
pip install -r requirements-agentic.txt
python3 policy.py                      # policy decisions, six cases
python3 tests/test_authorisation.py    # enforcement over real MCP stdio
```

The full pipeline needs Google Cloud credentials for Gemini and AWS credentials for Bedrock:

```
gcloud auth application-default login
python3 google_agent.py                # KYC agent alone
python3 orchestrator_a2a.py            # full pipeline with A2A handoff
streamlit run app.py                   # web interface
```

## Build Progress

| Component | Status |
|---|---|
| Tool layer, 6 compliance tools | Complete |
| MCP server, 7 tools over stdio | Complete |
| Tool authorisation, PDP/PEP, default-deny | Complete, covered by tests |
| Google Gemini KYC agent as tool-calling loop | Complete |
| AWS Bedrock AML agent | Built, currently unverified (see Known gaps) |
| A2A orchestration pipeline | Complete |
| LangGraph review workflow | Complete |
| Gemini Vision document verification | Complete via the Streamlit upload tab |
| Vision as a model-callable tool | Exposed and authorised, not yet exercised (see Known gaps) |
| Streamlit UI with real-time input | Complete |
| OpenTelemetry traces and logs to GCP | Complete |

## Key Design Decisions

**Why two agents instead of one.** Separating KYC orchestration from AML reasoning creates a cleaner separation of concerns. Gemini handles tool coordination and initial screening. Claude handles regulatory reasoning, producing more structured, legislation-aware compliance rationale.

**Why MCP for tools.** Direct Python imports work fine for a single process, and for a project this size an MCP server is overhead. What it buys in a governance context is one inspectable boundary that every tool call must cross, whichever agent is calling. That boundary is where authorisation is enforced. MCP also makes tools discoverable over the wire, so the model selects them from their schemas rather than the control flow being hard-coded. The payoff is real at multi-agent scale; this implementation demonstrates the pattern.

**Why authorisation is enforced server-side.** `policy.py` is the Policy Decision Point. The MCP server's dispatch handler is the Policy Enforcement Point. The authoritative check runs in the server, next to the tools, because an agent cannot be trusted to police itself: a buggy or compromised client that requests a disallowed tool is refused by the resource. The agent runs the same policy client-side, but only as an optimisation to skip a doomed round trip. The default is deny, so a tool never explicitly granted is refused rather than silently allowed, which is the safe failure mode for sanctions screening and identity documents. Policy rules are data, not code, which is the seam where a production system would drop in OPA or Cedar.

**Why authorisation is attribute-based, not just role-based.** The vision tool's rule constrains its arguments as well as its caller: an image path must resolve inside `data/`, checked with `realpath` so `../` sequences and symlinks cannot escape. Authorisation depends on what is being touched, not only who is asking. Permitted calls also carry obligations, such as a mandatory PII scan and no image retention, which the enforcement point actions rather than merely recommends.

**Why the KYC stage is a tool-calling loop, not a script.** The previous version was a fixed six-step pipeline with a single model call at the end, which is a workflow rather than an agent. Gemini now receives the tool schemas and chooses which tools to call, in what order, and when to stop. Automatic function calling is disabled in the SDK so that no call can bypass the enforcement point, and a step budget bounds the loop. The tool trace in each assessment records what the model actually did. This is a single bounded agent, not autonomous multi-agent orchestration.

**Facts from tools, judgement from the model.** The factual fields in a final assessment (PEP hit, document validity, confidence, escalation) are taken from recorded tool results, not from the model's restatement of them. The model contributes the recommendation and its reasoning. Downstream consumers never have to trust the model's summary of a deterministic check.

**Why mandatory auditing sits at the enforcement point.** The model may call `audit_logger` for business events, and does so inconsistently across runs. That is why the compliance-critical record does not depend on it: the enforcement point writes an entry for every tool call, authorised or denied, before the tool runs. A denied call is a security event and leaves a trace.

**Why A2A for agent communication.** A2A makes the handoff between agents explicit and inspectable. Each agent publishes a capability card, tasks travel as structured messages, and the receiving agent responds with a typed result.

**Why Gemini for document vision.** Gemini 2.5 Flash has stronger image understanding than Claude Haiku for document quality assessment, tamper indicators and field consistency.

## Known gaps

Kept here deliberately, because a prototype that overstates itself is worse than one that does not.

**AWS Bedrock AML agent is currently unverified.** The code is complete and the A2A handoff passes it a well-formed payload, but the AWS credentials on the development machine have expired, so the Bedrock call cannot presently be exercised end to end. Running `orchestrator_a2a.py` reaches the AML stage and fails there on authentication.

**Vision is not yet called by the agent in the pipeline.** `analyse_id_document` is exposed as the seventh MCP tool, is authorised by policy, and its argument conditions are covered by tests. But no customer record carries an image path, so the model has had no occasion to call it during a KYC run. Document vision does work today through the Streamlit upload tab, which calls `document_analyser` directly.

**"Inconclusive" is defined in the prompt, not in the tool.** `verify_document` only performs a length check and returns a boolean, so the judgement about when to escalate to vision analysis lives in the system instruction. The better design has the deterministic tool return an explicit inconclusive state.

**Tool discovery is not filtered per principal.** Execution is gated, so a caller is refused a tool it lacks a grant for. Discovery is not: every identity sees all seven tools when listing. Filtering the list per principal would be defence in depth on top of the control that matters.

**Identity binding over stdio is not a security boundary.** The calling principal is passed to the MCP server in its spawn environment, which is appropriate for a local prototype and nothing more. Over an authenticated HTTP transport the principal would be derived from a validated OAuth 2.1 access token or an mTLS client certificate. The enforcement code would not change, only how the principal is resolved.

**Model-driven control flow costs latency.** A KYC assessment now takes roughly eight to ten seconds instead of two, because the loop makes five to ten model round trips where the old pipeline made one.

## Important

This system uses synthetic data only. Never use with real customer data or genuine identity documents. Not intended for production deployment.

Built by Ravi Teja Mandapati. Working on agent platforms in financial services.
