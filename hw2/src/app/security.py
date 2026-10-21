import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.errors import ApiException


JWT_SECRET = os.getenv("JWT_SECRET", "local-development-secret-key-with-at-least-32-bytes")
ACCESS_MINUTES = 20
REFRESH_DAYS = 14
ALGORITHM = "HS256"
ROLES = {"USER", "SELLER", "ADMIN"}
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    role: str


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_tokens(user_id: UUID, role: str) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    base = {"sub": str(user_id), "role": role, "iat": now}
    access = jwt.encode(
        {**base, "type": "access", "exp": now + timedelta(minutes=ACCESS_MINUTES)},
        JWT_SECRET,
        algorithm=ALGORITHM,
    )
    refresh = jwt.encode(
        {**base, "type": "refresh", "exp": now + timedelta(days=REFRESH_DAYS)},
        JWT_SECRET,
        algorithm=ALGORITHM,
    )
    return {"access_token": access, "refresh_token": refresh}


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exception:
        code = "TOKEN_EXPIRED" if expected_type == "access" else "REFRESH_TOKEN_INVALID"
        raise ApiException(code, 401, "Token expired") from exception
    except jwt.InvalidTokenError as exception:
        code = "TOKEN_INVALID" if expected_type == "access" else "REFRESH_TOKEN_INVALID"
        raise ApiException(code, 401, "Token is invalid") from exception
    if payload.get("type") != expected_type:
        code = "TOKEN_INVALID" if expected_type == "access" else "REFRESH_TOKEN_INVALID"
        raise ApiException(code, 401, "Token type is invalid")
    return payload


def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentUser:
    if credentials is None:
        raise ApiException("TOKEN_INVALID", 401, "Access token is missing or invalid")
    payload = decode_token(credentials.credentials, "access")
    try:
        user = CurrentUser(UUID(payload["sub"]), payload["role"])
    except (KeyError, TypeError, ValueError) as exception:
        raise ApiException("TOKEN_INVALID", 401, "Access token is invalid") from exception
    if user.role not in ROLES:
        raise ApiException("TOKEN_INVALID", 401, "Access token is invalid")
    request.state.user_id = str(user.id)
    return user


def require_role(user: CurrentUser, *roles: str) -> None:
    if user.role not in roles:
        raise ApiException("ACCESS_DENIED", 403, "Access denied")
