"""
a2a_server.py - A2A task server for the AML reasoning agent, and the second
Policy Enforcement Point in the system.

Before: the task endpoint read the caller's identity from a field in the request
body and ran a Bedrock inference on whatever was attached. Identity was asserted,
not proved, and nothing checked whether that caller was permitted to delegate
this kind of work.

Now every task request passes through the same three stages as a tool call:

    authenticate  ->  authorise  ->  execute

Authentication (a2a_identity) establishes WHO is calling, cryptographically.
Authorisation (policy.evaluate) decides WHETHER that principal may invoke this
action with this payload. Only then does the task run. The identity in the
request body is ignored entirely; it is kept in the stored task record only so
that a mismatch between what a caller claimed and what it proved is visible in
the audit trail.

The important structural point: this file and mcp_server.py are different
transports guarding different resources, but they call the SAME policy decision
function. Agent-to-tool and agent-to-agent are the same question asked about
different things, so they should not have two different answers.
"""

import sys
import os
import json
import uuid
from datetime import datetime, timezone
from flask import Flask, request, jsonify

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))

from aws_agent import run_aml_assessment
from tools import audit_logger
import policy
import a2a_identity

app = Flask(__name__)

THIS_AGENT_ID = "aml-reasoning-claude"
REQUIRED_SCOPE = "aml_assessment"          # the only action this endpoint offers
ACTION = f"a2a:{REQUIRED_SCOPE}"           # how policy names it

tasks = {}
replay_cache = a2a_identity.ReplayCache()


def _audit(event: str, data: dict) -> None:
    audit_logger(event, data)


# ── Agent Card ────────────────────────────────────────────────────────────────

@app.route('/agent-card', methods=['GET'])
def agent_card():
    """Public metadata. Deliberately unauthenticated: a capability card is how
    another agent discovers how to talk to this one, including how to
    authenticate. It advertises no customer data."""
    card_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'a2a', 'agent_card_aws.json'
    )
    with open(card_path) as f:
        return jsonify(json.load(f))


# ── Task endpoint ─────────────────────────────────────────────────────────────

@app.route('/tasks', methods=['POST'])
def receive_task():
    data = request.get_json(silent=True)
    if not data or "payload" not in data:
        return jsonify({"error": "Missing required field: payload"}), 400
    payload = data["payload"]
    claimed_id = data.get("sender_agent_id")   # untrusted, recorded only

    # ---- 1. Authenticate --------------------------------------------------
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        _audit("A2A_TASK_UNAUTHENTICATED", {
            "claimed_sender": claimed_id, "reason": "no bearer token presented"})
        return jsonify({"error": "unauthenticated",
                        "detail": "A bearer token is required. See /agent-card."}), 401

    token = auth_header.removeprefix("Bearer ").strip()
    try:
        caller = a2a_identity.verify_token(
            token, expected_audience=THIS_AGENT_ID,
            payload=payload, replay_cache=replay_cache)
    except a2a_identity.IdentityError as exc:
        # Deliberately terse to the caller, detailed in the audit log. Verbose
        # auth errors help an attacker tune their next attempt.
        _audit("A2A_TASK_AUTH_FAILED", {
            "claimed_sender": claimed_id, "reason": str(exc)})
        return jsonify({"error": "unauthenticated"}), 401

    # ---- 2. Authorise -----------------------------------------------------
    if caller.scope != REQUIRED_SCOPE:
        _audit("A2A_TASK_DENIED", {
            "principal": caller.agent_id, "reason": f"token scope '{caller.scope}' "
            f"does not cover '{REQUIRED_SCOPE}'"})
        return jsonify({"error": "forbidden", "reason": "token scope does not "
                        "cover this action"}), 403

    principal = policy.principal_for_agent(caller.agent_id)
    decision = policy.evaluate(principal, ACTION, {"payload": payload})

    _audit("A2A_TASK_AUTHORISED" if decision.allowed else "A2A_TASK_DENIED", {
        "principal": principal.id, "role": principal.role, "action": ACTION,
        "rule": decision.rule_id, "reason": decision.reason,
        "token_id": caller.token_id,
        "claimed_sender": claimed_id,
        "identity_mismatch": bool(claimed_id and claimed_id != caller.agent_id),
    })

    if not decision.allowed:
        return jsonify({"error": "forbidden", "reason": decision.reason}), 403

    # ---- 3. Execute -------------------------------------------------------
    task_id = str(uuid.uuid4())
    task = {
        "task_id": task_id,
        "sender_agent_id": caller.agent_id,        # verified, not claimed
        "receiver_agent_id": THIS_AGENT_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "payload": payload,
        "result": None,
        "error": None,
        "authorisation": {"rule": decision.rule_id,
                          "obligations": list(decision.obligations)},
    }
    tasks[task_id] = task

    print(f"\n[A2A SERVER] Task received: {task_id}")
    print(f"  From (verified): {caller.agent_id}")
    print(f"  Authorised by rule: {decision.rule_id}")
    print(f"  Customer: {payload.get('customer_name', 'unknown')}")

    try:
        result = run_aml_assessment(payload)
        task["status"] = "completed"
        task["result"] = result
        print(f"[A2A SERVER] Task completed: {task_id}")
        print(f"  Recommendation: {result.get('aml_recommendation')}")
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        _audit("A2A_TASK_FAILED", {"task_id": task_id, "error": str(e)})
        print(f"[A2A SERVER] Task failed: {task_id}: {str(e)}")
        return jsonify(task), 500

    return jsonify(task), 200


# ── Task status ───────────────────────────────────────────────────────────────

@app.route('/tasks/<task_id>', methods=['GET'])
def get_task(task_id):
    """Also authenticated. A task record contains the customer assessment, so
    an unauthenticated read here would leak exactly what the POST endpoint
    protects. Task ids are UUIDs, but an unguessable identifier is not an
    access control."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "unauthenticated"}), 401

    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404

    token = auth_header.removeprefix("Bearer ").strip()
    try:
        # The stored payload is what the token must be bound to.
        caller = a2a_identity.verify_token(
            token, expected_audience=THIS_AGENT_ID,
            payload=tasks[task_id]["payload"], replay_cache=replay_cache)
    except a2a_identity.IdentityError:
        return jsonify({"error": "unauthenticated"}), 401

    # A caller may read only the tasks it submitted.
    if caller.agent_id != tasks[task_id]["sender_agent_id"]:
        _audit("A2A_TASK_READ_DENIED", {
            "principal": caller.agent_id, "task_id": task_id,
            "reason": "caller is not the submitting agent"})
        return jsonify({"error": "forbidden"}), 403

    return jsonify(tasks[task_id])


# ── Health ────────────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    """Unauthenticated liveness only. Reports no customer data and no task
    contents; the task count is deliberately the limit of what it discloses."""
    return jsonify({
        "status": "healthy",
        "agent_id": THIS_AGENT_ID,
        "tasks_processed": len(tasks)
    })


if __name__ == '__main__':
    print("A2A Server starting: AML Reasoning Agent")
    print(f"Agent identity: {THIS_AGENT_ID}")
    print("Task endpoint requires a signed bearer token (see /agent-card)")
    print("Listening on http://localhost:5001")
    app.run(host='0.0.0.0', port=5001, debug=False)
