# 定稿 neo4j 工具契约

Type: grilling
Status: resolved
Blocked by:

## Question

定稿 neo4j 工具契约（既定取向：粒度工具 + agent 编排）：
`load_model` / `upsert_node` / `upsert_edge` / `match` / `delete` / `sync_schema`，每个工具的**参数、返回、错误语义**；是否要拆 batching。

Blocked by 票单「调研 neo4j 驱动与 Cypher 写入最佳实践」（驱动能力决定签名上限）。01 已解，阻塞解除。

## Answer

工具契约（六条，全部经 grilling 拍板）：

1. **两层写入**：主写 `commit_batch`（整篇文档单事务原子）；单条 `upsert_node/upsert_edge` 各单事务供精确操作。不只用纯单条工具。
2. **commit_batch** `(nodes, edges) → {nodes_merged, edges_merged}`：内部 `UNWIND $rows` + 一个 `execute_write` 短事务（01 官方批量模式）；先 MERGE 节点后建边；边引用批内不存在的 key（悬空）→ 整批回滚。
   `nodes:[{entity_type, key, properties}]`、`edges:[{edge_type, source_key, target_key, properties}]`。
3. **sync_schema**：只建每个实体类别在 key_field 上的唯一性约束；边不单独建约束（幂等靠「源+目标+边类型」MERGE，串行场景不担心 01 的并发死锁）。
4. **match** `(label?, properties_filter?) → {records}`：只读，进首版，供 agent 校验/查证。
5. **delete**：标记「首版可选」——契约占位，首版可不实现。
6. **错误语义**：统一返回结构化错误 `{error:{code,message,detail}}`、不抛异常；code 如 `bad_args`/`dangling_ref`/`constraint_violation`/`unknown_type`；agent 读到记日志走映射规则的跳过。

签名清单：`load_model()`、`sync_schema()`、`commit_batch(nodes, edges)`、`upsert_node(entity_type, key, properties)`、`upsert_edge(edge_type, source_key, target_key, properties)`、`match(label?, filter?)`、`delete(what, key?)`（可选）。
