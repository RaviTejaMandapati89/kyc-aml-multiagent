# KYC/AML Multi-Agent Compliance System

A prototype agentic compliance pipeline. Two AI agents across two clouds screen a customer for KYC and AML risk, and every tool call and agent-to-agent handoff is authorised before it runs.

Built to work through a specific problem: once a model decides which tools to call and which agents to delegate to, the authorisation question moves from design time to run time. Synthetic data only.

---

## What it does

A customer profile goes through four stages:

**1. KYC screening.** Google Gemini runs a tool-calling loop against an MCP server hosting seven compliance tools. The model chooses which tools to call, in what order, and when to stop. Every call is authorised at the server before it executes, and audited whether allowed or refused.

**2. AML reasoning.** The KYC agent proves its identity with a signed token and delegates over A2A to Claude on AWS Bedrock, which applies FCA-regulated reasoning and cites UK legislation.

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

`policy.py` decides. The two servers enforce. Rules are held as data rather than code, which is where OPA or Cedar would drop in.

---

## Try it

The authorisation layer runs with no cloud account and no setup:

```bash
pip install -r requirements-agentic.txt
python3 policy.py                          # nine policy decisions
python3 tests/test_authorisation.py        # tool boundary, over real MCP stdio
python3 tests/test_a2a_authorisation.py    # agent boundary, nine attack cases
python3 tests/test_citation_control.py     # regulatory citation validation
```

Nineteen checks, about thirty seconds. The A2A tests generate ephemeral keys in memory.

A record of a full run, with the case files it produced, is in [docs/verified-run.md](docs/verified-run.md).

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
| Cite a regulatory instrument outside the permitted set | Allowlist, validated on output | Flagged on the result |

Every attempt, permitted or refused, is written to the audit trail before the action runs.

---

## Key design decisions

Fuller reasoning, diagrams and the alternatives considered are in [DESIGN.md](DESIGN.md).

**Two agents, two clouds.** Gemini handles tool coordination and screening. Claude handles regulatory reasoning, producing more structured, legislation-aware rationale. Splitting them lets the second agent disagree with the first, and that disagreement is recorded.

**MCP for tools.** Six Python functions in one process do not need a protocol, and at this size it is overhead. What it buys is a single boundary that every tool call crosses regardless of which agent is calling, plus schema-based discovery so the model selects tools rather than the control flow being hard-coded.

**Enforcement lives with the resource.** `policy.py` decides; the MCP server and the A2A server enforce. The authoritative check runs next to the thing being protected, so a bug or a prompt injection in the agent cannot skip it. The agent runs the same policy locally, but only to avoid a round trip it knows will be refused.

**Default is deny.** A tool or action with no matching rule is refused, so a tool added without a rule does nothing until one is written.

**Attributes, not just roles.** The vision tool's rule constrains its arguments as well as its caller: an image path must resolve inside `data/`, checked with `realpath` so `../` sequences and symlinks cannot escape. The delegation rule permits only the assessment fields to reach the AML agent. Permitted calls carry obligations, such as a mandatory PII scan and no image retention, which enforcement carries out.

**Identity is proved, not asserted.** The A2A endpoint used to read the caller's name from a field in the request body. Callers now present a short-lived RS256-signed token bound to a hash of the payload, and the receiver derives identity from the verified token. Asymmetric signing means the receiver holds only a public key and cannot mint tokens as the sender.

**A tool-calling loop, not a script.** Gemini receives the tool schemas and decides the sequence. The SDK's automatic function calling is disabled so that tool execution goes through the enforcement point, and a step budget bounds the loop.

**Facts from tools, judgement from the model.** Factual fields in an assessment come from recorded tool results rather than the model's restatement of them. The model contributes the recommendation and its reasoning. The tool trace records what it actually called.

**The KYC model's prose does not cross to the AML agent.** Only the deterministic findings and the recommendation label are forwarded. When the written reason was included, the AML agent followed its wording: the same customer returned ESCALATE in one run and ENHANCED REVIEW in another, changing the assigned team and whether the account was suspended.

**Both models run at temperature zero,** so a determination can be reproduced.

**Citations are drawn from a permitted set.** The prompt supplies the instruments the model may reference and the output is checked against that list, with anything outside it recorded on the result. This bounds the range rather than verifying correctness; the list would be owned by a compliance SME.

**Mandatory auditing sits at the enforcement point.** The model may call the audit tool for business events and does so inconsistently between runs. The compliance record does not rely on it: enforcement writes an entry for every call before the call runs.

**The pipeline halts if the AML agent is unreachable.** An earlier fallback called the AML function in-process, which skipped the token, the policy check and the audit entry whenever the server was down. Availability is traded for not producing an unauthorised determination.

---

## Prototype scope

Simpler than production in several places.

**Identity binding.** Over stdio the calling principal is passed in the spawn environment. Over A2A, keys are generated locally with no rotation or revocation. In production both come from the platform: a workload identity token or an OAuth 2.1 client-credentials grant, with public keys discovered through JWKS. The enforcement code is unchanged; only the resolution of the principal differs.

**Replay protection is in-process,** so it does not survive a restart or work across replicas.

**Discovery is not filtered per principal.** Execution is gated; listing is not, so every identity sees all seven tools.

**Delegation chains are not modelled.** If the AML agent called a third agent, nothing would carry the fact that it is acting on behalf of the original case.

**No rate limiting** on either boundary.

**A2A runs over plain HTTP on localhost,** with TLS assumed to terminate elsewhere.

**Latency.** A KYC assessment takes roughly eight to ten seconds against two for the earlier fixed pipeline, and around fifty-five when a document escalates to vision analysis.

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
├── DESIGN.md                # Design reasoning and alternatives considered
├── docs/verified-run.md     # Record of a full pipeline execution
├── tests/
│   ├── test_authorisation.py        # tool boundary, over real MCP
│   ├── test_a2a_authorisation.py    # agent boundary, nine attack cases
│   └── test_citation_control.py     # regulatory citation validation
├── tools/tools.py           # 6 compliance tools
└── data/                    # synthetic profiles, mock watchlist, specimen images
```

---

## Important

Synthetic data only. Not for use with real customer data or genuine identity documents, and not intended for production deployment.

Built by Ravi Teja Mandapati. Working on agent platforms in financial services.
