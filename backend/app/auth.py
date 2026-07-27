"""Authentication middleware using Supabase Auth JWT tokens.

Flow:
  1. Frontend logs user in via Supabase Auth (email/password or OAuth)
  2. Frontend sends the access_token in Authorization header on every request
  3. This middleware validates the JWT and extracts user_id
  4. user_id is attached to the request state for downstream use
  5. Unauthenticated requests get 401

Public endpoints (no auth required): /health, /docs, /openapi.json, /redoc
"""
from __future__ import annotations

import logging
from typing import Optional

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

logger = logging.getLogger(__name__)

# Endpoints that don't require authentication
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc", "/docs/oauth2-redirect"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate Supabase JWT and attach user_id to request.state."""

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public endpoints and CORS preflight
        if request.url.path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid authorization header"},
            )

        token = auth_header[len("Bearer "):]

        # Validate JWT
        user_id = _validate_token(token)
        if user_id is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
            )

        # Attach user_id to request state for downstream use
        request.state.user_id = user_id
        return await call_next(request)


def _validate_token(token: str) -> Optional[str]:
    """Validate a Supabase JWT and return the user_id (sub claim)."""
    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
        user_id = payload.get("sub")
        if not user_id:
            return None
        return user_id
    except jwt.ExpiredSignatureError:
        logger.debug("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("Invalid token: %s", e)
        return None
