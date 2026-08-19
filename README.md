# Nairag V2

本仓库实现单组织、私有化部署的 RAG 知识库系统。领域约束与分阶段范围以 [已确认实施基线](docs/已确认实施基线.md) 为准。

## 当前实现

已完成第一个可审查模块：账号与认证后端。它提供首次系统管理员初始化、Cookie JWT 登录、强制改密、账号创建/禁用/改角色/重置临时密码，以及关键动作审计。

## 本地运行（账号与认证模块）

1. 创建 Python 3.12 虚拟环境并安装后端依赖：

   ```bash
   python3.12 -m venv .venv
   . .venv/bin/activate
   pip install -e ./backend[dev]
   ```

2. 配置 PostgreSQL 连接和两个 Docker Secret 文件路径。示例见 [backend/.env.example](backend/.env.example)。首次启动时，数据库没有账号才会读取初始管理员密码；管理员创建后，修改 Secret 不会覆盖现有账号。

3. 迁移并启动 API：

   ```bash
   cd backend
   alembic upgrade head
   uvicorn app.main:app --reload
   ```

API 文档位于 `http://127.0.0.1:8000/docs`。登录前需先请求 `GET /api/v1/auth/csrf` 获取 `nairag_pre_auth_csrf` Cookie；所有已认证的变更请求则需要 `nairag_csrf` Cookie 及匹配的 `X-CSRF-Token` 请求头。前端已自动处理这两个步骤。

4. 启动前端（另开一个终端）：

   ```bash
   cd web
   npm install
   npm run dev
   ```

Vite 会把 `/api` 代理到本地 API，浏览器通过同源 Cookie 完成登录。生产环境将由后续部署模块使用 Nginx 托管并反向代理。

## 容器开发

`docker compose up --build` 会启动 PostgreSQL 和 API。启动前请按 [secrets/README.md](secrets/README.md) 创建本地 Secret 文件；这些文件不会纳入版本控制。
