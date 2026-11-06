# -*- coding: utf-8 -*-
"""SSR 页面路由（Jinja2）。页面访问同样经服务端鉴权：未登录跳转 /login，越权 403。"""
from __future__ import annotations

from pathlib import Path

from auth import COOKIE_NAME, _decode
from database import SessionLocal, User
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

pages = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

LOGIN_REDIRECT = HTTPException(status_code=302, headers={"Location": "/login"})


def _load_user(request: Request, db) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = _decode(token)
    except HTTPException:
        return None
    user = db.query(User).filter(User.username == payload["sub"]).first()
    if not user or not user.is_active:
        return None
    return user


def page_auth(request: Request, admin_only: bool = False) -> tuple[User, SessionLocal]:
    """页面级鉴权：未登录 302 /login；越权 403。服务端强制，前端仅展示。"""
    db = SessionLocal()
    user = _load_user(request, db)
    if user is None:
        db.close()
        raise LOGIN_REDIRECT
    if admin_only and user.role != "admin":
        db.close()
        raise HTTPException(403, "需要管理员权限")
    return user, db


def render(request: Request, template: str, user: User, db: SessionLocal, **ctx):
    resp = templates.TemplateResponse(
        request, template, {"user": {"username": user.username, "role": user.role}, **ctx},
    )
    db.close()
    return resp


# ---------------- 页面 ----------------

@pages.get("/login")
def login_page(request: Request):
    db = SessionLocal()
    if _load_user(request, db) is not None:
        db.close()
        return RedirectResponse("/chat", status_code=302)
    db.close()
    return templates.TemplateResponse(request, "login.html", {})


@pages.get("/")
def home(request: Request):
    user, db = page_auth(request)
    return render(request, "index.html", user, db)


@pages.get("/chat")
def chat_page(request: Request):
    user, db = page_auth(request)
    return render(request, "chat.html", user, db)


@pages.get("/drafts")
def drafts_page(request: Request):
    user, db = page_auth(request)
    return render(request, "drafts.html", user, db)


@pages.get("/chat/history")
def history_page(request: Request):
    user, db = page_auth(request)
    return render(request, "history.html", user, db)


@pages.get("/admin/kb")
def kb_page(request: Request):
    user, db = page_auth(request, admin_only=True)
    return render(request, "admin_kb.html", user, db)


@pages.get("/admin/prompt")
def prompt_page(request: Request):
    user, db = page_auth(request, admin_only=True)
    return render(request, "admin_prompt.html", user, db)


@pages.get("/admin/audit")
def audit_page(request: Request):
    user, db = page_auth(request, admin_only=True)
    return render(request, "admin_audit.html", user, db)


@pages.get("/admin/users")
def users_page(request: Request):
    user, db = page_auth(request, admin_only=True)
    return render(request, "admin_users.html", user, db)
