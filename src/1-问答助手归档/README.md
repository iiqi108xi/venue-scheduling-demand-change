# 1 · 问答助手归档（老师清单 src-1）

问答助手（RAG 问答 / 智能体模式）的**完整会话留痕归档**。系统级保障：每一轮问答在落库时即同步留存提问原文、召回片段（含文档名/文档版本/切片号）、大模型原始回答全文、提问人与角色、提示词模板版本、时间戳——归档即数据库原文导出，非事后补写。

## 目录内容

| 文件 | 说明 |
|------|------|
| `对话归档.md` | 人类可读会话实录：按会话分组的问答全文（提问 / 召回片段来源 / 模型原文 / 留痕元数据），附 Agent 工具轨迹节选 |
| `chat_messages.json` | `chat_messages` 表机器可读原始留痕（32 条消息，含召回片段 JSON） |
| `agent_run_logs.json` | `agent_run_logs` 表工具执行轨迹（65 条：轮次/工具/参数/耗时/状态/结果摘要） |
| `截图-问答页.png` | 问答页真实提问截图（含来源标注 + "最终结论需要人工复核确认"强制脚注） |
| `截图-历史会话.png` | "我的问答记录"页：按会话归档的历史列表 |
| `截图-审计页.png` | 管理员审计页：全用户会话/消息/风险标记一览 |

## 留痕机制（代码位置）

- 表结构：`src/admin/database.py` → `ChatSession`（会话）/ `ChatMessage`（消息，含 `retrieved_chunks`、`llm_raw_answer`、`prompt_template_version`、风险标记字段）
- 写入时机：`src/admin/api.py` → `POST /api/chat`（问答）/ `POST /api/agent/run`（智能体，含工具轨迹）
- 查看界面：问答页 `/chat`、历史页 `/chat/history`、审计页 `/admin/audit`（管理员）

## 归档重新生成方式

```bash
# 1. 启动本地服务
cd src/admin && DB_PATH=../../data/venue_scheduling.db python -m uvicorn main:app --port 8511
# 2. 采集真实会话并导出（另开终端，仓库根目录）
python scripts/collect_qa_archive.py    # 跑会话 + 导出 JSON
python scripts/build_qa_archive.py      # 截图 + 生成对话归档.md
```
