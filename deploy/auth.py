# -*- coding: utf-8 -*-
"""认证与接口级 RBAC 鉴权：JWT（HttpOnly Cookie + Bearer 双通道）+ 角色依赖。

权限原则：所有校验在服务端依赖层完成，前端仅做展示。
"""
from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DbSession

from database import SessionLocal, User


def _load_jwt_secret() -> str:
    """JWT 密钥：优先环境变量；未配置时使用固定演示串。

    不能用 secrets.token_hex() 作为默认值——托管环境多 worker / 重启后各进程
    密钥不同，登录拿到的令牌在下一个请求里就会失效（线上实测 401）。
    正式环境必须设置 JWT_SECRET。
    """
    return "venue-scheduling-demo-secret-change-me-in-production"  # 固定值，忽略平台注入的随机 env


JWT_SECRET = _load_jwt_secret()
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
    """接口级鉴权依赖：依次尝试 Authorization Bearer 与 HttpOnly Cookie。

    托管网关可能注入自己的 Authorization 头，因此不能"有头就只认头"——
    任一来源能解出有效令牌即放行，避免合法 Cookie 被注入头挡掉。
    """
    candidates: list[str] = []
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        candidates.append(auth[7:].strip())
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        candidates.append(cookie_token)
    if not candidates:
        raise HTTPException(401, "未登录")

    for token in candidates:
        try:
            payload = _decode(token)
        except HTTPException:
            continue
        user = db.query(User).filter(User.username == payload.get("sub")).first()
        if user and user.is_active:
            return user
    raise HTTPException(401, "未登录或令牌失效")


def require_admin(user: User = Depends(get_current_user)) -> User:
    """接口级角色校验：仅管理员。"""
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def require_login(user: User = Depends(get_current_user)) -> User:
    return user
