"""Authentication middleware using Supabase Auth JWT tokens.

Supports both HS256 (legacy shared secret) and ES256 (new ECC keys).
For ES256, fetches the public key from Supabase's JWKS endpoint.
"""
from __future__ import annotations

import logging
from typing import Optional

import jwt
from jwt import PyJWKClient
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

logger = logging.getLogger(__name__)

# Endpoints that don't require authentication
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc", "/docs/oauth2-redirect"}

# JWKS client for ES256 verification (cached)
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate Supabase JWT and attach user_id to request.state."""

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public endpoints and CORS preflight
        if request.url.path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return self._unauthorized("Missing or invalid authorization header", request)

        token = auth_header[len("Bearer "):]

        # Validate JWT
        user_id = _validate_token(token)
        if user_id is None:
            return self._unauthorized("Invalid or expired token", request)

        # Attach user_id to request state for downstream use
        request.state.user_id = user_id
        return await call_next(request)

    def _unauthorized(self, detail: str, request: Request) -> JSONResponse:
        """Return a 401 with CORS headers so the browser doesn't block it."""
        origin = request.headers.get("origin", "")
        headers = {}
        if origin:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Credentials"] = "true"
        return JSONResponse(
            status_code=401,
            content={"detail": detail},
            headers=headers,
        )


def _validate_token(token: str) -> Optional[str]:
    """Validate a Supabase JWT and return the user_id (sub claim).

    Tries ES256 (JWKS) first, then falls back to HS256 (shared secret).
    """
    # Try ES256 via JWKS
    try:
        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
        )
        user_id = payload.get("sub")
        return user_id if user_id else None
    except Exception as e:
        logger.debug("ES256 validation failed: %s", e)

    # Fallback: try HS256 with shared secret
    if settings.supabase_jwt_secret:
        try:
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
            user_id = payload.get("sub")
            return user_id if user_id else None
        except jwt.ExpiredSignatureError:
            logger.debug("Token expired (HS256)")
            return None
        except jwt.InvalidTokenError as e:
            logger.debug("HS256 validation failed: %s", e)

    logger.warning("Token validation failed with all methods")
    return None
