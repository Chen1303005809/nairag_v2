# Nairag V2

本仓库实现单组织、私有化部署的 RAG 知识库系统。领域约束与分阶段范围以 [已确认实施基线](docs/已确认实施基线.md) 为准。

## 当前实现

当前已完成以下可审查模块：

1. 账号与认证：首次系统管理员初始化、Cookie JWT、CSRF、强制改密、账号管理与审计。
2. 知识库与审查授权：知识库启停、逻辑标识和物理 Collection 代映射、审查管理员分配。
3. 父类、子条目与审核提交：不可变修订、父类—主子条目原子投稿、普通子条目目标库门禁和投稿界面。
4. 审查工作台与发布状态：按知识库授权过滤审核队列、不可变审核决定、父类聚合全局发布、普通子条目分库发布和归档。
5. 异步索引与检索基础：持久化索引任务、独立 worker、租约/重试、可替换的 Qwen/Milvus 适配层、离线 artifact 混合召回、发布事实回查、父类关键词保底和有用反馈。

后续实施请从 [实施交接](docs/实施交接.md) 继续，并以 [已确认实施基线](docs/已确认实施基线.md) 为准。

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

   另开一个终端启动文本索引 worker：

   ```bash
   cd backend
   python worker.py
   ```

API 文档位于 `http://127.0.0.1:8000/docs`。登录前需先请求 `GET /api/v1/auth/csrf` 获取 `nairag_pre_auth_csrf` Cookie；所有已认证的变更请求则需要 `nairag_csrf` Cookie 及匹配的 `X-CSRF-Token` 请求头。前端已自动处理这两个步骤。

4. 启动前端（另开一个终端）：

   ```bash
   cd web
   npm install
   npm run dev
   ```

Vite 会把 `/api` 代理到本地 API，浏览器通过同源 Cookie 完成登录。Compose 容器运行时则由 `web` 服务中的 Nginx 托管构建产物，并将 `/api/` 反向代理到 `api` 服务。

## 容器开发

`docker compose up --build` 会启动 PostgreSQL、API、前端和独立索引 worker。前端由 Nginx 托管 Vite 构建产物，并将 `/api/` 同源反向代理到 API；启动后访问 `http://127.0.0.1:8080/`，API 文档仍位于 `http://127.0.0.1:8000/docs`。容器数据统一保存在项目下的 `volumes/` 目录（该目录已加入 `.gitignore`）：PostgreSQL 使用 `volumes/postgres_data`，API 与 worker 共享只读/读写隔离的 `volumes/index_artifacts`。worker 从 PostgreSQL 的持久化 `index_job` 队列领取任务，写入开发用的确定性索引产物，API 读取同一份产物执行混合检索，并在索引成功后推进发布；启动前请按 [secrets/README.md](secrets/README.md) 创建本地 Secret 文件，这些文件不会纳入版本控制。
