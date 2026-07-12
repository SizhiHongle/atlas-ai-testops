# Atlas TestOps Backend

Atlas AI 测试平台的 Python 3.14 后端基础包。

## 当前范围

- FastAPI Application Factory 与 `/v1` Router。
- 环境变量配置和 CORS。
- Liveness / Readiness 健康检查。
- 严格的 Workflow Graph Pydantic Schema。
- Node、Port、Edge、DAG、必填输入、终止节点与 HARD Oracle 覆盖校验。

PostgreSQL、Temporal、Playwright Browser Worker 和对象存储将在对应业务功能落地时接入。

## 开发

```bash
uv sync
cp .env.example .env
uv run uvicorn atlas_testops.main:app --reload
```

服务启动后可访问：

- API 文档：`http://127.0.0.1:8000/docs`
- OpenAPI：`http://127.0.0.1:8000/openapi.json`
- Liveness：`http://127.0.0.1:8000/v1/health/live`
- Readiness：`http://127.0.0.1:8000/v1/health/ready`

## 质量检查

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest
uv build
```

## 环境变量

所有服务变量使用 `ATLAS_` 前缀。数组值使用 JSON，例如：

```bash
ATLAS_CORS_ORIGINS='["http://localhost:5173","http://127.0.0.1:5173"]'
```
