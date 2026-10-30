# 场馆档期排期智能体 · 管理后台

FastAPI + Jinja2 服务端渲染，对标企业 SaaS 风格。

## 启动

```bash
pip install -r ../../requirements.txt
cd src/admin
python -m uvicorn main:app --port 8123
# 浏览器打开 http://127.0.0.1:8123/login
```

默认账号：`admin/admin123`（管理员）、`staff/staff123`（一线员工）
LLM 供应商：环境变量 `LLM_PROVIDER`（默认 mock 离线可跑，详见 docs/07、docs/08）

## 业务硬约束（代码级强制）

- 智能体仅输出冲突提示 / 缺项清单 / 来源位置
- 不自动锁档、不签约、不对外承诺容量（草稿仅登记）
- 所有业务结论必须人工复核确认（渲染层强制追加，不受模板编辑影响）
- 接口级 RBAC：员工/管理员双角色，全部 22 个接口经依赖注入校验
- 对话审计完整留痕：提问原文、召回片段（含文档版本）、大模型原始回答全文、访问人、提示词模板版本
