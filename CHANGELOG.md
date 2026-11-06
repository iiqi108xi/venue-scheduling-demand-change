# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.6.2] - 2026-11-06

### Added（src 按老师清单编号）
- `src/1-问答助手归档/`：对话归档.md（32 条会话实录）、chat_messages.json、agent_run_logs.json（65 条工具轨迹）、问答/历史/审计 3 张截图、README
- `src/2-MVP项目/README.md`：工程架构、Agent 框架（6 工具·5 轮循环）、RAG 知识库、4 处人工确认点、3 角色接口级 RBAC，附代码行号与实测证据
- `scripts/collect_qa_archive.py`、`scripts/build_qa_archive.py`：归档采集与再生成脚本

### Fixed
- 测试执行器 N1 登录改用管理员会话本身（此前 token 取自匿名会话，线上网关覆盖 Authorization 后全部 401）
- 修复版覆盖发布至分享链接（链接不变），线上 26/26 用例回归通过，D-01 缺陷线上验证不再 500

## [1.6.1] - 2026-11-06

### Fixed
- D-01：`t_get_schedule` 对非法时间范围（parse_dt 返回 None）缺保护导致 500，改为退化为不限范围并给出缺项提示；补回归单测

### Added
- `tests/test_agent_core.py` 18 条单元测试；`scripts/run_scenario_tests.py` 26 条接口级场景用例执行器
- `Test/` 三件套：00 场景测试方案（md+docx）、01–05 可视化过程（34 图）、06 上线地址（md+docx）

## [1.6.0] - 2026-11-06

### Changed（按老师清单重排 docs 目录）
- `docs/` 改为**编号文件夹**，目录名即老师要求的序号，打开即可 1→9 逐项验收：
  - `1-六人访谈记录/`：六人访谈记录 .md / .json / .csv（三格式同源）
  - `2-项目基本简报/`：1-客户简报 / 2-现状信息请求表 / 3-业务发现访谈问题库 / 4-利益相关者资料与分工（4 个 HTML）+ 同名 zip（对应“下载并解压缩”）
  - `3-典型事件采访/`：采访记录-顾明岚（原始对话）.txt + 采访记录整理-顾明岚（AI整理版）.docx
  - `4-行业分析/`：行业分析文档 .md / .docx
  - `5-两份流程图/`：流程图-改造前.png / 流程图-改造后.png（另附两个可交互 HTML，从 examples/ 移入）
  - `6-需求定义与需求分析方案/`、`7-详细项目设计方案/`、`8-报价与投入产出测算/`、`9-上线交付运行手册/`：各 1 份 docx
- 原 `docs/01–15` 技术底稿移入 `docs/_附录-项目原始文档（01-15）/`；16 场景测试方案与 17 上线地址移入 `docs/_附录-测试方案与部署地址/`，docs 根目录只留九项 + 两个附录
- 重写 `docs/00-验收索引.md`：新增目录树、按序号的总览表与逐项看点
- 同步修正引用路径：`README.md` 文档导航、`scripts/init_db.py`（简报目录 → `docs/2-项目基本简报/*.html`）、`data/README.md`、`src/admin/README.md`、16/17 附录文档

### Verified
- `scripts/init_db.py` 重跑通过：6 篇文档 / 25 切片 / 204KB，知识库检索正常
- 9 个编号目录内 docx 全部用 python-docx 解析通过（段落 + 表格数正常）

## [1.5.1] - 2026-11-06

### Fixed（线上实测问题）
- 托管网关会注入自己的 `Authorization` 头，导致登录令牌被顶掉、接口恒返回 401：鉴权改为「Bearer 与 HttpOnly Cookie 双通道依次尝试」，任一有效即放行
- JWT 密钥不再用 `secrets.token_hex()` 随机生成（多 worker/重启后令牌立即失效），改为固定演示密钥，并支持 `JWT_SECRET` 环境变量覆盖
- 登录接口由服务端下发 HttpOnly Cookie，不再依赖前端 JS 写 Cookie
- 线上复测：`staff/staff123` 登录 200 → 提问「版本有效性怎么判断」200，返回 3 条知识库来源（含《场地使用规则与交接规范》切片#2）

### Added
- **data 数据库入库**：`data/venue_scheduling.db`（SQLite，10 张表，users 3 / kb_documents 6 / kb_chunks 25 / prompt_templates 1 / schedule_slots 3），随仓库提交，评审无需运行代码即可核对数据模型
- `scripts/init_db.py`：一键重建交付库（建表 + 种子账号/提示词/档期 + 知识库切片向量化），制度文件按「条」切片，引用可落到具体条款
- 知识库灌入仓库既有业务规范 `src/data/kb/场馆档期业务规范.md`、`场地使用规则与交接规范.md` 与四份项目简报
- `data/README.md`：表结构、演示账号、重建方式、已验证结论
- `docs/00-验收索引.md`：老师九项 docs 要求 1→9 逐项对照（文件路径 / 规模 / 看点 / 自查结果）
- `database.py` 支持 `DB_PATH` 环境变量，可切换运行时库与交付库

### Fixed
- 知识库此前为空导致问答走兜底：已灌入 6 篇文档共 25 个切片，实测「装卸口冲突怎么判定」命中《场馆档期业务规范》§2.2 并附来源
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
