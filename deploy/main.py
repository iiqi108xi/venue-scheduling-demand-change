# -*- coding: utf-8 -*-
"""场馆档期排期智能体 · 管理后台（FastAPI 服务端渲染）。

启动：
  cd src/admin && python -m uvicorn main:app --port 8000
默认账号：admin/admin123（管理员）、staff/staff123（一线员工）
"""
from __future__ import annotations

from pathlib import Path

from database import init_db
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import api as api_module
from pages import pages

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="场馆档期排期智能体 · 管理后台", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(api_module.api)
app.include_router(pages)


@app.on_event("startup")
def startup():
    init_db()
    api_module.reload_index()


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc):
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "未登录或令牌失效"}, status_code=401)
    return RedirectError("/login")


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": getattr(exc, "detail", "需要管理员权限")}, status_code=403)
    return JSONResponse({"detail": "403：需要管理员权限"}, status_code=403)


def RedirectError(location: str):
    from fastapi.responses import RedirectResponse

    return RedirectResponse(location, status_code=302)
