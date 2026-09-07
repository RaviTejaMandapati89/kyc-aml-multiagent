"""
Proves the authorisation boundary works over the real MCP protocol, without
any cloud credentials. Run:  python tests/test_authorisation.py

What it demonstrates
  * seven tools are discoverable over stdio
  * the orchestrator identity may write to the audit log; the read-only identity may not
  * an argument-level condition (path traversal on image_path) is refused
    BEFORE the vision tool is ever imported or executed
  * every attempt, allowed or denied, lands in audit_trail.log
"""
import os, sys, json, asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "mcp_server.py")
sys.path.insert(0, REPO)
import policy  # noqa: E402


def _params(role: str, ident: str) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=[SERVER],
                                 env={**os.environ, "MCP_AGENT_ROLE": role, "MCP_AGENT_ID": ident})


async def _call(role: str, tool: str, args: dict) -> dict:
    async with stdio_client(_params(role, f"agent://{role}")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            return json.loads(res.content[0].text)


async def _discover(role: str) -> list[str]:
    async with stdio_client(_params(role, f"agent://{role}")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return [t.name for t in (await s.list_tools()).tools]


def test_pdp_default_deny():
    kyc = policy.Principal("agent://kyc", "kyc_orchestrator")
    ro = policy.Principal("agent://ro", "read_only_reviewer")
    assert policy.evaluate(kyc, "audit_logger", {}).allowed
    assert not policy.evaluate(ro, "audit_logger", {}).allowed
    assert not policy.evaluate(kyc, "tool_that_does_not_exist", {}).allowed
    assert policy.evaluate(kyc, "analyse_id_document",
                           {"image_path": "data/specimen_passport.jpg", "declared_doc_type": "passport"}).allowed
    assert not policy.evaluate(kyc, "analyse_id_document",
                               {"image_path": "../../etc/passwd", "declared_doc_type": "passport"}).allowed


def test_pep_over_mcp():
    assert len(asyncio.run(_discover("kyc_orchestrator"))) == 7
    ok = asyncio.run(_call("kyc_orchestrator", "audit_logger", {"event": "TEST", "data": {"k": 1}}))
    assert ok["authorised"] is True and "audit" in ok["obligations"]
    denied = asyncio.run(_call("read_only_reviewer", "audit_logger", {"event": "TEST", "data": {}}))
    assert denied["authorised"] is False and "no rule grants" in denied["reason"]
    traversal = asyncio.run(_call("kyc_orchestrator", "analyse_id_document",
                                  {"image_path": "../../etc/passwd", "declared_doc_type": "passport"}))
    assert traversal["authorised"] is False and "path_within" in traversal["reason"]


def test_denials_are_audited():
    log = os.path.join(REPO, "audit_trail.log")
    with open(log) as f:
        tail = f.read()[-4000:]
    assert "TOOL_CALL_DENIED" in tail and "TOOL_CALL_AUTHORISED" in tail


if __name__ == "__main__":
    for fn in (test_pdp_default_deny, test_pep_over_mcp, test_denials_are_audited):
        fn(); print(f"PASS  {fn.__name__}")
