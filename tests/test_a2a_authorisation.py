"""
Proves the agent-to-agent boundary rejects what it should, with no cloud
credentials and no key files on disk. Run:  python tests/test_a2a_authorisation.py

Keys are generated in memory for the test, so a clean clone passes with no
setup and no private key is ever written or committed.

Each test corresponds to a specific attack:
  * forged identity      - claiming to be an agent you cannot sign as
  * expired token        - replaying a credential after its window
  * wrong audience       - reusing a token minted for a different agent
  * tampered payload     - keeping a valid token, swapping the customer data
  * replayed token       - using the same token twice
  * unregistered agent   - a real signature from an agent policy does not know
  * excess data          - a permitted caller forwarding more than it should
"""
import os
import sys
import time
import json

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import a2a_identity as ident  # noqa: E402
import policy  # noqa: E402

RECEIVER = "aml-reasoning-claude"
SENDER = "kyc-orchestrator-gemini"

VALID_PAYLOAD = {
    "customer_id": "CUST-2026-001",
    "customer_name": "James Harrington",
    "risk_tier": "High",
    "pep_hit": True,
    "document_valid": True,
    "gemini_recommendation": "ESCALATE",
}


def _keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private_pem, public_pem


SENDER_PRIVATE, SENDER_PUBLIC = _keypair()
ATTACKER_PRIVATE, _ATTACKER_PUBLIC = _keypair()


def _resolver(agent_id: str) -> str:
    """Stands in for JWKS discovery. Only the genuine sender has a public key
    on file, which is the point: an unknown issuer cannot be verified at all."""
    if agent_id == SENDER:
        return SENDER_PUBLIC
    raise ident.IdentityError(f"no public key on file for issuer '{agent_id}'")


def _verify(token, payload=VALID_PAYLOAD, audience=RECEIVER, cache=None):
    return ident.verify_token(token, expected_audience=audience, payload=payload,
                              replay_cache=cache or ident.ReplayCache(),
                              public_key_resolver=_resolver)


def _expect_rejected(fn, expected_fragment: str) -> None:
    try:
        fn()
    except ident.IdentityError as exc:
        assert expected_fragment in str(exc).lower(), \
            f"rejected, but for the wrong reason: {exc}"
        return
    raise AssertionError(f"NOT rejected, expected failure mentioning "
                         f"'{expected_fragment}'")


def test_genuine_token_is_accepted():
    token = ident.mint_token(SENDER, RECEIVER, "aml_assessment",
                             VALID_PAYLOAD, private_key_pem=SENDER_PRIVATE)
    caller = _verify(token)
    assert caller.agent_id == SENDER
    assert caller.scope == "aml_assessment"


def test_forged_identity_is_rejected():
    """An attacker claims to be the KYC orchestrator but signs with its own key.
    The signature is checked against the real sender's public key and fails."""
    token = ident.mint_token(SENDER, RECEIVER, "aml_assessment",
                             VALID_PAYLOAD, private_key_pem=ATTACKER_PRIVATE)
    _expect_rejected(lambda: _verify(token), "verification")


def test_unknown_issuer_is_rejected():
    """Signing correctly is not enough if nobody knows who you are."""
    token = ident.mint_token("some-other-agent", RECEIVER, "aml_assessment",
                             VALID_PAYLOAD, private_key_pem=ATTACKER_PRIVATE)
    _expect_rejected(lambda: _verify(token), "no public key")


def test_expired_token_is_rejected():
    original = ident.TOKEN_LIFETIME_SECONDS
    try:
        ident.TOKEN_LIFETIME_SECONDS = -60   # already expired when minted
        token = ident.mint_token(SENDER, RECEIVER, "aml_assessment",
                                 VALID_PAYLOAD, private_key_pem=SENDER_PRIVATE)
    finally:
        ident.TOKEN_LIFETIME_SECONDS = original
    _expect_rejected(lambda: _verify(token), "expired")


def test_wrong_audience_is_rejected():
    """A token minted for a different receiving agent must not work here."""
    token = ident.mint_token(SENDER, "some-other-receiver", "aml_assessment",
                             VALID_PAYLOAD, private_key_pem=SENDER_PRIVATE)
    _expect_rejected(lambda: _verify(token), "not issued for this agent")


def test_tampered_payload_is_rejected():
    """The attack payload binding exists to stop: a valid, unexpired token
    presented with a different customer's data."""
    token = ident.mint_token(SENDER, RECEIVER, "aml_assessment",
                             VALID_PAYLOAD, private_key_pem=SENDER_PRIVATE)
    swapped = dict(VALID_PAYLOAD, customer_id="CUST-2026-999",
                   customer_name="Someone Else")
    _expect_rejected(lambda: _verify(token, payload=swapped), "does not match")


def test_replayed_token_is_rejected():
    cache = ident.ReplayCache()
    token = ident.mint_token(SENDER, RECEIVER, "aml_assessment",
                             VALID_PAYLOAD, private_key_pem=SENDER_PRIVATE)
    _verify(token, cache=cache)                       # first use: fine
    _expect_rejected(lambda: _verify(token, cache=cache), "replay")


def test_policy_governs_the_delegation():
    """Authentication answers who. Authorisation answers whether."""
    registered = policy.principal_for_agent(SENDER)
    assert registered.role == "kyc_orchestrator"
    assert policy.evaluate(registered, "a2a:aml_assessment",
                           {"payload": VALID_PAYLOAD}).allowed

    stranger = policy.principal_for_agent("some-other-agent")
    assert stranger.role == "unregistered_agent"
    assert not policy.evaluate(stranger, "a2a:aml_assessment",
                               {"payload": VALID_PAYLOAD}).allowed


def test_data_minimisation_is_enforced():
    """A permitted caller forwarding more than the task needs is still refused.
    Authorisation is about the payload, not only the principal."""
    over_shared = dict(VALID_PAYLOAD, annual_income=85000,
                       address="42 Kensington Gardens, London")
    decision = policy.evaluate(policy.principal_for_agent(SENDER),
                               "a2a:aml_assessment", {"payload": over_shared})
    assert not decision.allowed
    assert "keys_within" in decision.reason


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(tests)} checks passed, no cloud credentials and no key files used.")
