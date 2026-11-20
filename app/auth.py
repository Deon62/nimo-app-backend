"""Verifies Supabase access tokens sent by the app as `Authorization: Bearer <jwt>`."""

from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None
    phone: str | None


@lru_cache
def _jwks_client(supabase_url: str) -> jwt.PyJWKClient:
    # Projects using asymmetric signing keys publish them here.
    return jwt.PyJWKClient(f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json", cache_keys=True)


def decode_supabase_token(token: str, settings: Settings) -> dict:
    alg = jwt.get_unverified_header(token).get("alg", "")
    options = {"require": ["exp", "sub"]}
    if alg == "HS256":
        if not settings.supabase_jwt_secret:
            raise jwt.InvalidTokenError("HS256 token but SUPABASE_JWT_SECRET is not set")
        key = settings.supabase_jwt_secret
    else:
        key = _jwks_client(settings.supabase_url).get_signing_key_from_jwt(token).key
    return jwt.decode(
        token,
        key,
        algorithms=[alg] if alg in {"HS256", "RS256", "ES256"} else ["RS256", "ES256"],
        audience=settings.supabase_jwt_audience,
        options=options,
    )


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        claims = decode_supabase_token(creds.credentials, settings)
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
    return CurrentUser(id=claims["sub"], email=claims.get("email"), phone=claims.get("phone"))
