# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.1] - 2026-11-06

### Added
- **data 数据库入库**：`data/venue_scheduling.db`（SQLite，10 张表，users 3 / kb_documents 5 / kb_chunks 24 / prompt_templates 1 / schedule_slots 3），随仓库提交，评审无需运行代码即可核对数据模型
- `scripts/init_db.py`：一键重建交付库（建表 + 种子账号/提示词/档期 + 知识库切片向量化），制度文件按「条」切片，引用可落到具体条款
- `data/kb/01_场馆排期与变更管理制度.md`：合成制度文本（十条），用于 RAG 检索与引用演示
- `data/README.md`：表结构、演示账号、重建方式、已验证结论
- `docs/00-验收索引.md`：老师九项 docs 要求 1→9 逐项对照（文件路径 / 规模 / 看点 / 自查结果）
- `database.py` 支持 `DB_PATH` 环境变量，可切换运行时库与交付库

### Fixed
- 知识库此前为空导致问答走兜底：已灌入 5 篇文档共 24 个切片，实测「占档确认前核对哪三项」命中制度第 5 条并附来源
- `.gitignore` 增加 `!data/*.db` 例外，避免交付库被误排除

## [1.5.0] - 2026-11-06

### Added
- docs-9：`9-上线交付运行手册.docx`（部署/账号/备份/故障/回退）
- Test-1：`16-场景测试方案-正常边界对抗权限故障.md/.docx`（23 条用例 100% PASS，量化判定）
- Test-2：`Test/01～05-*.docx` 五份可视化场景测试过程（11 张接口证据渲染图 + 8 张真实 UI 截图，Playwright 取证）
- Test-3：MVP 公网部署上线（FastAPI 沙箱单端口）+ `17-上线部署网站地址.md/.docx`（分享链接、演示账号、上线验证记录）
- 部署入口 `deploy/run.py`（监听 PORT 环境变量绑定 0.0.0.0）与 `scripts/ui_shots.py`、`scripts/build_test_docx.py` 取证脚本
- docs/14 提交材料清单增补「课堂要求 1–9 与 Test 三项对照」表

## [1.4.0] - 2026-11-06

### Added
- 课堂要求八件套（老师 1–8 条原始调研与文档交付物，README 文档导航新增对照表）：
  - 六人访谈记录（md 正文 + data/ 下 json/csv 结构化，25 组问答、f00–f55 事实编号）
  - 项目简报四件套（docs/briefing/：客户简报、现状信息请求表、业务发现访谈问题库、利益相关者资料与分工）
  - 典型事件采访记录 3 轮（txt）+ AI 整理版纪要（docx）
  - 行业分析文档（md + docx 双格式）
  - 流程图改造前 / 后高清 PNG（assets/figures/，六泳道 × 五阶段标准符号）
  - 需求分析方案 / 需求定义文档（docx：痛点 PL-1～5、AI 改造 AI-1～8 优先级、FR-1～8 规格、追溯矩阵、验收 AC-1～8）
  - 详细项目设计方案（docx：五设计原则、四层架构、模块 M1～M6、技术选型、四周试点计划、风险回退）
  - 三档报价与投入产出测算（docx：¥28,000 / ¥61,800 / ¥128,000；商业论证链五步：报价及范围→月运行成本→业务量/节省时间/采用率/兑现假设→首年净收益/ROI/回本期→待验证假设 A-1～6）
- 交互版演示页两个（examples/：六角色现状流程图、数智化方案与报价单）

### Merged
- 并入调研源材料分支（unrelated histories 合并），README/LICENSE/.gitignore/CHANGELOG 以主干为准

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
