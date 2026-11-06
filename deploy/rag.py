# -*- coding: utf-8 -*-
"""RAG 管线：解析（md/pdf）→ 500 字切片 → 向量化 → FAISS（全量重建策略）→ BM25+向量混合检索。

向量持久化在 kb_chunks.vector（float32 hex），FAISS 索引在内存中随增删重建（演示规模足够），
供应商配置复用阶段1的环境变量（LLM_PROVIDER / LLM_API_KEY / ...，mock 可离线）。
"""
from __future__ import annotations

import hashlib
import io
import re

import numpy as np

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
TOP_K = 3
RRF_K = 60
MOCK_EMBED_DIM = 256

# ---------------------------------------------------------------------------
# 供应商配置（与阶段1一致）
# ---------------------------------------------------------------------------

PROVIDER_PRESETS = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "embedding_model": "text-embedding-3-small"},
    "dashscope": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "embedding_model": "text-embedding-v3"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "embedding_model": "text-embedding-3-small"},
    "moonshot": {"base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "embedding_model": "text-embedding-3-small"},
    "mock": {"base_url": "", "model": "mock-chat", "embedding_model": "mock-embedding"},
}


def get_llm_config() -> dict:
    import os

    name = os.getenv("LLM_PROVIDER", "mock").strip().lower()
    preset = dict(PROVIDER_PRESETS.get(name, PROVIDER_PRESETS["mock"]))
    return {
        "provider": name,
        "api_key": os.getenv("LLM_API_KEY", ""),
        "base_url": os.getenv("LLM_BASE_URL", preset["base_url"]),
        "model": os.getenv("LLM_MODEL", preset["model"]),
        "embedding_model": os.getenv("EMBEDDING_MODEL", preset["embedding_model"]),
    }


# ---------------------------------------------------------------------------
# 解析与切片
# ---------------------------------------------------------------------------

def parse_file(filename: str, data: bytes) -> str:
    """Markdown / PDF 文本解析。"""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return data.decode("utf-8", errors="ignore")


def chunk_text(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):
            nl = text.rfind("\n", end - 80, end)
            if nl > start + 200:
                end = nl
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = end - CHUNK_OVERLAP
    return chunks


# ---------------------------------------------------------------------------
# 向量化
# ---------------------------------------------------------------------------

def _mock_embed(texts: list[str]) -> np.ndarray:
    vecs = np.zeros((len(texts), MOCK_EMBED_DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        seed = int(hashlib.md5(t.encode("utf-8")).hexdigest()[:8], 16)
        vecs[i] = np.random.default_rng(seed).standard_normal(MOCK_EMBED_DIM)
    return vecs.astype(np.float32)


def embed_texts(texts: list[str], cfg: dict | None = None) -> np.ndarray:
    import requests

    cfg = cfg or get_llm_config()
    if cfg["provider"] == "mock":
        vecs = _mock_embed(texts)
    else:
        parts = []
        for i in range(0, len(texts), 32):
            resp = requests.post(
                cfg["base_url"].rstrip("/") + "/embeddings",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json={"model": cfg["embedding_model"], "input": texts[i : i + 32]},
                timeout=60,
            )
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            parts.append(np.array([d["embedding"] for d in data], dtype=np.float32))
        vecs = np.vstack(parts)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vecs / norms).astype(np.float32)


def vec_to_hex(vec: np.ndarray) -> str:
    return vec.astype(np.float32).tobytes().hex()


def hex_to_vec(hexstr: str) -> np.ndarray:
    return np.frombuffer(bytes.fromhex(hexstr), dtype=np.float32)


# ---------------------------------------------------------------------------
# FAISS 索引（全量重建策略：演示规模小，增删后重建成本可忽略）
# ---------------------------------------------------------------------------

class FaissIndex:
    """row = kb_chunks.id 的位置表；faiss_pos → chunk_id 映射。"""

    def __init__(self) -> None:
        self.chunk_ids: list[int] = []
        self.matrix = np.zeros((0, MOCK_EMBED_DIM), dtype=np.float32)
        self.index = None

    def rebuild(self, rows: list[tuple[int, np.ndarray]]) -> None:
        self.chunk_ids = [rid for rid, _ in rows]
        try:
            import faiss

            if rows:
                self.matrix = np.vstack([v for _, v in rows])
            else:
                self.matrix = np.zeros((0, MOCK_EMBED_DIM), dtype=np.float32)
            self.index = faiss.IndexFlatIP(self.matrix.shape[1] or MOCK_EMBED_DIM)
            if len(rows):
                self.index.add(self.matrix)
        except ImportError:  # 无 faiss 时降级 NumPy
            self.index = None
            self.matrix = np.vstack([v for _, v in rows]) if rows else np.zeros((0, MOCK_EMBED_DIM), dtype=np.float32)

    def search(self, query_vec: np.ndarray, top_k: int) -> list[int]:
        if not self.chunk_ids:
            return []
        q = query_vec.reshape(1, -1).astype(np.float32)
        k = min(top_k, len(self.chunk_ids))
        if self.index is not None:
            _, idx = self.index.search(q, k)
            return [self.chunk_ids[i] for i in idx[0] if 0 <= i < len(self.chunk_ids)]
        scores = self.matrix @ q.reshape(-1)
        return [self.chunk_ids[i] for i in np.argsort(-scores)[:k]]


# ---------------------------------------------------------------------------
# BM25 分词与混合检索
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    tokens = [m.group().lower() for m in _TOKEN_RE.finditer(text)]
    cjk = [m.group() for m in re.finditer(r"[\u4e00-\u9fff]", text)]
    tokens.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return tokens


def hybrid_search(query: str, chunks: list[dict], index: FaissIndex, cfg: dict) -> list[dict]:
    """chunks: [{id, source, chunk_no, content}]；返回 top3（BM25+向量 RRF 融合）。"""
    from rank_bm25 import BM25Okapi

    top = max(TOP_K, 1)
    if not chunks:
        return []

    bm25 = BM25Okapi([tokenize(c["content"]) for c in chunks])
    bm25_hits = bm25.get_top_n(tokenize(query), chunks, n=top)
    q_vec = embed_texts([query], cfg)[0]
    vec_ids = index.search(q_vec, top)
    by_id = {c["id"]: c for c in chunks}
    vec_hits = [by_id[i] for i in vec_ids if i in by_id]

    scores: dict[int, float] = {}
    for ranking in (bm25_hits, vec_hits):
        for rank, chunk in enumerate(ranking):
            scores[chunk["id"]] = scores.get(chunk["id"], 0.0) + 1.0 / (RRF_K + rank + 1)
    fused = sorted(chunks, key=lambda c: scores.get(c["id"], 0.0), reverse=True)
    return [c for c in fused if scores.get(c["id"], 0.0) > 0][:top]


# ---------------------------------------------------------------------------
# LLM 对话（独立封装，供应商可配置切换）
# ---------------------------------------------------------------------------

def call_llm(messages: list[dict], cfg: dict | None = None) -> str:
    import requests

    cfg = cfg or get_llm_config()
    if cfg["provider"] == "mock":
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        refs = re.findall(r"\[来源(\d+)\] (.*?)/切片", system)
        lines = ["（mock 供应商离线演示，未真实调用大模型）", ""]
        lines.append(f"针对查询「{question[:40]}」的候选提示：")
        for no, name in refs[:3]:
            lines.append(f"- [来源{no}] 命中资料《{name}》，请人工核对其中与查询相关的口径条款。")
        lines.append("")
        lines.append("⚠️ 以上为系统候选提示，最终结论需要人工复核确认。")
        return "\n".join(lines)

    resp = requests.post(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {cfg['api_key']}"},
        json={"model": cfg["model"], "messages": messages, "temperature": 0.2},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
