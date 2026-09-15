# Design notes: authorisation in an agentic system

Why this system is built the way it is. Written to be read start to finish, and
to answer "why did you do it that way?" for each decision.

---

## 1. The thesis

An agentic system has two places where something can go wrong: an agent calling
a tool, and an agent calling another agent. Both are the same question, *may this
principal perform this action on this resource?*, so both are answered by the
same policy engine rather than by ad-hoc checks in two places.

A second thesis runs alongside it, and most of the defects found while building
this were violations of it: **facts come from tools, judgement comes from the
model, and one model's judgement is never another model's fact.**

---

## 2. The two boundaries

```
                    +---------------------------+
                    |  google_agent.py          |
                    |  KYC orchestrator         |
                    +------+-------------+------+
                           |             |
        BOUNDARY 1         |             |         BOUNDARY 2
        agent -> tool      |             |         agent -> agent
        (MCP, stdio)       |             |         (A2A, HTTP)
                           v             v
              +-----------------+   +------------------+
              | mcp_server.py   |   | a2a_server.py    |
              | PEP #1          |   | PEP #2           |
              +--------+--------+   +---------+--------+
                       |                      |
                       |   both ask the same  |
                       +----------+-----------+
                                  v
                       +---------------------+
                       |  policy.py          |
                       |  PDP                |
                       |  default-deny ABAC  |
                       +---------------------+
```

**PDP**, Policy Decision Point: decides, and does nothing else.
**PEP**, Policy Enforcement Point: asks the PDP and obeys the answer.
**PAP**, Policy Administration Point: the rules themselves, kept as data.

Separating them means policy can be reviewed, tested and changed without
touching enforcement code, and enforcement can be added at a new boundary
without reimplementing the rules. When the A2A boundary was added later, no
policy logic was rewritten: a rule was added and a second enforcement point
called the same function.

---

## 3. Decision log

Every significant choice, the alternative, and what it cost.

| Decision | Alternative | Why | Cost |
|---|---|---|---|
| MCP server in front of six local functions | Direct Python imports | One inspectable boundary every call must cross; schema discovery so the model selects tools | Overhead at this size; a process boundary per call |
| Enforcement server-side | Check inside the agent | An agent cannot police itself; a bug or injection bypasses a client-side check | An extra round trip, mitigated by an advisory client-side pre-check |
| Default deny | Default allow with a denylist | A tool added without a rule is inert rather than open | Every new tool needs a rule before it works |
| ABAC with argument conditions | Role-based access alone | Authorising on *what* is touched, not only who asks; a path must resolve inside `data/` | Rules are longer and need testing |
| Obligations attached to permits | Checks scattered in tool code | A permission can carry conditions (PII scan, no retention) that enforcement actions | Enforcement must understand each obligation |
| Automatic function calling disabled | Let the SDK execute tools | The convenient path routed around the enforcement point | Manual dispatch loop to write and maintain |
| Step budget on the loop | Unbounded | A model-driven loop can loop | A complex case could hit the ceiling |
| RS256 tokens for A2A | Shared secret (HS256) | The receiver holds only a public key and cannot mint tokens impersonating the sender | Key generation and distribution |
| Token bound to a payload hash | Plain bearer token | A captured token is otherwise valid against any payload for its lifetime | Sender and receiver must hash canonically |
| 60s expiry plus replay cache | Long-lived tokens | A leaked token is worth at most one request | Clock skew handling; cache is in-process |
| Audience checked | Issuer and signature only | A token minted for one agent cannot be replayed against another | One more claim to get right |
| Agent registry maps identity to role | Role inside the token | The caller does not assert its own privileges | A registry to maintain |
| Data minimisation in policy | Convention and code review | The receiving agent gets what its task needs; enforced, not agreed | The allowlist must track the schema |
| Pipeline halts if the AML agent is unreachable | In-process fallback | A network error must not disable the boundary | Availability |
| Three-state document check | Boolean valid/invalid | A format check can genuinely fail to settle the question; gives the agent a principled reason to escalate | Callers must handle a third state |
| Citations constrained and validated | Trust the model | An invented citation in a compliance tool reads as authoritative | The allowlist needs a compliance owner |
| Both models at temperature zero | Default sampling | A determination that changes between runs cannot be reproduced in an audit | Less variety in phrasing |
| Only tool findings cross the agent boundary | Forward the KYC model's reasoning too | One model's prose was steering another's regulatory determination | The second agent has less context and must reason from findings |

---

## 4. Boundary 1: agent to tool

### What was wrong before

`google_agent.py` imported six functions and called them in a fixed order.
Nothing checked whether a call was permitted, because in a single script the
question does not arise. As soon as a model chooses the calls, it does.

### What it does now

Every tool call travels over MCP to `mcp_server.py`, whose single `call_tool`
handler is the only path to any tool. That handler asks `policy.evaluate` before
executing anything, and writes an audit entry for every attempt, allowed or
denied, before the tool runs.

### Decisions worth defending

**Enforcement lives with the resource, not the caller.** The agent also runs the
policy check locally, but only to avoid a pointless round trip. If the agent's
check were the only one, a bug, a prompt injection, or a modified client would
bypass it entirely.

**Default is deny.** A tool with no matching rule is refused. Failing closed is
the correct posture when the resources are sanctions screening and identity
documents.

**Authorisation looks at arguments.** The vision tool's rule requires
`image_path` to resolve inside `data/`, checked with `realpath` so `../`
sequences and symlinks cannot escape. This is the difference between "who are
you" and "who are you, what are you touching, under what conditions".

**Permitted calls carry obligations.** The vision rule attaches
`pii_scan_required` and `no_image_retention`, which the enforcement point
actions. An obligation is a condition of the permission, not advice.

**Mandatory auditing does not depend on the model.** The model may call the audit
tool for business events, and does so inconsistently: one run logged after every
step, another logged nothing. That inconsistency does not matter, because the
compliance record is written by the enforcement point before each call runs.

### The three-state document check

`verify_document` returns `valid`, `invalid` or `inconclusive` rather than a
boolean. A number of the expected length whose body contains letters where
digits belong is more likely a transcription or OCR artefact, a capital O read
for a zero, than a forgery, and a length check cannot tell those apart. Rather
than guess, the tool declines to decide.

That third state is what gives the agent a principled reason to escalate to
vision analysis, instead of the judgement living only in prompt wording. The
boolean `doc_valid` is retained for callers that need one and is `False` when
inconclusive, so an unresolved document fails closed.

Observed: on a customer whose passport number contained a capital O, the model
called `verify_document`, received `inconclusive`, and its next call was
`analyse_id_document` with the image path from the customer record. It did not
call vision for any customer whose check returned a definite answer.

---

## 5. Boundary 2: agent to agent

### What was wrong before

```
POST /tasks
{
  "sender_agent_id": "kyc-orchestrator-gemini",     <-- just a string
  "payload": { ...customer data... }
}
```

The receiving agent read the caller's identity out of the request body and ran a
Bedrock inference. Anything that could reach the port could claim to be the KYC
orchestrator. Identity was **asserted**, not **proved**, and nothing checked
whether that caller was allowed to delegate this kind of work. The server binds
to all interfaces, so in practice anything on the same network could have done it.

### What it does now

```mermaid
sequenceDiagram
    participant K as KYC agent
    participant A as A2A server
    participant P as policy.py (PDP)
    participant B as AWS Bedrock

    Note over K: hash the payload<br/>mint a 60s token signed<br/>with its private key
    K->>A: POST /tasks + Authorization: Bearer <jwt>
    Note over A: 1. verify signature with<br/>sender's public key
    Note over A: 2. check audience, expiry, scope
    Note over A: 3. check payload hash matches<br/>the body received
    Note over A: 4. check token id not seen before
    A->>P: may kyc-orchestrator-gemini<br/>do a2a:aml_assessment<br/>with this payload?
    P-->>A: permit / deny + obligations
    alt permitted
        A->>B: run AML assessment
        B-->>A: result
        A-->>K: 200 + task record
    else refused
        A-->>K: 401 or 403 (audited)
    end
```

### Decisions worth defending

**Prove identity, don't read it.** The receiver derives identity from the
verified token and ignores `sender_agent_id` in the body. That field is still
recorded, so a mismatch between claimed and proved identity appears in the audit
log.

**Asymmetric, not shared secret.** With a shared secret, anyone who can verify a
token can mint one, so the receiver would hold a key capable of impersonating
the sender. With a keypair, compromising the receiver does not forge the
sender's identity.

**The algorithm is pinned.** Verification specifies `algorithms=["RS256"]`. Left
open, a token claiming `alg: none` or `alg: HS256` can trick some libraries into
skipping verification or treating a public key as a shared secret. Known attack
class, one-argument fix.

**The token is bound to the payload.** Without binding, a token captured from a
log or the network is valid against *any* body for its lifetime, so an attacker
could keep the token and substitute a different customer's data. The `pbh` claim
carries a SHA-256 of the canonical JSON body.

**Short expiry plus replay cache.** Expiry bounds how long a leaked token is
useful; recording the `jti` means it cannot be used twice inside that window.
Together they reduce a captured credential to at most one lost request.

**Audience is checked,** so a token minted for one agent cannot be replayed
against another that trusts the same signer.

**Identity resolves to a role through a registry.** An agent absent from the
registry gets a role no rule grants anything to, so it is refused by default
rather than raising. The caller never asserts its own privileges.

**Data minimisation is an authorisation decision.** The delegation rule permits
only the assessment fields to cross. Attach the customer's address, income or
document image and the request is refused with a 403. The receiving agent gets
what its task needs, enforced rather than agreed in review.

**Task reads are authenticated too,** and only the submitting agent may read its
own task. A UUID is unguessable, but an unguessable identifier is not an access
control.

**Health and agent-card stay open, deliberately.** A capability card is how
another agent discovers how to authenticate, so it cannot itself require
authentication. Neither endpoint discloses customer data.

**Auth errors are terse to the caller, detailed in the log.** Telling an attacker
which check failed helps them tune the next attempt.

**The pipeline fails closed.** The orchestrator previously fell back to calling
the AML function in-process if the server was unreachable: no token, no policy
check, no audit entry. The fallback is gone, and so is the import that made it
possible, so the capability no longer exists in that file rather than merely
being unused. A refused delegation is terminal for the same reason: continuing
with an empty AML result would produce a compliance decision with a hole where
the reasoning should be.

---

## 6. Model outputs as a control surface

Three defects, all found by running the system rather than reading it, all the
same underlying mistake: treating model output as if it were data.

### Fabricated citations

Asked for "compliance-grade rationale", the AML agent volunteered regulatory
references unprompted. One run cited "FCA BCBS guidelines", conflating the
Financial Conduct Authority with the Basel Committee on Banking Supervision.
That is not an instrument. In a compliance tool an invented citation is worse
than none: it reads as authoritative and someone may act on it.

Two controls, because a prompt instruction is guidance rather than enforcement.
The prompt now supplies the permitted instruments and forbids others, and the
output is validated against that set, with anything outside it recorded on the
result rather than passed through silently. The validator reports rather than
rewrites: silently deleting a fabricated citation would hide the fact that the
model produced one, and that signal is worth keeping.

The first version of the validator was wrong. It flagged any capitalised token,
which fired on the model's own decision labels ("ENHANCED REVIEW") and produced
warnings on correct output. A control that raises false alarms trains people to
ignore it, which is worse than having none. It now requires citation context: an
acronym next to words like "under", "pursuant to", "guidelines", "requirements".

### Non-deterministic determinations

The same customer produced different outcomes across runs: ESCALATE in one,
ENHANCED REVIEW in the next, changing the assigned team and whether the account
was suspended. A compliance decision that depends on when it was run cannot be
reproduced in an audit.

The first hypothesis was sampling temperature. The KYC agent was pinned at zero;
the AML agent was left at the default. Pinning it did not fix the flip, which
ruled the hypothesis out rather than confirming it.

### One model's prose steering another's decision

The real cause. The AML agent received `gemini_reason` and `additional_flags`,
free text the KYC model writes fresh each run, and followed their wording. A run
whose reason happened to contain the word "escalate" produced an ESCALATE
determination; the same customer described as "requiring human review" came back
ENHANCED REVIEW.

Only the deterministic tool findings and the KYC recommendation label now cross
the boundary, with an explicit instruction not to defer to the label, and the
data-minimisation rule refuses the prose fields if anyone adds them back. This
is the same principle already applied a stage earlier, where factual fields in
an assessment come from recorded tool results rather than the model's
restatement of them.

### Artefacts disagreeing with each other

The AML agent can shorten the SLA: four hours on an escalation against the KYC
default of seventy-two. The shortened value reached the pipeline result but not
the LangGraph workflow, so the saved case file told the investigating team
seventy-two hours for a case whose actual deadline was four. Two documents from
one run disagreeing on a deadline is itself a compliance problem. The binding
SLA now propagates to both.

---

## 7. What each control stops

| Attack | Control | Result |
|---|---|---|
| Call a tool the caller was never granted | Default-deny allowlist | Refused |
| Read a document image from outside `data/` | Argument condition via `realpath` | Refused |
| Reach the AML agent with no credential | Bearer token required | 401 |
| Claim to be the KYC agent, sign with your own key | Signature checked against the real agent's public key | 401 |
| Sign correctly as an agent nobody knows | No public key on file for that issuer | 401 |
| Reuse a token found in a log an hour later | 60-second expiry | 401 |
| Reuse a token minted for a different agent | Audience check | 401 |
| Keep a valid token, swap in another customer's data | Payload binding (`pbh`) | 401 |
| Capture a token in flight and send it again | Replay cache (`jti`) | 401 |
| Genuine agent asking for a different action | Scope check | 403 |
| Genuine agent forwarding the customer's full record | Data-minimisation condition | 403 |
| Genuine agent reading another agent's task | Submitter check on `GET /tasks/<id>` | 403 |
| Take the A2A server offline to skip the boundary | No in-process fallback exists | Pipeline halts |
| Cite a regulatory instrument that does not exist | Allowlist plus output validation | Flagged on the result |

All covered by `tests/test_authorisation.py`, `tests/test_a2a_authorisation.py`
and `tests/test_citation_control.py`, which generate ephemeral keys in memory and
need no cloud credentials.

---

## 8. Questions you are likely to be asked

**"Isn't a JWT overkill for two local processes?"**
For two local processes, yes. The interesting version of this system has many
agents, some operated by other teams, and at that point "the caller told us who
it was" is not a control. The pattern matters; the transport is incidental.

**"Where do the keys come from?"**
Generated locally on first run into a gitignored directory. No private key is
committed, and the tests generate their own in memory so a clean clone passes
with no setup. In production the calling workload's identity comes from the
platform: a Kubernetes service account token, cloud workload identity, or an
OAuth 2.1 client-credentials grant, with public keys discovered through JWKS.

**"Why not mTLS?"**
mTLS authenticates the transport, which is the right layer for machine-to-machine
trust and would be a sensible addition. It does not carry the scope of a request
or bind to a payload, so a token does work a certificate does not. In practice
you use both: mTLS for the channel, a token for the request.

**"Your replay cache is in-process. What happens with three replicas?"**
It breaks: a token rejected by one replica would be accepted by another. Shared
state, Redis or a uniqueness constraint on `jti`, is the production answer.

**"You disabled the SDK's automatic function calling. Why make life harder?"**
Because the convenient path executed tools without passing through the
enforcement point. A control a library can silently route around is not a control.

**"The model didn't always call the audit tool. Isn't that a compliance problem?"**
It would be if the compliance record depended on it. It does not: the enforcement
point writes an entry for every call, authorised or denied, before the call runs.

**"What happens if the AML agent is down?"**
The pipeline stops and reports which stage failed. It used to fall back to an
in-process call, which bypassed authorisation whenever the server was
unreachable. Availability is the real cost of failing closed, and the right
trade for a compliance decision: no answer beats an unauthorised one.

**"How do you know the citation control works?"**
It caught the reference that prompted it, and it catches instruments it has
never seen, which is the harder case. It does not make a citation correct. It
bounds what the model can draw from and surfaces what escapes, and the allowlist
would be owned by a compliance SME rather than by me.

**"What is the cost of all this?"**
Latency and complexity. A standard assessment takes eight to ten seconds against
two for the old fixed pipeline; one escalating to vision takes around
fifty-five. Every tool call crosses a process boundary and a policy check. For a
prototype demonstrating governance that is the right trade; for a high-volume
onboarding flow the policy decision would be cached and the vision path made
asynchronous.

**"What would you do differently with more time?"**
Filter tool discovery per principal, model delegation chains so a third agent
inherits the context of the original case, move the replay cache to shared
state, and add rate limiting. None of those change the architecture; they are
the production versions of choices deliberately simplified here.

---

## 9. Prototype scope

Deliberately simpler than production would require.

- **Identity binding.** Over stdio the principal is passed in the spawn
  environment; over A2A, keys are generated locally with no rotation or
  revocation. Production uses platform-issued workload identity and JWKS. The
  enforcement code does not change, only how the principal is resolved.
- **Replay protection is in-process,** so it does not survive a restart or work
  across replicas.
- **Discovery is not filtered per principal.** Execution is gated; listing is
  not, so every identity sees all seven tools.
- **Delegation chains are not modelled.** If the AML agent called a third agent,
  nothing would carry the fact that it is acting on behalf of the original case.
- **No rate limiting** on either boundary.
- **The A2A transport is plain HTTP on localhost,** with TLS assumed to be
  terminated elsewhere.
