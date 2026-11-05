# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.1] - 2026-11-06

### Added
- 文档：`15-设计决策与答辩备忘`（八大设计决策问答、追问兜底口径、演示日检查清单），README 文档导航同步
- 验证：干净环境重装依赖后实跑——单测 12/12 OK、plumbing 评测 30/30 PASS（端口 8501 实跑留痕）

## [1.3.0] - 2026-11-05

### Added
- `src/admin/export.py`：草稿导出 Markdown 交接单（含交接确认清单）、ICS 日历（TENTATIVE 占位、标题带【草稿·不生效】）、冲突候选 Markdown 报告、Agent 轨迹统计聚合
- API：`GET /api/drafts/{id}/export.md`、`GET /api/drafts/{id}/export.ics`、`GET /api/admin/audit/agent-stats`（草稿导出按角色鉴权，非本人草稿 403）
- `tests/test_export.py`：12 条单元测试（字段渲染/ICS 结构/特殊字符转义/非法时间/统计聚合）
- 文档：`12-测试与验证报告`（实跑留痕）、`13-演示操作手册`（分角色演示脚本）、`14-提交材料清单`（全流程对照）

### Fixed
- 评测器 `http_json` 在 Windows 下被系统注册表代理劫持，`127.0.0.1` 请求报 502 / 连接拒绝；改为 `ProxyHandler({})` 强制直连，评测恢复 30/30

### Changed
- README 重写：补系统架构图、六工具执行循环、RBAC 表、端口说明、评测结果、01–14 文档导航

## [1.2.0] - 2026-10-30

### Added
- Agent Harness 执行循环：5 轮上限、`agent_run_logs` 决策日志表、`create_schedule_draft` 强制中断 + 人工确认单（`agent_confirmations`）、SSE 流式
- 双 precheck token（HMAC，5 分钟）防跳步；确认后重跑缺项/冲突双校验
- 30 条评测用例（制度问答/工具链/违规意图/多轮上下文）+ 执行器四道判分关卡

### Fixed
- 登录页缺少 `api()` 帮助函数导致 `api is not defined`

## [1.1.0] - 2026-10-30

### Added
- 管理后台（FastAPI + Jinja2 SSR）：登录 RBAC、知识库上传/切片/重建索引、提示词模板版本管理、档期问答、对话审计（风险标记）、草稿、用户管理
- 智能体升级：6 工具 Function Calling（search_kb / get_schedule / check_missing_fields / check_conflict / create_schedule_draft / submit_for_review）+ ReAct 编排器 + 协调员角色

## [0.1.0] - 2026-10-23

### Added
- Initial project scaffold with `src/`, `tests/`, `docs/`, `examples/`, `scripts/`, `config/`, `data/`, `.github/`
- Docs: business discovery, industry analysis, current-state & to-be flowcharts, proposal & quote
- README with project overview, installation and usage
- MIT License
- CI/CD workflow template (GitHub Actions)
