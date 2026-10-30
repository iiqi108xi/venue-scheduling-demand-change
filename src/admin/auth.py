# -*- coding: utf-8 -*-
"""认证与接口级 RBAC 鉴权：JWT（HttpOnly Cookie + Bearer 双通道）+ 角色依赖。

权限原则：所有校验在服务端依赖层完成，前端仅做展示。
"""
from __future__ import annotations

import hashlib
import os
import secrets

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DbSession

from database import SessionLocal, User

JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_hex(32))
JWT_EXPIRE_HOURS = 12
COOKIE_NAME = "access_token"


# ---------- 密码（pbkdf2_sha256，标准库实现） ----------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return secrets.compare_digest(calc.hex(), digest)


# ---------- JWT ----------

def create_token(user: User) -> str:
    import datetime as dt

    payload = {
        "sub": user.username,
        "role": user.role,
        "uid": user.id,
        "exp": dt.datetime.now(dt.UTC) + dt.timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _get_db() -> DbSession:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(401, "登录已过期，请重新登录") from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(401, "无效令牌") from e


def get_current_user(request: Request, db: DbSession = Depends(_get_db)) -> User:
    """接口级鉴权依赖：优先 Authorization Bearer，其次 HttpOnly Cookie。"""
    token = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    if not token:
        token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "未登录")
    payload = _decode(token)
    user = db.query(User).filter(User.username == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(401, "用户不存在或已停用")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """接口级角色校验：仅管理员。"""
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def require_login(user: User = Depends(get_current_user)) -> User:
    return user
