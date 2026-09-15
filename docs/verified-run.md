# Verified run

Evidence from a full pipeline execution: three synthetic customers, both clouds,
every hop authenticated, authorised and audited. The case files this run
produced are in [sample-outputs/](sample-outputs/).

Reproduce it with `python3 a2a_server.py` in one terminal and
`python3 orchestrator_a2a.py` in another. The authorisation layer alone can be
verified with no cloud account at all: see [Try it](../README.md#try-it).

---

## Outcomes

| Customer | Document | PEP | KYC (Gemini) | AML (Claude) | Routed to | SLA |
|---|---|---|---|---|---|---|
| James Harrington | valid | hit | ESCALATE | ESCALATE | Financial Crime Investigation | 4h |
| Sarah Johnson | invalid | clear | ENHANCED REVIEW | ENHANCED REVIEW | Enhanced Due Diligence | 24h |
| Emma Williams | valid | clear | APPROVE | APPROVE | Standard Onboarding | 72h |

Harrington triggers SAR consideration under POCA 2002 and account opening is
suspended. Johnson's invalid document blocks customer due diligence, so a
replacement document is requested before onboarding proceeds. Williams clears
every check.

---

## The tool boundary

Each KYC assessment runs as a model-driven loop. Gemini chooses the tools and
the order; the `_tool_trace` in each result records what it actually called, and
the trace varies between customers because the model, not the code, decides.
Every call is authorised at the MCP server before it executes:

```
[CLOUD TRACE] kyc-gemini-agent span completed in 8266ms
```

A representative call from the trace, showing the authorisation verdict carried
alongside the result:

```json
{
  "tool": "verify_document",
  "args": { "doc_type": "driving_licence", "doc_number": "SJ123" },
  "authorised": true,
  "result": {
    "status": "invalid",
    "doc_valid": false,
    "reason": "Document number too short for driving_licence"
  }
}
```

---

## The agent boundary

The KYC agent discovers how to authenticate from the receiving agent's
capability card, then presents a signed token bound to the request payload:

```
[ORCHESTRATOR] A2A server is healthy.
[ORCHESTRATOR] Agent discovered: aml-reasoning-claude
[ORCHESTRATOR] Auth scheme required: bearer_jwt (RS256), scope 'aml_assessment'
[ORCHESTRATOR] Sending task via A2A protocol...
[ORCHESTRATOR] A2A task status: completed
[ORCHESTRATOR] Authorised by rule: a2a-aml-delegation
```

On the receiving side, identity comes from the verified token rather than
anything in the request body:

```
[A2A SERVER] Task received: d980fec1-50bd-4588-bb88-587da157fca5
  From (verified): kyc-orchestrator-gemini
  Authorised by rule: a2a-aml-delegation
  Customer: James Harrington
```

---

## Cross-cloud reasoning

The AML agent forms its own determination from the deterministic screening
results rather than deferring to the KYC stage. Harrington's rationale, produced
by Claude on Bedrock:

> The customer presents a confirmed PEP hit linked to a financial fraud
> investigation, which constitutes a mandatory trigger for enhanced due
> diligence under MLR 2017 and requires escalation under POCA 2002 suspicious
> activity reporting obligations.

Every regulatory reference in this run came from the permitted set, and the
citation validator raised no warnings.

---

## Failure behaviour, observed

Two faults occurred during testing and the pipeline halted both times rather
than continuing without the AML stage:

```
[ORCHESTRATOR] AML agent unavailable. Halting: this pipeline does not run the
AML stage outside the authorised A2A path.
```

The first was an AWS account verification delay, the second a port collision
from a stale server process. In both cases the KYC assessment completed and the
pipeline stopped at the boundary, because the in-process fallback that would
have bypassed the token, the policy check and the audit entry no longer exists.

---

## Audit trail

Every tool call and every delegation is written before the action runs,
permitted or refused:

```
EVENT: TOOL_CALL_AUTHORISED | DATA: {'principal': 'agent://kyc-orchestrator',
  'role': 'kyc_orchestrator', 'tool': 'audit_logger', 'rule': 'kyc-core-tools'}
EVENT: TOOL_CALL_DENIED | DATA: {'principal': 'agent://read_only_reviewer',
  'role': 'read_only_reviewer', 'tool': 'audit_logger', 'rule': None,
  'reason': "no rule grants role 'read_only_reviewer' access to tool 'audit_logger'"}
EVENT: A2A_TASK_AUTHORISED | DATA: {'principal': 'kyc-orchestrator-gemini',
  'action': 'a2a:aml_assessment', 'rule': 'a2a-aml-delegation'}
```

Timings: roughly eight to ten seconds for a KYC assessment, two to six for AML
reasoning, and around fifty-five seconds when a document escalates to vision
analysis.
