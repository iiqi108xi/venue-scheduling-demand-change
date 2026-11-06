# -*- coding: utf-8 -*-
"""数据库连接与 ORM 模型（首版 SQLite，SQLAlchemy 可无缝切 PostgreSQL）。"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "admin.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def now() -> dt.datetime:
    return dt.datetime.now()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="employee")  # employee / admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class KbDocument(Base):
    __tablename__ = "kb_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(256))
    doc_type: Mapped[str] = mapped_column(String(64), default="制度/规范")
    version: Mapped[int] = mapped_column(Integer, default=1)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing/ready/failed
    uploaded_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    chunks: Mapped[list[KbChunk]] = relationship(back_populates="document", cascade="all, delete-orphan")


class KbChunk(Base):
    __tablename__ = "kb_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("kb_documents.id", ondelete="CASCADE"))
    chunk_no: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    vector: Mapped[bytes | None] = mapped_column(Text, nullable=True)  # float32 hex（复用文本列避免 BLOB 兼容问题）
    document: Mapped[KbDocument] = relationship(back_populates="chunks")


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


DEFAULT_PROMPT = (
    "你是会展场馆档期排期助手，只根据提供的排期制度条文回答，"
    "仅输出档期冲突、缺项清单与对应来源位置，"
    "严禁自动锁档、签约、承诺场地容量，"
    "不确定时明确说「制度中未找到对应口径，请人工复核」，"
    "回答末尾附引用来源，不得自行编造排期规则。"
)

# 渲染层强制追加，不受模板编辑影响（人工复核底线）
FIXED_FOOTER = "⚠️ 以上为系统候选提示，最终结论需要人工复核确认。"


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    username: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(128), default="新会话")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, index=True)
    messages: Mapped[list[ChatMessage]] = relationship(back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    """一轮问答的完整审计留痕：提问原文 / 召回片段 / 大模型原始回答全文。"""
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    retrieved_chunks: Mapped[str] = mapped_column(Text, default="[]")  # JSON：[{source,doc_id,doc_version,chunk_no,text}]
    llm_raw_answer: Mapped[str] = mapped_column(Text, default="")
    prompt_template_version: Mapped[int] = mapped_column(Integer, default=0)
    mode: Mapped[str] = mapped_column(String(16), default="qa")  # qa=普通问答 / agent=智能体（含工具轨迹）
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, index=True)
    # 风险标记（审计）
    flag_type: Mapped[str | None] = mapped_column(String(32), nullable=True)   # conflict_misjudge/overcommit/invalid_rule
    flag_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    flagged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    flagged_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    session: Mapped[ChatSession] = relationship(back_populates="messages")


class ScheduleDraft(Base):
    __tablename__ = "schedule_drafts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    username: Mapped[str] = mapped_column(String(64))
    project_name: Mapped[str] = mapped_column(String(128))
    venue: Mapped[str] = mapped_column(String(64))
    event_start: Mapped[str] = mapped_column(String(32))
    event_end: Mapped[str] = mapped_column(String(32))
    dock_no: Mapped[str] = mapped_column(String(32), default="")
    load_in_start: Mapped[str] = mapped_column(String(32), default="")
    load_out_end: Mapped[str] = mapped_column(String(32), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # 仅草稿，绝不自动锁定
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class AgentRunLog(Base):
    """Agent 决策日志表（提示词7 · 第2项）：每轮/每步工具调用全量留痕。

    用途：阶段 5 的轨迹分析——统计各工具调用频次、耗时分布、被拦截的跳步比例、
    中断等待确认时长等。一行 = 一次工具调用（含被拒绝/中断的调用）。
    """
    __tablename__ = "agent_run_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)          # 一次智能体回合的唯一标识
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    round_no: Mapped[int] = mapped_column(Integer, default=0)            # 第几轮模型决策（1 起）
    step: Mapped[int] = mapped_column(Integer, default=0)                # run 内全局步号（跨轮递增）
    tool_name: Mapped[str] = mapped_column(String(64))                   # 工具名
    arguments: Mapped[str] = mapped_column(Text, default="{}")           # 入参 JSON（超长截断）
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)         # 执行耗时（毫秒）
    status: Mapped[str] = mapped_column(String(24), default="ok")        # ok/blocked/denied/error/pending_confirm/rejected/confirmed
    result_summary: Mapped[str] = mapped_column(Text, default="")        # 结果摘要（JSON，≤500 字符）
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, index=True)


class AgentConfirmation(Base):
    """人工确认单（提示词7 · 第3项）：create_schedule_draft 执行前的强制中断记录。

    生命周期：pending（中断挂起）→ confirmed / rejected / expired。
    恢复执行时不复用中断前的 precheck_token，而是对草稿**重新**跑两道校验——
    既保证凭据新鲜（HMAC 5 分钟有效期），也能捕获确认等待期间档期变化导致的
    新冲突（等待窗口内他人确认了冲突档期，恢复时会重新检出并拒绝）。
    """
    __tablename__ = "agent_confirmations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)   # 提交人（须与确认人一致）
    username: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)     # 中断时对应的问答留痕 id
    draft: Mapped[str] = mapped_column(Text, default="{}")                     # 挂起的草稿内容 JSON（确认后不再可改）
    question: Mapped[str] = mapped_column(Text, default="")                    # 触发中断的原始提问
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/confirmed/rejected
    draft_id: Mapped[int | None] = mapped_column(Integer, nullable=True)       # 确认后创建的草稿 id
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class ScheduleSlot(Base):
    """已确认档期快照（冲突检测的判定基准）。confirmed 仅能由人工产生。"""
    __tablename__ = "schedule_slots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_no: Mapped[str] = mapped_column(String(64), index=True)
    project_name: Mapped[str] = mapped_column(String(128))
    venue: Mapped[str] = mapped_column(String(64))
    event_start: Mapped[str] = mapped_column(String(32))
    event_end: Mapped[str] = mapped_column(String(32))
    dock_no: Mapped[str] = mapped_column(String(32), default="")
    load_in_start: Mapped[str] = mapped_column(String(32), default="")
    load_out_end: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    source: Mapped[str] = mapped_column(String(32), default="manual")


def init_db() -> None:
    """建表 + 种子数据（默认管理员/协调员/员工 + 默认提示词模板 + 演示确认档期）。"""
    from auth import hash_password  # noqa: PLC0415

    Base.metadata.create_all(engine)
    _ensure_columns()
    with SessionLocal() as db:
        seed_users = [("admin", "admin123", "admin"), ("coord", "coord123", "coordinator"),
                      ("staff", "staff123", "employee")]
        existing = {u.username for u in db.query(User).all()}
        for uname, pwd, role in seed_users:
            if uname not in existing:
                db.add(User(username=uname, password_hash=hash_password(pwd), role=role))
        if not db.query(PromptTemplate).count():
            db.add(PromptTemplate(version=1, content=DEFAULT_PROMPT, is_active=True, updated_by="system"))
        if not db.query(ScheduleSlot).count():
            db.add(ScheduleSlot(
                request_no="A001", project_name="春季设备展", venue="主馆 Hall-A",
                event_start="2026-11-18 09:00", event_end="2026-11-20 17:00",
                dock_no="B", load_in_start="2026-11-16 08:00", load_out_end="2026-11-21 12:00",
                source="演示种子数据"))
        db.commit()


def _ensure_columns() -> None:
    """SQLite 轻量迁移：为旧表补新增列。"""
    from sqlalchemy import text

    needed = {
        "chat_messages": {"mode": "VARCHAR(16) DEFAULT 'qa'"},
    }
    with engine.connect() as conn:
        for table, cols in needed.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for col, ddl in cols.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
        conn.commit()
