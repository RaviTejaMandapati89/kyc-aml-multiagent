# KYC/AML Multi-Agent Compliance System

A working prototype of an agentic compliance pipeline, built to answer a question that matters more as agents get autonomy: **when a model decides which tools to call and which agents to delegate to, what stops it doing something it shouldn't?**

Two AI agents across two clouds screen a customer for KYC and AML risk. Every tool call and every agent-to-agent handoff passes an authorisation check before it executes. Synthetic data only.

---

## What it does

A customer profile goes through four stages:

**1. KYC screening.** Google Gemini runs a tool-calling loop against an MCP server hosting seven compliance tools. The model chooses which tools to call, in what order, and when it has enough to stop. Every call is authorised at the server before it executes, and audited whether it is allowed or refused.

**2. AML reasoning.** The KYC agent proves its identity with a signed token and delegates over A2A to Claude on AWS Bedrock, which applies FCA-regulated reasoning and cites UK legislation. In a recorded run this second agent overrode the first, escalating a case the KYC agent had marked for enhanced review, because a document validity failure is a control breach that clean sanctions screening cannot offset. Two models reaching different conclusions, with the reasoning recorded, is the argument for splitting them.

**3. Post-decision workflow.** LangGraph routes the case to Standard Onboarding, Enhanced Due Diligence, or Financial Crime Investigation, triggers SAR consideration where required, and generates a document request list.

**4. Document verification.** Gemini Vision analyses identity document images for quality, field consistency and tamper indicators, behind a guardrail that refuses images appearing to contain real personal data. The agent calls it only when the deterministic format check returns `inconclusive`.

---

## Architecture

Two boundaries, one policy engine.

```
                    +---------------------------+
                    |  google_agent.py          |
                    |  KYC orchestrator         |
                    |  Gemini tool-calling loop |
                    +------+-------------+------+
                           |             |
        BOUNDARY 1         |             |         BOUNDARY 2
        agent -> tool      |             |         agent -> agent
        MCP over stdio     |             |         A2A over HTTP
                           v             v
              +-----------------+   +------------------+
              | mcp_server.py   |   | a2a_server.py    |
              | enforcement     |   | enforcement      |
              +--------+--------+   +---------+--------+
                       |                      |
                       |  same decision call  |
                       +----------+-----------+
                                  v
                       +---------------------+
                       |  policy.py          |
                       |  ABAC, default-deny |
                       +---------------------+
                                  |
              +-------------------+------------------+
              v                                      v
      7 compliance tools                AWS Bedrock AML agent
                                                     |
                                                     v
                                        LangGraph review workflow
                                                     |
                                                     v
                                             Streamlit UI
```

`policy.py` decides. The two servers enforce. Rules are data, not code, which is the seam where a production system drops in OPA or Cedar.

---

## Try it

The authorisation layer runs with no cloud account and no setup:

```bash
pip install -r requirements-agentic.txt
python3 policy.py                          # nine policy decisions
python3 tests/test_authorisation.py        # tool boundary, over real MCP stdio
python3 tests/test_a2a_authorisation.py    # agent boundary, nine attack cases
```

Twelve checks, about thirty seconds, no credentials required. The A2A tests generate ephemeral keys in memory.

The full pipeline needs Google Cloud for Gemini and AWS for Bedrock:

```bash
gcloud auth application-default login
python3 a2a_server.py           # terminal one
python3 orchestrator_a2a.py     # terminal two
streamlit run app.py            # web interface
```

---

## What the authorisation layer stops

| Attempt | Control | Result |
|---|---|---|
| Call a tool the caller was never granted | Default-deny allowlist | Refused |
| Read a document image from outside `data/` | Argument condition, resolved with `realpath` | Refused |
| Reach the AML agent with no credential | Bearer token required | 401 |
| Claim another agent's identity | RS256 signature checked against its public key | 401 |
| Reuse a token found later in a log | 60-second expiry | 401 |
| Keep a valid token, swap the customer data | Token bound to a hash of the payload | 401 |
| Send the same token twice | Replay cache on token id | 401 |
| Forward more customer data than the task needs | Data-minimisation rule in policy | 403 |
| Take the A2A server offline to skip the boundary | No in-process fallback exists | Pipeline halts |

Every attempt, permitted or refused, is written to the audit trail before the action runs.

---

## Key design decisions

Full reasoning, diagrams and the follow-up questions are in [DESIGN.md](DESIGN.md).

**Two agents, two clouds.** Gemini handles tool coordination and screening. Claude handles regulatory reasoning, producing more structured, legislation-aware rationale. Splitting them means the second agent can disagree with the first, and that disagreement is recorded rather than averaged away.

**MCP for tools.** Six Python functions in one process do not need a protocol, and at this size it is overhead. What it buys is a single inspectable boundary that every tool call must cross, whichever agent is calling, plus schema-based discovery so the model selects tools rather than the control flow being hard-coded.

**Enforcement lives with the resource.** `policy.py` decides; the MCP server and the A2A server enforce. The authoritative check runs next to the thing being protected, because an agent cannot be trusted to police itself. The agent runs the same policy locally, but only to skip a doomed round trip.

**Default is deny.** A tool or action with no matching rule is refused. Add an eighth tool and forget the rule, and it is inert rather than open.

**Attributes, not just roles.** The vision tool's rule constrains its arguments as well as its caller. The delegation rule permits only the assessment fields to cross to the AML agent, so a caller cannot quietly widen what it forwards. Permitted calls carry obligations, such as a mandatory PII scan and no image retention, which enforcement actions rather than recommends.

**Identity is proved, not asserted.** The A2A endpoint used to read the caller's name from a field in the request body. Callers now present a short-lived RS256-signed token bound to a hash of the payload; the receiver derives identity from the verified token and ignores the body. Asymmetric signing means the receiver holds only a public key and cannot mint tokens impersonating the sender.

**A tool-calling loop, not a script.** Gemini receives the tool schemas and decides the sequence. The SDK's automatic function calling is disabled so no call can bypass enforcement, and a step budget bounds the loop.

**Facts from tools, judgement from the model.** Factual fields in an assessment come from recorded tool results, not from the model's restatement of them. The model contributes the recommendation and reasoning. The tool trace shows what it actually called.

**Mandatory auditing sits at the enforcement point.** The model may call the audit tool for business events and does so inconsistently. The compliance record does not depend on it: enforcement writes an entry for every call before the call runs.

**The pipeline fails closed.** If the AML agent is unreachable, the pipeline halts rather than calling it in-process. The fallback that used to exist bypassed the token, the policy check and the audit entry whenever the network failed. Losing availability is the accepted cost of not producing an unauthorised compliance decision.

---

## Prototype scope

A prototype, and a few things are deliberately simpler than production would require.

**Identity binding.** Over stdio the calling principal is passed in the spawn environment; over A2A, keys are generated locally with no rotation. In production both would come from the platform: a workload identity token or an OAuth 2.1 client-credentials grant, with public keys discovered through JWKS. The enforcement code would not change, only how the principal is resolved.

**Replay protection is in-process,** so it does not survive a restart or work across replicas. Production needs shared state.

**Discovery is not filtered per principal.** Execution is gated; listing is not, so every identity sees all seven tools.

**Delegation chains are not modelled.** If the AML agent called a third agent, nothing would carry the fact that it is acting on behalf of the original case.

**Model-driven control flow costs latency.** A standard assessment takes eight to ten seconds against two for the old fixed pipeline, and one that escalates to vision analysis takes around fifty-five. For a high-volume onboarding flow the policy decision would be cached and the vision path made asynchronous.

---

## Tech stack

| Component | Technology |
|---|---|
| KYC orchestration | Google Gemini 2.5 Flash via Vertex AI |
| AML reasoning | AWS Bedrock Claude Haiku 4.5 (eu-west-2) |
| Document vision | Google Gemini 2.5 Flash Vision |
| Post-decision workflow | LangGraph |
| Tool protocol | MCP, official `mcp` SDK, stdio transport |
| Agent protocol | A2A over HTTP, with capability cards |
| Authorisation | ABAC, default-deny, one decision point across both boundaries |
| Agent identity | RS256-signed workload tokens, payload-bound, replay-protected |
| Observability | OpenTelemetry traces and structured logs to Cloud Trace and Cloud Logging |
| Frontend | Streamlit |
| Language | Python 3.13 |

---

## Project structure

```
kyc-aml-multiagent/
├── google_agent.py          # KYC tool-calling loop, MCP client
├── mcp_server.py            # MCP server over stdio, enforcement point
├── policy.py                # Policy decision point, ABAC with default-deny
├── a2a_identity.py          # Workload identity for the agent boundary
├── a2a_server.py            # A2A task server, enforcement point
├── aws_agent.py             # AML reasoning via Bedrock Claude
├── orchestrator_a2a.py      # Full pipeline over A2A
├── review_graph.py          # LangGraph post-decision workflow
├── document_analyser.py     # Gemini Vision document verification
├── observability.py         # OpenTelemetry tracing and structured logging
├── app.py                   # Streamlit UI
├── DESIGN.md                # Why the authorisation layer is built this way
├── tests/
│   ├── test_authorisation.py        # tool boundary, over real MCP
│   └── test_a2a_authorisation.py    # agent boundary, nine attack cases
├── tools/tools.py           # 6 compliance tools
└── data/                    # synthetic profiles, mock watchlist, specimen images
```

---

## Important

Synthetic data only. Never use with real customer data or genuine identity documents. Not intended for production deployment.

Built by Ravi Teja Mandapati. Working on agent platforms in financial services.
