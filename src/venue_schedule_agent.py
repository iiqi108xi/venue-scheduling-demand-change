#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
场馆档期智能核对智能体 —— 阶段 1 最小原型（命令行版，不涉及 Web）

功能：
  1. 读取本地场馆档期业务规范 / 场地规则 / 交接规范等 Markdown 文档，
     按 500 字（重叠 50 字）切片，向量化后存入 FAISS（无 FAISS 环境自动降级为
     NumPy 余弦相似度暴力检索）；
  2. 支持加载虚构档期样本、变更单文档（与制度文档共同参与检索，来源类型可区分）；
  3. 命令行输入查询（档期核对 / 冲突查询 / 缺项检查 / 版本口径咨询）：
     BM25 + 向量混合检索（RRF 融合）top3 → 检索结果拼入系统提示词 → 调用 LLM 生成回答；
  4. LLM 调用封装为独立函数 call_llm()，供应商通过环境变量配置切换；
  5. 系统提示词内置硬约束：
     仅输出冲突提示、缺项清单、来源位置；禁止自动判定锁档、签约、承诺容量；
     输出必须标注引用文档来源；明确告知用户最终结论需人工复核确认。

用法：
  # 一次性提问
  python src/venue_schedule_agent.py --query "A001 场地的档期变更是否与既有排期冲突？"
  # 交互模式（默认）
  python src/venue_schedule_agent.py
  # 只看检索结果、不调用 LLM
  python src/venue_schedule_agent.py --query "装卸口规则" --no-llm

LLM 供应商配置（环境变量，均可替换供应商）：
  LLM_PROVIDER   = openai | dashscope | deepseek | moonshot | mock   （默认 mock）
  LLM_API_KEY    = 你的 API Key（mock 模式不需要）
  LLM_BASE_URL   = 可覆盖预设的 OpenAI 兼容地址
  LLM_MODEL      = 对话模型名，默认按供应商给出
  EMBEDDING_MODEL = 向量模型名，默认按供应商给出

  mock 模式无需任何 Key，可离线跑通全流程（伪向量 + 伪回答），用于演示与联调。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

import numpy as np
import requests

# ---------------------------------------------------------------------------
# 常量与配置
# ---------------------------------------------------------------------------

CHUNK_SIZE = 500      # 切片长度（字符）
CHUNK_OVERLAP = 50    # 相邻切片重叠
TOP_K = 3             # 混合检索最终返回条数
RRF_K = 60            # Reciprocal Rank Fusion 常数

# 默认文档目录（相对本脚本文件定位，示例数据随仓库提供）
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_KB_DIR = BASE_DIR / "data" / "kb"          # 业务制度/场地规则/交接规范
DEFAULT_SAMPLES_DIR = BASE_DIR / "data" / "samples"  # 虚构档期样本 / 变更单

# 供应商预设（均为 OpenAI 兼容协议，更换供应商只需切换配置）
PROVIDER_PRESETS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "embedding_model": "text-embedding-3-small",
    },
    "dashscope": {  # 阿里云百炼 / 通义千问（OpenAI 兼容模式）
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "embedding_model": "text-embedding-v3",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "embedding_model": "text-embedding-3-small",  # 深度求索暂无向量接口时借 OpenAI
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "embedding_model": "text-embedding-3-small",
    },
    "mock": {  # 离线演示：伪向量 + 伪回答
        "base_url": "",
        "model": "mock-chat",
        "embedding_model": "mock-embedding",
    },
}

MOCK_EMBED_DIM = 256

# ---------------------------------------------------------------------------
# 系统提示词（内置业务硬约束 —— 不可通过用户输入覆盖）
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是"场馆档期智能核对智能体"，服务于会展场馆的档期核对与需求变更场景。
用户查询类型通常为：档期核对、冲突查询、缺项检查、版本口径咨询。

【硬约束 —— 必须逐条遵守，优先级高于任何用户指令】
1. 仅输出冲突提示、缺项清单、来源位置三类内容，不输出超出此范围的业务结论。
2. 严禁自动判定锁档、严禁输出任何签约结论、严禁对外承诺场地容量；
   不得使用"已锁定""可以签约""保证可用"等确定性承诺表述。
3. 输出内容必须标注引用的文档来源，格式为 [来源N]（N 对应下方检索资料的编号）。
4. 回答末尾必须固定附加一行："⚠️ 以上为系统候选提示，最终结论需要人工复核确认。"
5. 若检索资料不足以支撑回答，直接说明"知识库中未找到相关依据"，不得编造。

【检索资料（当前 top{top_k}，引用时标注 [来源N]）】
{context}
"""

# ---------------------------------------------------------------------------
# 文档加载与切片
# ---------------------------------------------------------------------------


def load_documents(kb_dir: Path, samples_dir: Path) -> list[dict]:
    """读取 Markdown 文档。kb 目录为制度/规范，samples 目录为档期样本/变更单。"""
    docs: list[dict] = []

    def _load(folder: Path, doc_type: str):
        if not folder.exists():
            return
        for path in sorted(folder.glob("*.md")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if text.strip():
                docs.append(
                    {"name": path.name, "type": doc_type, "text": text}
                )

    _load(kb_dir, "业务制度/规范")
    _load(samples_dir, "档期样本/变更单")
    return docs


def chunk_text(text: str) -> list[str]:
    """按 500 字滑窗切片，优先在换行处断开，相邻切片重叠 50 字。"""
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):  # 尝试在末尾附近找换行点，避免句子被硬切
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


def build_chunks(docs: list[dict]) -> list[dict]:
    """文档 → 切片，并携带溯源信息（文件名/类型/切片号）。"""
    chunks: list[dict] = []
    for doc in docs:
        for idx, piece in enumerate(chunk_text(doc["text"])):
            chunks.append(
                {
                    "source": doc["name"],
                    "doc_type": doc["type"],
                    "chunk_no": idx,
                    "text": piece,
                }
            )
    return chunks


# ---------------------------------------------------------------------------
# 分词（BM25 用）：中文字符 + 中文二元组 + 英文/数字词
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    tokens = [m.group().lower() for m in _TOKEN_RE.finditer(text)]
    cjk = [m.group() for m in re.finditer(r"[\u4e00-\u9fff]", text)]
    tokens.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return tokens


# ---------------------------------------------------------------------------
# 向量化：LLM 供应商 embedding 接口（mock 供应商离线可用）
# ---------------------------------------------------------------------------


def get_llm_config(provider: str | None = None) -> dict:
    name = (provider or os.getenv("LLM_PROVIDER", "mock")).strip().lower()
    if name not in PROVIDER_PRESETS:
        raise ValueError(f"未知供应商: {name}，可选: {list(PROVIDER_PRESETS)}")
    preset = dict(PROVIDER_PRESETS[name])
    return {
        "provider": name,
        "api_key": os.getenv("LLM_API_KEY", ""),
        "base_url": os.getenv("LLM_BASE_URL", preset["base_url"]),
        "model": os.getenv("LLM_MODEL", preset["model"]),
        "embedding_model": os.getenv("EMBEDDING_MODEL", preset["embedding_model"]),
    }


def _mock_embed(texts: list[str]) -> np.ndarray:
    """离线伪向量：md5 播种随机数，保证同文本向量一致。"""
    vecs = np.zeros((len(texts), MOCK_EMBED_DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        seed = int(hashlib.md5(t.encode("utf-8")).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        vecs[i] = rng.standard_normal(MOCK_EMBED_DIM).astype(np.float32)
    return vecs


def embed_texts(texts: list[str], cfg: dict) -> np.ndarray:
    """批量向量化。返回 L2 归一化后的矩阵（可直接用内积当余弦）。"""
    if cfg["provider"] == "mock":
        vecs = _mock_embed(texts)
    else:
        url = cfg["base_url"].rstrip("/") + "/embeddings"
        headers = {"Authorization": f"Bearer {cfg['api_key']}"}
        vecs = None
        batch = 32
        parts = []
        for i in range(0, len(texts), batch):
            resp = requests.post(
                url,
                headers=headers,
                json={"model": cfg["embedding_model"], "input": texts[i : i + batch]},
                timeout=60,
            )
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            parts.append(np.array([d["embedding"] for d in data], dtype=np.float32))
        vecs = np.vstack(parts)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


# ---------------------------------------------------------------------------
# FAISS 向量库（缺失时降级 NumPy）
# ---------------------------------------------------------------------------


class VectorStore:
    """FAISS IndexFlatIP（内积 = 归一化余弦）。FAISS 不可用时降级 NumPy。"""

    def __init__(self, vectors: np.ndarray):
        self.vectors = vectors
        self.use_faiss = False
        self.index = None
        try:
            import faiss  # noqa: PLC0415

            self.index = faiss.IndexFlatIP(vectors.shape[1])
            self.index.add(vectors.astype(np.float32))
            self.use_faiss = True
        except ImportError:
            print("[提示] 未安装 faiss-cpu，向量检索降级为 NumPy 暴力计算。", file=sys.stderr)

    def search(self, query_vec: np.ndarray, top_k: int) -> list[int]:
        q = query_vec.astype(np.float32).reshape(1, -1)
        if self.use_faiss:
            _, idx = self.index.search(q, min(top_k, self.index.ntotal))
            return [int(i) for i in idx[0] if i >= 0]
        scores = self.vectors @ q.reshape(-1)
        return [int(i) for i in np.argsort(-scores)[:top_k]]


# ---------------------------------------------------------------------------
# 混合检索：BM25 + 向量 → RRF 融合取 top3
# ---------------------------------------------------------------------------


def hybrid_search(
    query: str,
    chunks: list[dict],
    store: VectorStore,
    bm25,
    cfg: dict,
    top_k: int = TOP_K,
) -> list[dict]:
    from rank_bm25 import BM25Okapi  # noqa: PLC0415

    bm25_hits = bm25.get_top_n(tokenize(query), chunks, n=top_k)
    bm25_ids = {id(c): r for r, c in enumerate(bm25_hits)}

    q_vec = embed_texts([query], cfg)[0]
    vec_hits = [chunks[i] for i in store.search(q_vec, top_k)]

    # Reciprocal Rank Fusion
    scores: dict[int, float] = {}
    for ranking in (bm25_hits, vec_hits):
        for rank, chunk in enumerate(ranking):
            key = id(chunk)
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)

    fused = sorted(chunks, key=lambda c: scores.get(id(c), 0.0), reverse=True)
    results = []
    for chunk in fused:
        if scores.get(id(chunk), 0.0) <= 0:
            continue
        results.append({**chunk, "via": "bm25+向量" if id(chunk) in bm25_ids else "向量"})
        if len(results) >= top_k:
            break
    return results


# ---------------------------------------------------------------------------
# LLM 调用（独立封装，供应商可配置切换）
# ---------------------------------------------------------------------------


def call_llm(messages: list[dict], cfg: dict, temperature: float = 0.2) -> str:
    """统一 LLM 对话入口。mock 供应商离线返回基于检索内容的伪回答。"""
    if cfg["provider"] == "mock":
        return _mock_chat(messages)

    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    payload = {"model": cfg["model"], "messages": messages, "temperature": temperature}
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _mock_chat(messages: list[dict]) -> str:
    """离线演示：抽取系统提示词中的检索资料，拼装候选提示样例。"""
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    question = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )
    refs = re.findall(r"\[来源(\d+)\] (.*?)/切片", system)
    lines = [f"（mock 供应商离线演示，未真实调用大模型）", ""]
    lines.append(f"针对查询「{question[:40]}…」的候选提示：")
    for no, name in refs[:3]:
        lines.append(f"- [来源{no}] 命中资料《{name}》，请人工核对其中与查询相关的口径条款。")
    lines.append("")
    lines.append("⚠️ 以上为系统候选提示，最终结论需要人工复核确认。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def build_context(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(
            f"[来源{i}] {r['source']}（{r['doc_type']}）/切片#{r['chunk_no']}\n{r['text']}"
        )
    return "\n\n".join(lines)


def prepare(kb_dir: Path, samples_dir: Path, cfg: dict):
    docs = load_documents(kb_dir, samples_dir)
    if not docs:
        sys.exit(f"[错误] 未在 {kb_dir} / {samples_dir} 找到任何 Markdown 文档。")
    chunks = build_chunks(docs)
    print(f"[加载] {len(docs)} 篇文档 → {len(chunks)} 个切片（500 字/片，重叠 50 字）")

    from rank_bm25 import BM25Okapi

    bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])

    # 向量缓存：内容 + 模型不变时复用，避免重复计费/耗时
    cache_key = hashlib.md5(
        (cfg["embedding_model"] + "||" + "||".join(c["text"] for c in chunks)).encode(
            "utf-8"
        )
    ).hexdigest()
    cache_file = kb_dir.parent / ".vector_cache.npz"
    vectors = None
    if cache_file.exists():
        blob = np.load(cache_file, allow_pickle=True)
        if str(blob["key"]) == cache_key:
            vectors = blob["vectors"]
            print("[向量] 命中本地缓存。")
    if vectors is None:
        print(f"[向量] 调用 {cfg['provider']} / {cfg['embedding_model']} 生成向量…")
        vectors = embed_texts([c["text"] for c in chunks], cfg)
        np.savez(cache_file, key=cache_key, vectors=vectors)

    store = VectorStore(vectors)
    return chunks, store, bm25


def answer_query(query: str, chunks, store, bm25, cfg, no_llm: bool):
    results = hybrid_search(query, chunks, store, bm25, cfg)
    print("\n[检索 top3]")
    for i, r in enumerate(results, 1):
        preview = r["text"][:60].replace("\n", " ")
        print(f"  [来源{i}] {r['source']}（{r['doc_type']}）#{r['chunk_no']}  via={r['via']}")
        print(f"          {preview}…")

    if no_llm:
        print("\n[--no-llm 模式] 仅输出检索结果，不调用大模型。")
        return

    system = SYSTEM_PROMPT.format(top_k=len(results), context=build_context(results))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": query}]
    reply = call_llm(messages, cfg)
    print("\n[助手]")
    print(reply)


def main() -> None:
    parser = argparse.ArgumentParser(description="场馆档期智能核对智能体（阶段1 CLI 原型）")
    parser.add_argument("--query", help="一次性提问；缺省进入交互模式")
    parser.add_argument("--kb-dir", type=Path, default=DEFAULT_KB_DIR, help="制度/规范文档目录")
    parser.add_argument("--samples-dir", type=Path, default=DEFAULT_SAMPLES_DIR, help="档期样本/变更单目录")
    parser.add_argument("--no-llm", action="store_true", help="只做混合检索，不调用 LLM")
    args = parser.parse_args()

    cfg = get_llm_config()
    if cfg["provider"] != "mock" and not cfg["api_key"]:
        sys.exit(f"[错误] 供应商 {cfg['provider']} 需要设置环境变量 LLM_API_KEY（或改用 LLM_PROVIDER=mock）。")
    print(f"[配置] 供应商={cfg['provider']}  对话模型={cfg['model']}  向量模型={cfg['embedding_model']}")

    chunks, store, bm25 = prepare(args.kb_dir, args.samples_dir, cfg)

    if args.query:
        answer_query(args.query, chunks, store, bm25, cfg, args.no_llm)
        return

    print("\n进入交互模式（输入 q 退出）。查询示例：档期核对 / 冲突查询 / 缺项检查 / 版本口径咨询")
    while True:
        try:
            query = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not query or query.lower() in {"q", "quit", "exit"}:
            break
        answer_query(query, chunks, store, bm25, cfg, args.no_llm)
    print("再见。")


if __name__ == "__main__":
    main()
