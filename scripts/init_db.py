# -*- coding: utf-8 -*-
"""生成交付用 SQLite 数据库 data/venue_scheduling.db。

做四件事：
1. 建表（与 src/admin/database.py 的 ORM 完全一致）
2. 种子账号 admin / coord / staff、默认提示词模板、演示档期快照
3. 把 src/data/kb/*.md（业务规范）与 docs/2-项目基本简报/*.html（项目简报）切分、向量化后写入知识库表
4. 打印表行数，便于验收核对

用法：
    python scripts/init_db.py                # 生成 data/venue_scheduling.db
    python scripts/init_db.py --db data/demo.db
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "admin"))


def strip_html(text: str) -> str:
    text = re.sub(r"<script.*?</script>", "", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"&nbsp;?", " ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "venue_scheduling.db"))
    args = ap.parse_args()

    db_path = Path(args.db).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()  # 每次重建，保证可复现

    os.environ["DB_PATH"] = str(db_path)

    import database as db_mod  # noqa: PLC0415  必须在设置 DB_PATH 之后导入
    import rag  # noqa: PLC0415

    db_mod.init_db()

    sources: list[tuple[str, str, str]] = []  # (filename, doc_type, text)
    for p in sorted((ROOT / "src" / "data" / "kb").glob("*.md")):
        sources.append((p.name, "制度/规范", p.read_text(encoding="utf-8")))
    for p in sorted((ROOT / "data" / "kb").glob("*.md")):
        sources.append((p.name, "制度/规范", p.read_text(encoding="utf-8")))
    for p in sorted((ROOT / "docs" / "2-项目基本简报").glob("*.html")):
        sources.append((p.name, "项目简报", strip_html(p.read_text(encoding="utf-8"))))

    cfg = rag.get_llm_config()
    with db_mod.SessionLocal() as db:
        db.query(db_mod.KbChunk).delete()
        db.query(db_mod.KbDocument).delete()
        db.commit()

        for filename, doc_type, body in sources:
            if not body.strip():
                continue
            if filename.endswith(".md"):
                # 制度文件按「条」切片，保证引用能落到具体条款
                pieces = [sec.strip() for sec in re.split(r"(?=^## )", body, flags=re.M) if sec.strip()]
            else:
                pieces = rag.chunk_text(body)
            vectors = rag.embed_texts(pieces, cfg)
            doc = db_mod.KbDocument(
                filename=filename, doc_type=doc_type, chunk_count=len(pieces),
                status="ready", uploaded_by="system",
            )
            db.add(doc)
            db.flush()
            for i, (piece, vec) in enumerate(zip(pieces, vectors)):
                db.add(db_mod.KbChunk(
                    document_id=doc.id, chunk_no=i, content=piece,
                    vector=rag.vec_to_hex(vec),
                ))
            print(f"  入库 {filename}: {len(pieces)} 片")

        # 额外的演示档期快照（冲突检测基准），已有 A001 时只补缺
        existing = {s.request_no for s in db.query(db_mod.ScheduleSlot).all()}
        demo_slots = [
            dict(request_no="A002", project_name="秋季招商会", venue="副馆 Hall-B",
                 event_start="2026-12-01 09:00", event_end="2026-12-03 17:00",
                 dock_no="D12", load_in_start="2026-11-30 08:00", load_out_end="2026-12-04 12:00",
                 source="演示种子数据"),
            dict(request_no="A003", project_name="智能制造论坛", venue="主馆 Hall-A",
                 event_start="2026-12-10 09:00", event_end="2026-12-11 17:00",
                 dock_no="D3", load_in_start="2026-12-09 08:00", load_out_end="2026-12-12 12:00",
                 source="演示种子数据"),
        ]
        for row in demo_slots:
            if row["request_no"] not in existing:
                db.add(db_mod.ScheduleSlot(**row))
        db.commit()

        print("\n=== 数据库就绪 ===")
        print(f"路径: {db_path}")
        print(f"大小: {db_path.stat().st_size / 1024:.0f} KB")
        for table in ("users", "kb_documents", "kb_chunks", "prompt_templates", "schedule_slots"):
            n = db.execute(text(f"select count(*) from {table}")).scalar()
            print(f"  {table}: {n} 行")


if __name__ == "__main__":
    main()
