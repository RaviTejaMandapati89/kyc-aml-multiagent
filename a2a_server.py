import sys
import os
import json
import uuid
from datetime import datetime
from flask import Flask, request, jsonify

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from aws_agent import run_aml_assessment

app = Flask(__name__)

# ── In-memory task store ──────────────────────────────────────────────────────
tasks = {}


# ── Agent Card endpoint ───────────────────────────────────────────────────────

@app.route('/agent-card', methods=['GET'])
def agent_card():
    card_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'a2a', 'agent_card_aws.json'
    )
    with open(card_path) as f:
        return jsonify(json.load(f))


# ── Task endpoint ─────────────────────────────────────────────────────────────

@app.route('/tasks', methods=['POST'])
def receive_task():
    data = request.get_json()

    if not data:
        return jsonify({"error": "No payload provided"}), 400

    required_fields = ["sender_agent_id", "payload"]
    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"Missing required field: {field}"}), 400

    task_id = str(uuid.uuid4())
    task = {
        "task_id": task_id,
        "sender_agent_id": data["sender_agent_id"],
        "receiver_agent_id": "aml-reasoning-claude",
        "created_at": datetime.utcnow().isoformat(),
        "status": "running",
        "payload": data["payload"],
        "result": None,
        "error": None
    }

    tasks[task_id] = task

    print(f"\n[A2A SERVER] Task received: {task_id}")
    print(f"  From: {data['sender_agent_id']}")
    print(f"  Customer: {data['payload'].get('customer_name', 'unknown')}")

    try:
        result = run_aml_assessment(data["payload"])
        task["status"] = "completed"
        task["result"] = result
        print(f"[A2A SERVER] Task completed: {task_id}")
        print(f"  Recommendation: {result.get('aml_recommendation')}")

    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        print(f"[A2A SERVER] Task failed: {task_id} — {str(e)}")
        return jsonify(task), 500

    return jsonify(task), 200


# ── Task status endpoint ──────────────────────────────────────────────────────

@app.route('/tasks/<task_id>', methods=['GET'])
def get_task(task_id):
    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(tasks[task_id])


# ── Health check ──────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "agent_id": "aml-reasoning-claude",
        "tasks_processed": len(tasks)
    })


if __name__ == '__main__':
    print("A2A Server starting — AML Reasoning Agent")
    print("Listening on http://localhost:5001")
    app.run(host='0.0.0.0', port=5001, debug=False)
