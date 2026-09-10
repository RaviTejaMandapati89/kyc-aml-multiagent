"""
a2a_identity.py - Workload identity for the agent-to-agent boundary.

Problem this solves
-------------------
The A2A task endpoint previously took the caller's identity from a field in the
request body ("sender_agent_id"). That is an assertion, not a proof: anything
able to reach the port could claim to be the KYC orchestrator and have a
compliance inference run on data it supplied. This module replaces the assertion
with a verifiable one.

Approach
--------
The calling agent mints a short-lived JSON Web Token signed with its own private
key. The receiving agent verifies it with the caller's public key and derives the
principal from the *verified* token, ignoring anything self-asserted in the body.

Why asymmetric (RS256) rather than a shared secret (HS256)
----------------------------------------------------------
With a shared secret, every party that can verify a token can also mint one. The
receiving agent would hold a key capable of impersonating the sender, so a
compromise of the receiver forges the sender's identity. With asymmetric keys the
receiver holds only a public key: it can check a signature and cannot produce
one. That property is what makes the identity claim meaningful.

Why the token is bound to the payload
-------------------------------------
A bearer token is, by definition, usable by whoever holds it. Without binding, a
token captured in transit or in a log is valid against ANY payload for its whole
lifetime: an attacker could keep the token and substitute a different customer's
data. The `pbh` claim carries a SHA-256 hash of the canonical request body, so
the receiver can confirm the body it received is the body the sender signed for.
Change one byte and verification fails.

Why short expiry plus a replay cache
------------------------------------
Expiry bounds how long a leaked token is useful. The `jti` (unique token id)
cache means a token cannot be used twice even inside that window. Together they
turn a captured token from a reusable credential into, at worst, one lost
request.

Why audience is checked
-----------------------
The `aud` claim names the intended receiver. Without checking it, a token minted
for one agent could be replayed against a different agent that trusts the same
signer. Checking audience keeps a credential scoped to the conversation it was
issued for.

Production differences, stated plainly
--------------------------------------
Keys here are generated locally on first run. In production the calling
workload's identity would come from the platform (a Kubernetes service account
token, GCP workload identity, or an OAuth 2.1 client-credentials grant against a
real authorisation server), public keys would be discovered through a JWKS
endpoint rather than a local file, keys would rotate on a schedule, and the
replay cache would be shared state (Redis) rather than in-process. The claim set
and the verification order below are the same either way.
"""

from __future__ import annotations

import os
import json
import time
import uuid
import hashlib
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
KEY_DIR = os.path.join(REPO_ROOT, "keys")

ALGORITHM = "RS256"
TOKEN_LIFETIME_SECONDS = 60          # short: a leaked token is useful briefly
CLOCK_SKEW_LEEWAY_SECONDS = 5        # tolerate small clock differences


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class IdentityError(Exception):
    """Raised whenever a token cannot be trusted. The caller turns this into a
    401. Every failure path fails closed: there is no branch in this module that
    accepts a token it could not fully verify."""


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------
def _key_paths(agent_id: str) -> tuple[str, str]:
    safe = agent_id.replace("/", "_").replace(":", "_")
    return (os.path.join(KEY_DIR, f"{safe}.private.pem"),
            os.path.join(KEY_DIR, f"{safe}.public.pem"))


def ensure_keypair(agent_id: str) -> tuple[str, str]:
    """Generate an RSA keypair for an agent on first use, into a gitignored
    directory. Private keys are never committed: a private key in a public
    repository is a published credential, demo or not."""
    private_path, public_path = _key_paths(agent_id)
    if os.path.exists(private_path) and os.path.exists(public_path):
        return private_path, public_path

    os.makedirs(KEY_DIR, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with open(private_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    os.chmod(private_path, 0o600)

    with open(public_path, "wb") as f:
        f.write(key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
    return private_path, public_path


def load_private_key(agent_id: str) -> str:
    private_path, _ = ensure_keypair(agent_id)
    with open(private_path) as f:
        return f.read()


def load_public_key(agent_id: str) -> str:
    """The receiver's view of a sender. In production this is a JWKS lookup
    against the sender's well-known endpoint, not a file read."""
    _, public_path = _key_paths(agent_id)
    if not os.path.exists(public_path):
        raise IdentityError(f"no public key on file for issuer '{agent_id}'")
    with open(public_path) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Payload binding
# ---------------------------------------------------------------------------
def payload_hash(payload: Any) -> str:
    """SHA-256 over a canonical JSON encoding. Canonical (sorted keys, fixed
    separators) so that sender and receiver hash the same bytes regardless of
    dictionary ordering."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# ---------------------------------------------------------------------------
# Minting (sender side)
# ---------------------------------------------------------------------------
def mint_token(issuer_agent_id: str, audience_agent_id: str, scope: str,
               payload: Any, private_key_pem: str | None = None) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer_agent_id,                 # who is calling
        "sub": issuer_agent_id,                 # workload identity, not a user
        "aud": audience_agent_id,               # who it may be presented to
        "scope": scope,                         # what is being requested
        "pbh": payload_hash(payload),           # binds the token to this body
        "iat": now,
        "nbf": now,
        "exp": now + TOKEN_LIFETIME_SECONDS,
        "jti": str(uuid.uuid4()),               # unique, for replay detection
    }
    key = private_key_pem or load_private_key(issuer_agent_id)
    return jwt.encode(claims, key, algorithm=ALGORITHM)


# ---------------------------------------------------------------------------
# Verification (receiver side)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VerifiedCaller:
    """What the receiver knows about the caller AFTER cryptographic proof.
    Nothing in here came from the request body."""
    agent_id: str
    scope: str
    token_id: str
    expires_at: int


class ReplayCache:
    """Remembers token ids until they expire. In-process and therefore
    single-instance only; production uses shared state so that a token replayed
    against a different replica is still caught."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def check_and_record(self, token_id: str, expires_at: int) -> None:
        now = int(time.time())
        for jti, exp in list(self._seen.items()):
            if exp < now:
                del self._seen[jti]
        if token_id in self._seen:
            raise IdentityError("token replay detected: this token id has already been used")
        self._seen[token_id] = expires_at


def verify_token(token: str, expected_audience: str, payload: Any,
                 replay_cache: ReplayCache,
                 public_key_resolver=load_public_key) -> VerifiedCaller:
    """Verify in a deliberate order, cheapest and most fundamental first.

    1. Read the issuer WITHOUT trusting it, only to find which key to check
       against. An unverified header is a routing hint, never an identity.
    2. Verify the signature, expiry, not-before and audience. Algorithm is
       pinned to RS256 so a token claiming alg=none or alg=HS256 is rejected
       rather than being verified against a public key treated as a secret.
    3. Confirm the payload hash matches the body actually received.
    4. Confirm the token has not been used before.

    Any failure raises. There is no partial trust.
    """
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        issuer = unverified.get("iss")
    except jwt.PyJWTError as exc:
        raise IdentityError(f"malformed token: {exc}") from exc

    if not issuer:
        raise IdentityError("token has no issuer claim")

    public_key = public_key_resolver(issuer)

    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=[ALGORITHM],          # pinned: no algorithm confusion
            audience=expected_audience,
            issuer=issuer,
            leeway=CLOCK_SKEW_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "jti", "scope", "pbh"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise IdentityError("token has expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise IdentityError("token was not issued for this agent") from exc
    except jwt.PyJWTError as exc:
        raise IdentityError(f"token failed verification: {exc}") from exc

    if claims["pbh"] != payload_hash(payload):
        raise IdentityError(
            "payload does not match the one this token was issued for")

    replay_cache.check_and_record(claims["jti"], claims["exp"])

    return VerifiedCaller(
        agent_id=claims["iss"],
        scope=claims["scope"],
        token_id=claims["jti"],
        expires_at=claims["exp"],
    )
