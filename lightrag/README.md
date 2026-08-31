# 独立 LightRAG 服务

该目录是全局补充资料的独立部署单元。它不共享 Nairag 的 PostgreSQL、Compose
项目、启动依赖或健康检查；平台 API 只是通过一个持久 Docker 内部网络调用它。
LightRAG 的 PostgreSQL、工作目录、输入文件和日志都保存在项目根目录的
`volumes/lightrag_postgres_data/`、`volumes/lightrag_work/`、
`volumes/lightrag_input/` 和 `volumes/lightrag_logs/`（均已被 Git 忽略），
而非 Docker 的系统管理卷中。

首次部署先创建网络（只需一次）：

```bash
docker network create --internal nairag-supplemental
```

然后准备 LightRAG 自己的配置并启动：

```bash
cp lightrag/.env.example lightrag/.env
# 编辑 lightrag/.env，填写数据库密码和 LightRAG 的模型服务配置
docker compose -f lightrag/compose.yaml up -d --build
```

服务不发布宿主机端口；`lightrag` 仅以 `lightrag:9621` 的别名存在于
`nairag-supplemental` 网络中。LightRAG 的 PostgreSQL 也只在其专用的内部
网络中可见。确认服务连续健康后，在项目根目录 `.env` 设置：

```dotenv
SUPPLEMENTAL_RETRIEVAL_ENABLED=true
LIGHTRAG_BASE_URL=http://lightrag:9621
```

再启动或重启 Nairag API。平台本身不会等待 LightRAG：它只有在后台探针连续两次
通过后才会检索补充资料；失联会自动停止派发，不影响平台 `/health`、主知识库
检索或平台启动。

该部署刻意不配置 LightRAG API Key 或账户认证，因此共享网络绝不能对外暴露，
并且只应允许平台 API 与 LightRAG 加入。参见 [LightRAG 的未鉴权模式安全公告](https://github.com/HKUDS/LightRAG/security/advisories/GHSA-mmg5-8x8q-v934)。

系统管理员通过 Nairag 的“全局资料”页面管理文件。浏览器不会直接访问
LightRAG，且平台只代理文件类型查询、分页列表、上传和单文件删除；没有清空、
缓存、扫描、重处理或文本内容管理接口。
