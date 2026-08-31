# LightRAG 独立部署与可用性门控集成计划

## 总体设计

- LightRAG 作为独立的全局补充检索源，不区分或映射平台知识库。
- 在统一检索编排处建立深模块 `SupplementalRetriever`；业务代码只依赖补充检索接口，LightRAG HTTP 实现与测试用内存 Adapter 位于该 seam 后。
- 用户不需要开启、选择或感知 LightRAG。现有结果排在前面，随后展示“相关资料”卡片。
- LightRAG 使用独立 Compose、独立 PostgreSQL、独立配置和生命周期；平台 Compose 不包含 LightRAG 服务，也不对其声明 `depends_on`。

## 独立 Compose 与网络

- 新增独立的 `lightrag/compose.yaml`，仅包含：
  - 固定为 `lightrag-hku==1.5.6`、Python 3.12 的 LightRAG。
  - 独立 `pgvector/pgvector:pg18` PostgreSQL 和持久卷。
  - LightRAG 工作目录、输入文件与日志卷。
- 独立启动方式为 `docker compose -f lightrag/compose.yaml up -d`，停止或升级该 Compose 不重启平台。
- 使用一个预先创建、长期保留的 Docker internal 网络 `nairag-supplemental`：
  - 平台仅 `api` 容器加入。
  - LightRAG 以固定别名 `lightrag` 加入。
  - PostgreSQL 不加入该网络，只连接 LightRAG 私有数据网络。
  - LightRAG 另接仅供访问 LLM、Embedding 服务的出站网络。
- LightRAG 不发布任何宿主机端口，只在共享 internal 网络 `expose: 9621`。
- 按要求不设置 `LIGHTRAG_API_KEY`、账户认证或平台侧鉴权头；平台也不增加 LightRAG 密钥配置。
- 由于未鉴权模式允许所有可达客户端访问管理端点，共享网络不得对外暴露，且预期只允许平台 API 与 LightRAG 加入。[LightRAG 未鉴权模式说明](https://github.com/HKUDS/LightRAG/security/advisories/GHSA-mmg5-8x8q-v934)
- 平台自身启动、`/health` 和 Compose 健康状态完全不检查 LightRAG；共享网络作为持久基础设施存在，LightRAG 容器是否运行不影响平台 Compose。

## 可用性门控

- 配置项：
  - `SUPPLEMENTAL_RETRIEVAL_ENABLED=false`
  - `LIGHTRAG_BASE_URL=http://lightrag:9621`
  - 健康探测间隔 5 秒、超时 1 秒、状态有效期 10 秒。
  - 检索超时 15 秒。
- 未启用时使用 Disabled Adapter，不启动健康探测，也不进入任何补充检索代码。
- 启用后：
  - 平台启动不等待 LightRAG，初始状态为 `unavailable`。
  - 后台监视器只调用官方 `/health`，绝不调用 `/query/data`；连续两次健康后切换为 `available`。
  - 任意健康检查失败、状态超过 10 秒未刷新或检索连接失败，立即切换为 `unavailable`。
- 搜索编排在创建并发任务前读取内存可用性快照：
  - `available`：创建 LightRAG 检索任务。
  - `disabled`、`unknown`、`unavailable` 或 `stale`：不创建任务、不等待超时、不调用 `/query/data`，直接执行现有检索。
- LightRAG 在最近一次健康检查后瞬间宕机的竞态无法从网络层预知；该次请求若失败则静默丢弃补充结果并立即关闭门控，后续请求不再触发该链路。
- 服务恢复后由后台健康探测自动重新打开门控，无需重启平台。
- 搜索事件只记录 `skipped_disabled`、`skipped_unavailable`、`success` 或 `failed_after_dispatch` 等内部诊断状态，不向用户显示降级提示。

## 检索与展示

- 文本、图片 OCR、混合查询和快速搜索均可触发补充检索；字段筛选不触发。
- 即使用户限定了某个知识库，LightRAG 全局语料仍参与。
- 使用 `/query/data`、`mode=mix`、`enable_rerank=false`，只取得结构化检索数据，不生成答案。[LightRAG 查询接口](https://github.com/HKUDS/LightRAG/blob/v1.5.6/lightrag/api/routers/query_routes.py)
- 文本与 OCR 分通道调用，沿用 0.65/0.35 权重；按来源文档归并片段，并通过规范化路径哈希去重。
- 标题仅显示安全文件名，片段最长 4,000 字符，不暴露内部路径。
- 使用现有重排模块排列补充文档；重排不可用时按通道权重和上游排名做确定性融合。
- 平台不对 LightRAG 返回的唯一文档做二次条数截断；上游检索预算默认 `top_k=60`、`chunk_top_k=20`。
- 响应保留现有 `groups`，新增 `supplemental_results`：
  - `result_item_id`
  - `rank`
  - `score`
  - 可空 `rerank_score`
  - `title`
  - `content`
  - `selection_stage`
  - 快速搜索额外包含 `matched_queries`
- 展示顺序固定为现有知识库卡片在前、“相关资料”补充卡片在后；任意一类有结果即令 `no_match=false`。

## 结果持久化与标注

- 将 `search_result_item` 泛化为用户实际看到的卡片快照：
  - 新增 `result_kind = knowledge | supplement`。
  - 平台外键改为可空，并通过数据库约束保证 `knowledge` 卡片拥有完整平台标识。
  - `supplement` 卡片保存来源哈希、标题、片段和清理后的引用元数据，不允许带知识库归属。
- 在当前结果级标注迁移之后追加新迁移，不覆盖正在开发的标注代码。
- 两类卡片继续通过相同的 `search_result_item` 外键进入结果级标注流程，不创建 LightRAG 专用标注表。
- 补充卡片不显示“有用”按钮，后端拒绝为其记录 helpful feedback；人工标注仍可正常标记。
- 标注详情使用持久化快照还原用户当时看到的内容，即使 LightRAG 文档之后被删除。
- 知识库维度统计只归类平台卡片；补充卡片永远不伪造知识库。

## 全局资料管理

- 系统管理员新增“全局资料管理”页面，通过平台后端代理访问 LightRAG：
  - 文件上传，单文件上限 20 MB。
  - 动态读取 LightRAG 支持的扩展名。
  - 分页、状态筛选、异步处理进度和清理后的错误信息。
  - 单文档删除与二次确认。
- 不提供清空全部、缓存清理、扫描目录、重新处理或粘贴文本。
- 管理接口同样先检查可用性门控；不可用时直接返回平台统一的 503，不尝试上游管理请求。
- 浏览器不能直接访问 LightRAG；系统管理员权限、CSRF 和操作审计仍由平台负责。

## 验证方案

- 模块测试：Disabled、Unavailable、Available 三种 Adapter 状态；不可用时断言没有创建 `/query/data` 请求。
- 可用性测试：初始跳过、连续健康后启用、健康失败立即关闭、状态过期关闭、服务恢复自动开启。
- 搜索测试：并行执行、知识库筛选下仍参与、字段筛选跳过、平台结果在前、快速搜索去重、仅补充结果时 `no_match=false`。
- Compose 测试：
  - 只启动平台，搜索和健康检查正常。
  - 后启动独立 LightRAG，无需重启平台即可启用补充结果。
  - 停止 LightRAG 后平台继续运行，门控关闭且后续搜索不创建补充任务。
  - LightRAG 无宿主机端口、未配置 API Key，并且 PostgreSQL 数据在独立 Compose 重启后保留。
- 端到端闭环：上传 → 处理成功 → 检索出补充卡片 → 人工标注 → 删除 → 后续检索不再出现。

## 假设

- “LightRAG 不可用时不触发检索链路”指已知不可用、初始未知或健康状态过期时不创建检索任务；后台 `/health` 探测不属于检索链路。
- 无 API Key 的信任边界是宿主机及专用 Docker internal 网络，因此不会对宿主机或外部网络发布 LightRAG 端口。
- 当前结果级标注改动予以保留，LightRAG 集成只在其基础上追加兼容修改。
- LightRAG 维护独立外部语料，不同步 Nairag 已发布内容。
- 卡片只展示文档标题和相关片段，不提供原文件下载或生成式答案。
- “无数量限制”定义为 Nairag 不对上游返回的唯一文档做额外展示截断；LightRAG 自身仍保留有限检索预算。