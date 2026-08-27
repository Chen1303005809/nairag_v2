# Nairag V2

本仓库实现单组织、私有化部署的 RAG 知识库系统。领域约束与分阶段范围以 [已确认实施基线](docs/已确认实施基线.md) 为准。

## 当前实现

当前已完成以下可审查模块：

1. 账号与认证：首次系统管理员初始化、Cookie JWT、CSRF、强制改密、账号管理与审计。
2. 知识库与审查授权：知识库启停、逻辑标识和物理 Collection 代映射、审查管理员分配。
3. 父类、子条目与审核提交：不可变修订、父类—主子条目原子投稿、普通子条目目标库门禁和投稿界面。
4. 审查工作台与发布状态：按知识库授权过滤审核队列、不可变审核决定、父类聚合全局发布、普通子条目分库发布和归档。
5. 异步索引与检索基础：持久化索引任务、独立 worker、租约/重试、可替换的 Qwen/Milvus 适配层、PyMilvus 官方 hybrid search、离线 artifact 混合召回、发布事实回查、父类关键词保底和有用反馈。
6. 查询图片 OCR：前端上传 PNG/JPEG/WebP 后由本地 `PP-OCRv6_medium` 服务识别；API 只暂存请求内图片，审计与检索事件只记录清洗文本、关键词、置信度、模型版本和图片哈希。配置方式见 [.env.example](.env.example)。
7. 知识子条目佐证材料：可上传 PNG/JPEG/WebP、PDF、DOCX、XLSX、PPTX 或 UTF-8 TXT 附件，并添加相关网页链接；两类材料均绑定不可变子条目修订、经过审核流程并在检索结果展示。开发/测试使用本地私有存储，生产环境使用与 Milvus 共用服务上的独立 MinIO Bucket 和独立账号。
8. 快速上传与快速检索：可直接粘贴企业微信转发卡片，将客户与我方会话分别用于异步生成私有普通子条目草稿，或同步提取待查询问题并合并展示已有检索结果。卡片内图片会在提交前经本地 OCR 服务识别并替换 `[图片]` 占位符；若卡片复制未携带原图，可在轻量富文本框中为每个占位符单独粘贴或选择图片。能力使用可配置的 OpenAI 协议兼容 LLM，原始会话仅在快速上传任务处理期间短期保存，任务完成后立即删除。

后续实施请从 [实施交接](docs/实施交接.md) 继续，并以 [已确认实施基线](docs/已确认实施基线.md) 为准。

## 本地运行（账号与认证模块）

1. 创建 Python 3.12 虚拟环境并安装后端依赖：

   ```bash
   python3.12 -m venv .venv
   . .venv/bin/activate
   pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -e ./backend[dev]
   ```

2. 在项目根目录创建唯一配置文件。后端、Vite 本地开发服务器和 Docker Compose 都从此处读取配置；相对路径也以项目根目录为基准：

   ```bash
   cp .env.example .env
   ```

   在 `.env` 中配置 PostgreSQL 连接、LLM 等非 Docker Secret 设置，并按 [secrets/README.md](secrets/README.md) 准备 Secret 文件。首次启动时，数据库没有账号才会读取初始管理员密码；管理员创建后，修改 Secret 不会覆盖现有账号。

   从旧版升级时，请将 `backend/.env` 移至根目录并把其中的 `../secrets/` 路径改为 `./secrets/`。

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

Vite 会从根目录 `.env` 读取配置并把 `/api` 代理到本地 API，浏览器通过同源 Cookie 完成登录。Compose 容器运行时则由 `web` 服务中的 Nginx 托管构建产物，并将 `/api/` 反向代理到 `api` 服务。

所有环境变量统一维护在根目录未跟踪的 `.env`，模板为 [.env.example](.env.example)。快速上传与快速检索需要在其中配置 `OPENAI_BASE_URL`、`OPENAI_KEY` 和可选的 `OPENAI_MODEL`。`CSRF_COOKIE_NAME`、`PRE_AUTH_CSRF_COOKIE_NAME`、`LLM_MAX_CONVERSATION_MESSAGES` 和 `LLM_MAX_CONVERSATION_CHARS` 会自动派生为浏览器构建配置，避免前后端上限或 Cookie 名称漂移；Compose 只向前端构建传递这些公开值，不会把 `OPENAI_KEY` 打入浏览器镜像。

Compose 已内置本地 `PP-OCRv6_medium` 服务，API 默认通过内部地址 `http://ocr:9003` 调用它；OCR 容器不暴露宿主机端口。开发环境使用 CPU 容器，生产环境通过 `docker-compose.prod.yaml` 切换为 NVIDIA CUDA 容器，二者保持同一 HTTP 协议、模型版本与缓存位置。首次启动会下载固定的检测与识别模型到 Docker 命名卷 `ocr_model_cache`，后续启动复用该缓存。查询原图只在 API 和 OCR 服务的请求内存中处理，不会写入该卷或数据库。离线交付前应在受控网络中先启动一次 `ocr` 服务并备份/随交付物携带该模型缓存；模型权重不提交到仓库。

若 API 在 Compose 外单独运行，可在根目录 `.env` 中设置 `OCR_SERVICE_URL`，接入同一 HTTP 协议的本地服务。

## 容器开发

开发环境运行：

```bash
docker compose up --build
```

生产环境运行（Linux x86_64、NVIDIA 驱动与 NVIDIA Container Toolkit）：

```bash
docker compose -f docker-compose.yaml -f docker-compose.prod.yaml up --build -d
```

前者会启动 PostgreSQL、CPU OCR、API、前端和独立索引 worker，并使用轻量的本地 artifact 索引与本地私有附件目录；后者额外启动 Milvus standalone（etcd、MinIO、Milvus），将 API 与 worker 切换为 Milvus 后端，并在该 MinIO 服务内初始化独立、私有的附件 Bucket。生产 API 使用附件专属 MinIO 账号，不使用 Milvus 的存储凭据；请按 [secrets/README.md](secrets/README.md) 准备这两份额外 Secret。生产启动前在根目录 `.env` 设置 `EMBEDDING_SERVICE_URL`，使其指向提供 `Qwen/Qwen3-Embedding-0.6B` 的 OpenAI-compatible embedding HTTP 服务，并为 Milvus 管理账号设置非默认凭据。首次预热时间取决于模型下载。前端由 Nginx 托管 Vite 构建产物，并将 `/api/` 同源反向代理到 API；启动后访问 `http://127.0.0.1:8080/`，API 文档仍位于 `http://127.0.0.1:8000/docs`。容器数据统一保存在项目下的 `volumes/` 目录（该目录已加入 `.gitignore`）：PostgreSQL 使用 `volumes/postgres_data`，开发附件使用 `volumes/attachments`，生产 Milvus 使用 `volumes/milvus/` 下独立的 etcd、MinIO 和数据目录，API 与 worker 共享只读/读写隔离的 `volumes/index_artifacts`；OCR 模型使用独立 Docker 命名卷 `ocr_model_cache`。worker 从 PostgreSQL 的持久化 `index_job` 队列领取任务，通过 Milvus 或本地 artifact 写入索引，并在索引成功后推进发布。
