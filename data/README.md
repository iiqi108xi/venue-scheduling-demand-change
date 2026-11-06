# data/ · 数据与数据库

本目录存放**可交付的演示数据库**与结构化数据，随仓库一起提交，评审时无需运行任何脚本即可核对数据模型。

## 1. 数据库文件

| 文件 | 说明 |
|------|------|
| `venue_scheduling.db` | SQLite 演示库（约 200 KB），含建表结构 + 种子数据 + 知识库向量，**已提交到仓库** |
| `kb/01_场馆排期与变更管理制度.md` | 知识库源文件（合成训练资料，十条制度），用于 RAG 检索与引用演示 |
| `1_六人访谈记录.json` / `.csv` | 六角色访谈结构化数据（与 `docs/1_六人访谈记录.md` 同源） |

运行时数据库默认在 `src/admin/data/admin.db`（该目录被 `.gitignore` 排除，避免把运行痕迹提交上去）。两者结构完全一致，可通过环境变量切换：

```bash
# 用交付库启动（推荐评审/演示时使用）
DB_PATH=data/venue_scheduling.db python -m uvicorn main:app --port 8500   # 在 src/admin 目录下执行
```

## 2. 表结构（10 张表）

| 表 | 用途 | 种子行数 |
|----|------|---------|
| `users` | 账号与角色（admin / coordinator / employee） | 3 |
| `kb_documents` | 知识库文档元数据（文件名、版本、切片数、状态） | 5 |
| `kb_chunks` | 知识库切片 + 向量（float32 hex），RAG 检索用 | 24 |
| `prompt_templates` | 提示词模板版本（含"严禁锁档/签约"底线） | 1 |
| `schedule_slots` | 已确认档期快照（冲突判定基准） | 3 |
| `schedule_drafts` | 档期草稿（状态恒为 draft，绝不自动锁定） | 0 |
| `agent_confirmations` | 人工确认单（pending → confirmed / rejected） | 0 |
| `agent_run_logs` | 每步工具调用留痕（工具名/耗时/状态） | 0 |
| `chat_sessions` / `chat_messages` | 会话与问答审计（提问原文、召回片段、模型原文） | 0 |

## 3. 演示账号

| 账号 | 密码 | 角色 | 权限要点 |
|------|------|------|---------|
| `admin` | `admin123` | 管理员 | 全部功能，可确认草稿、管理知识库与账号 |
| `coord` | `coord123` | 协调员 | 可触发草稿创建与确认（试点期白名单） |
| `staff` | `staff123` | 一线员工 | 问答与查询；创建草稿会被角色门禁拒绝（权限场景用例） |

> 密码为演示用弱口令，正式环境须改为强口令并启用 HTTPS。

## 4. 重建数据库

```bash
python scripts/init_db.py                 # 重建 data/venue_scheduling.db
python scripts/init_db.py --db data/x.db  # 指定输出路径
```

脚本会建表、写入种子账号/提示词/档期，并把 `data/kb/*.md` 与 `docs/briefing/*.html` 切片、向量化后写入知识库。制度文件按「条」切片，因此问答引用能精确落到具体条款（如"第五条 占档确认与人工门"）。

## 5. 已验证

- 表结构：10 张表全部创建成功，`users=3`、`kb_documents=5`、`kb_chunks=24`、`schedule_slots=3`
- 检索：问「占档确认前必须核对哪三项？」命中 `01_场馆排期与变更管理制度.md` 第 5 条，回答附「来源1/2/3」并强制追加"需人工复核"脚注
- 与代码一致：`src/admin/database.py` 的 ORM 定义即建表依据，无手工建表脚本，避免结构漂移
