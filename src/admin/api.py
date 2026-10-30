# -*- coding: utf-8 -*-
"""REST API 路由：全部接口经依赖注入做接口级角色校验。"""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from auth import COOKIE_NAME, create_token, get_current_user, hash_password, require_admin, require_login, verify_password
from database import (
    ChatMessage, ChatSession, KbChunk, KbDocument, PromptTemplate,
    ScheduleDraft, SessionLocal, User, now,
)
from rag import (
    FaissIndex, call_llm, chunk_text, embed_texts, get_llm_config,
    hex_to_vec, hybrid_search, parse_file, vec_to_hex,
)

api = APIRouter(prefix="/api")

# 全局 FAISS 索引（启动时从库重建，增删文档后刷新）
faiss_index = FaissIndex()


def reload_index() -> None:
    with SessionLocal() as db:
        rows = []
        for chunk in db.query(KbChunk).filter(KbChunk.vector.isnot(None)).all():
            rows.append((chunk.id, hex_to_vec(chunk.vector)))
    faiss_index.rebuild(rows)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================ 认证 ============================

class LoginIn(BaseModel):
    username: str
    password: str


@api.post("/auth/login")
def login(body: LoginIn, db: DbSession = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash) or not user.is_active:
        raise HTTPException(401, "账号或密码错误，或账号已停用")
    token = create_token(user)
    return {"token": token, "cookie_name": COOKIE_NAME, "role": user.role, "username": user.username}


@api.post("/auth/logout")
def logout():
    return {"ok": True, "clear_cookie": COOKIE_NAME}


@api.get("/me")
def me(user: User = Depends(require_login)):
    return {"username": user.username, "role": user.role}


# ============================ 知识库 ============================

@api.post("/kb/documents")
def upload_document(
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith((".md", ".markdown", ".pdf")):
        raise HTTPException(400, "仅支持 Markdown(.md) 与 PDF 文件")
    data = file.file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "文件不能超过 20MB")
    try:
        text = parse_file(file.filename, data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"文件解析失败: {e}") from e
    if not text.strip():
        raise HTTPException(400, "未能从文件中解析出文本内容")

    doc = KbDocument(filename=file.filename, doc_type="制度/规范", uploaded_by=user.username, status="processing")
    db.add(doc)
    db.commit()
    try:
        pieces = chunk_text(text)
        cfg = get_llm_config()
        vectors = embed_texts(pieces, cfg)
        for i, (piece, vec) in enumerate(zip(pieces, vectors)):
            db.add(KbChunk(document_id=doc.id, chunk_no=i, content=piece, vector=vec_to_hex(vec)))
        doc.chunk_count = len(pieces)
        doc.status = "ready"
        db.commit()
        reload_index()
    except Exception as e:  # noqa: BLE001
        doc.status = "failed"
        db.commit()
        raise HTTPException(500, f"切片/向量化失败: {e}") from e
    return {"id": doc.id, "filename": doc.filename, "chunk_count": doc.chunk_count, "status": doc.status}


@api.get("/kb/documents")
def list_documents(user: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    docs = db.query(KbDocument).order_by(KbDocument.created_at.desc()).all()
    return [
        {
            "id": d.id, "filename": d.filename, "doc_type": d.doc_type, "version": d.version,
            "chunk_count": d.chunk_count, "status": d.status, "uploaded_by": d.uploaded_by,
            "created_at": d.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for d in docs
    ]


@api.delete("/kb/documents/{doc_id}")
def delete_document(doc_id: int, user: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    doc = db.query(KbDocument).get(doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    db.delete(doc)  # kb_chunks 级联删除
    db.commit()
    reload_index()  # 同步重建 FAISS 索引，向量库同步清除
    return {"ok": True, "deleted": doc.filename}


@api.get("/kb/search")
def kb_search(q: str, user: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    chunks = [
        {"id": c.id, "source": c.document.filename, "chunk_no": c.chunk_no, "content": c.content}
        for c in db.query(KbChunk).join(KbDocument).all()
    ]
    results = hybrid_search(q, chunks, faiss_index, get_llm_config())
    return [{"source": r["source"], "chunk_no": r["chunk_no"], "preview": r["content"][:120]} for r in results]


# ============================ 提示词模板 ============================

@api.get("/prompt-template")
def get_prompt(user: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    tpl = db.query(PromptTemplate).filter(PromptTemplate.is_active).first()
    if not tpl:
        raise HTTPException(404, "未配置提示词模板")
    return {"version": tpl.version, "content": tpl.content, "updated_by": tpl.updated_by,
            "updated_at": tpl.updated_at.strftime("%Y-%m-%d %H:%M:%S")}


class PromptIn(BaseModel):
    content: str


@api.put("/prompt-template")
def save_prompt(body: PromptIn, user: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    content = body.content.strip()
    if len(content) < 20:
        raise HTTPException(400, "提示词内容过短（至少 20 字）")
    if not any(k in content for k in ("锁档", "签约", "容量", "人工复核")):
        raise HTTPException(400, "提示词必须保留业务风控边界关键词（锁档/签约/容量/人工复核）")
    current = db.query(PromptTemplate).filter(PromptTemplate.is_active).first()
    if current:
        current.is_active = False
    tpl = PromptTemplate(version=(current.version + 1) if current else 1,
                         content=content, is_active=True, updated_by=user.username)
    db.add(tpl)
    db.commit()
    return {"ok": True, "version": tpl.version, "effective": "立即生效（问答管线实时读库，无需重启）"}


@api.post("/prompt-template/reset")
def reset_prompt(user: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    from database import DEFAULT_PROMPT

    current = db.query(PromptTemplate).filter(PromptTemplate.is_active).first()
    if current:
        current.is_active = False
    tpl = PromptTemplate(version=(current.version + 1) if current else 1,
                         content=DEFAULT_PROMPT, is_active=True, updated_by=user.username)
    db.add(tpl)
    db.commit()
    return {"ok": True, "version": tpl.version}


# ============================ 档期问答（完整审计留痕） ============================

class ChatIn(BaseModel):
    question: str
    session_id: int | None = None


@api.post("/chat")
def chat(body: ChatIn, user: User = Depends(require_login), db: DbSession = Depends(get_db)):
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "问题不能为空")

    # 1. 每次请求实时读生效模板（保存即生效，无需重启）
    tpl = db.query(PromptTemplate).filter(PromptTemplate.is_active).first()
    if not tpl:
        raise HTTPException(500, "系统提示词模板未配置")

    # 2. 混合检索 top3
    rows = (
        db.query(KbChunk, KbDocument)
        .join(KbDocument, KbChunk.document_id == KbDocument.id)
        .filter(KbDocument.status == "ready")
        .all()
    )
    chunks = [
        {"id": c.id, "source": d.filename, "doc_id": d.id, "doc_version": d.version,
         "chunk_no": c.chunk_no, "content": c.content}
        for c, d in rows
    ]
    retrieved = hybrid_search(question, chunks, faiss_index, get_llm_config())

    # 3. 组装上下文并调用 LLM（供应商可配置切换）
    context = "\n\n".join(
        f"[来源{i}] {r['source']}/切片#{r['chunk_no']}\n{r['content']}" for i, r in enumerate(retrieved, 1)
    )
    system = tpl.content + "\n\n【检索资料（引用时标注 [来源N]）】\n" + (context or "（知识库为空）")
    answer = call_llm(
        [{"role": "system", "content": system}, {"role": "user", "content": question}],
        get_llm_config(),
    )

    # 4. 完整留痕：提问原文 / 召回片段（含文档版本）/ 大模型原始回答全文 / 访问人 / 模板版本
    if body.session_id:
        session = db.query(ChatSession).get(body.session_id)
        if not session or session.user_id != user.id:
            raise HTTPException(404, "会话不存在")
    else:
        session = ChatSession(user_id=user.id, username=user.username, title=question[:50])
        db.add(session)
        db.commit()
    msg = ChatMessage(
        session_id=session.id,
        question=question,
        retrieved_chunks=json.dumps(
            [{"source": r["source"], "doc_id": r["doc_id"], "doc_version": r["doc_version"],
              "chunk_no": r["chunk_no"], "text": r["content"]} for r in retrieved],
            ensure_ascii=False,
        ),
        llm_raw_answer=answer,
        prompt_template_version=tpl.version,
    )
    db.add(msg)
    db.commit()
    return {
        "session_id": session.id,
        "answer": answer,
        "footer": "⚠️ 以上为系统候选提示，最终结论需要人工复核确认。",
        "citations": [{"no": i, "source": r["source"], "chunk_no": r["chunk_no"], "doc_version": r["doc_version"]}
                      for i, r in enumerate(retrieved, 1)],
        "retrieved": retrieved,
    }


@api.get("/chat/sessions")
def my_sessions(user: User = Depends(require_login), db: DbSession = Depends(get_db)):
    q = db.query(ChatSession)
    if user.role != "admin":
        q = q.filter(ChatSession.user_id == user.id)
    sessions = q.order_by(ChatSession.created_at.desc()).limit(100).all()
    return [{"id": s.id, "username": s.username, "title": s.title,
             "created_at": s.created_at.strftime("%Y-%m-%d %H:%M")} for s in sessions]


@api.get("/chat/sessions/{sid}")
def session_detail(sid: int, user: User = Depends(require_login), db: DbSession = Depends(get_db)):
    s = db.query(ChatSession).get(sid)
    if not s or (user.role != "admin" and s.user_id != user.id):
        raise HTTPException(404, "会话不存在")
    msgs = db.query(ChatMessage).filter(ChatMessage.session_id == sid).order_by(ChatMessage.created_at).all()
    return {
        "id": s.id, "username": s.username, "title": s.title,
        "messages": [
            {"id": m.id, "question": m.question, "llm_raw_answer": m.llm_raw_answer,
             "retrieved": json.loads(m.retrieved_chunks), "prompt_template_version": m.prompt_template_version,
             "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S"),
             "flag_type": m.flag_type, "flag_reason": m.flag_reason}
            for m in msgs
        ],
    }


# ============================ 审计（管理员专属） ============================

@api.get("/admin/audit/sessions")
def audit_sessions(
    user: str = "", start: str = "", end: str = "", flag: str = "",
    _: User = Depends(require_admin), db: DbSession = Depends(get_db),
):
    q = db.query(ChatMessage).join(ChatSession)
    if user:
        q = q.filter(ChatSession.username == user)
    if start:
        q = q.filter(ChatMessage.created_at >= dt.datetime.strptime(start, "%Y-%m-%d"))
    if end:
        q = q.filter(ChatMessage.created_at < dt.datetime.strptime(end, "%Y-%m-%d") + dt.timedelta(days=1))
    if flag == "risk":
        q = q.filter(ChatMessage.flag_type.isnot(None))
    elif flag == "normal":
        q = q.filter(ChatMessage.flag_type.is_(None))
    msgs = q.order_by(ChatMessage.created_at.desc()).limit(200).all()
    return [
        {"id": m.id, "session_id": m.session_id, "username": m.session.username,
         "question": m.question[:80], "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S"),
         "flag_type": m.flag_type, "flag_reason": m.flag_reason}
        for m in msgs
    ]


class FlagIn(BaseModel):
    flag_type: str  # conflict_misjudge / overcommit / invalid_rule
    reason: str


FLAG_LABELS = {"conflict_misjudge": "排期冲突误判", "overcommit": "越权承诺", "invalid_rule": "违规口径"}


@api.post("/admin/audit/messages/{mid}/flag")
def flag_message(mid: int, body: FlagIn, user: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    if body.flag_type not in FLAG_LABELS:
        raise HTTPException(400, "无效的风险类型")
    if not body.reason.strip():
        raise HTTPException(400, "标记原因必填")
    m = db.query(ChatMessage).get(mid)
    if not m:
        raise HTTPException(404, "问答记录不存在")
    m.flag_type, m.flag_reason = body.flag_type, body.reason.strip()
    m.flagged_by, m.flagged_at = user.username, now()
    db.commit()
    return {"ok": True, "flag_type": m.flag_type, "label": FLAG_LABELS[m.flag_type]}


@api.delete("/admin/audit/messages/{mid}/flag")
def unflag_message(mid: int, user: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    m = db.query(ChatMessage).get(mid)
    if not m:
        raise HTTPException(404, "问答记录不存在")
    m.flag_type = m.flag_reason = m.flagged_by = None
    m.flagged_at = None
    db.commit()
    return {"ok": True}


@api.get("/admin/audit/messages/{mid}/full")
def audit_message_full(mid: int, _: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    m = db.query(ChatMessage).get(mid)
    if not m:
        raise HTTPException(404, "问答记录不存在")
    return {
        "id": m.id, "session_id": m.session_id, "username": m.session.username,
        "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "question": m.question,
        "retrieved": json.loads(m.retrieved_chunks),
        "llm_raw_answer": m.llm_raw_answer,
        "prompt_template_version": m.prompt_template_version,
        "flag_type": m.flag_type, "flag_reason": m.flag_reason,
        "flagged_by": m.flagged_by,
        "flagged_at": m.flagged_at.strftime("%Y-%m-%d %H:%M:%S") if m.flagged_at else None,
    }


# ============================ 档期草稿 ============================

class DraftIn(BaseModel):
    project_name: str
    venue: str
    event_start: str
    event_end: str
    dock_no: str = ""
    load_in_start: str = ""
    load_out_end: str = ""
    note: str = ""


@api.post("/drafts")
def create_draft(body: DraftIn, user: User = Depends(require_login), db: DbSession = Depends(get_db)):
    if not body.project_name.strip() or not body.venue.strip() or not body.event_start or not body.event_end:
        raise HTTPException(400, "项目名称、场地、起止时间为必填")
    draft = ScheduleDraft(user_id=user.id, username=user.username, **body.model_dump())
    db.add(draft)
    db.commit()
    return {"ok": True, "id": draft.id, "status": draft.status,
            "notice": "草稿仅作登记，不触发锁定/签约；正式档期需人工复核确认。"}


@api.get("/drafts")
def list_drafts(user: User = Depends(require_login), db: DbSession = Depends(get_db)):
    q = db.query(ScheduleDraft)
    if user.role != "admin":
        q = q.filter(ScheduleDraft.user_id == user.id)
    drafts = q.order_by(ScheduleDraft.created_at.desc()).limit(100).all()
    return [{"id": d.id, "username": d.username, "project_name": d.project_name, "venue": d.venue,
             "event_start": d.event_start, "event_end": d.event_end, "dock_no": d.dock_no,
             "status": d.status, "created_at": d.created_at.strftime("%Y-%m-%d %H:%M")} for d in drafts]


# ============================ 用户管理（管理员专属） ============================

@api.get("/admin/users")
def list_users(_: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    return [{"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active,
             "created_at": u.created_at.strftime("%Y-%m-%d")} for u in db.query(User).all()]


class UserIn(BaseModel):
    username: str
    password: str
    role: str = "employee"


@api.post("/admin/users")
def create_user(body: UserIn, _: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    if body.role not in ("employee", "admin"):
        raise HTTPException(400, "角色只能是 employee 或 admin")
    if len(body.username) < 2 or len(body.password) < 6:
        raise HTTPException(400, "用户名至少 2 位，密码至少 6 位")
    if db.query(User).filter(User.username == body.username).count():
        raise HTTPException(400, "用户名已存在")
    u = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.commit()
    return {"ok": True, "id": u.id}


class UserPatch(BaseModel):
    is_active: bool | None = None
    new_password: str | None = None


@api.patch("/admin/users/{uid}")
def patch_user(uid: int, body: UserPatch, _: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    u = db.query(User).get(uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    if body.is_active is not None:
        u.is_active = body.is_active
    if body.new_password:
        if len(body.new_password) < 6:
            raise HTTPException(400, "密码至少 6 位")
        u.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}
