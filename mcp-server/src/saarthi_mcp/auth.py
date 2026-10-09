"""Cognito access-token verification for one explicitly configured household.

The SDK verifies signatures against the pool's JWKS; this layer additionally
checks token purpose, app client, lifetime and allowed Cognito subject IDs.
An account in the pool alone never grants access to this household.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from urllib.parse import urlsplit
from uuid import UUID

from fastmcp.server.auth.providers.jwt import JWTVerifier


@dataclass(frozen=True)
class CognitoSettings:
    region: str
    pool_id: str
    client_id: str
    resource_url: str
    allowed_subjects: frozenset[str]

    def __post_init__(self):
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", self.region):
            raise ValueError("Set a valid AWS region for Cognito.")
        if not re.fullmatch(re.escape(self.region) + r"_[A-Za-z0-9]+", self.pool_id):
            raise ValueError("COGNITO_USER_POOL_ID must belong to AWS_REGION.")
        if not re.fullmatch(r"[a-z0-9]{1,128}", self.client_id):
            raise ValueError("Set a valid COGNITO_CLIENT_ID.")
        resource = urlsplit(self.resource_url)
        if (not re.fullmatch(r'[\x21\x23-\x5B\x5D-\x7E]{1,256}', self.resource_url)
                or not resource.hostname or resource.username or resource.password or resource.query or resource.fragment
                or not resource.path or resource.path == "/"
                or (resource.scheme != "https" and not (resource.scheme == "http" and resource.hostname == "localhost"))):
            raise ValueError("COGNITO_RESOURCE_URL must be the HTTPS MCP URL (HTTP localhost allowed for local checks).")
        if not 1 <= len(self.allowed_subjects) <= 100:
            raise ValueError("List the household's allowed Cognito subject IDs in COGNITO_ALLOWED_SUBJECTS.")
        try:
            if any(str(UUID(subject)) != subject for subject in self.allowed_subjects):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError("COGNITO_ALLOWED_SUBJECTS must contain canonical Cognito UUID subject IDs.") from None

    @property
    def scope(self):
        # Cognito resource binding requires custom scopes on this exact resource.
        return self.resource_url + "/notebook"

    @property
    def issuer(self):
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.pool_id}"


class HouseholdTokenVerifier(JWTVerifier):
    def __init__(self, settings: CognitoSettings):
        self.settings = settings
        super().__init__(jwks_uri=settings.issuer + "/.well-known/jwks.json",
                         issuer=settings.issuer, audience=settings.resource_url,
                         algorithm="RS256", required_scopes=[settings.scope])

    async def verify_token(self, token):
        # Fail closed if the identity provider or key retrieval is unavailable.
        try:
            verified = await super().verify_token(token)
        except Exception:
            return None
        if verified is None:
            return None
        claims = verified.claims
        current = time.time()
        exp, issued, not_before = claims.get("exp"), claims.get("iat"), claims.get("nbf", 0)
        if (claims.get("token_use") != "access" or claims.get("client_id") != self.settings.client_id
                or not isinstance(claims.get("sub"), str) or claims.get("sub") not in self.settings.allowed_subjects
                or type(exp) is not int or type(issued) is not int or type(not_before) is not int
                or not (0 <= issued <= current + 30 and issued < exp and current < exp)
                or not_before > current + 30):
            return None
        return verified
