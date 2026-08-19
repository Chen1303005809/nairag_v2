---
status: accepted
---

# PostgreSQL 是发布事实来源，Milvus 是可重建索引

发布状态、归档状态和每个 `子条目 × 知识库` 的当前修订只由 PostgreSQL 决定；Milvus 仅保存从不可变修订派生的稠密/BM25 候选索引。这样即使索引写入、删除或模型换代是异步的，检索回查关系库后也不会泄露已归档、未批准或过期版本。

## Consequences

- 每次检索必须按 PostgreSQL 的发布关系和 `active_revision_id` 过滤 Milvus 候选。
- 归档事务提交后立即对外不可见，向量可以异步清理。
- 模型升级通过新物理 Collection 全量重建、验证和映射切换完成，不能在同一 Collection 混用模型版本。
- Worker 任务必须幂等，失败不能撤掉仍可服务的旧当前修订。

