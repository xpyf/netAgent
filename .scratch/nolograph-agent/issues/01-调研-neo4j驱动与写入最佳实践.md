# 调研 neo4j 驱动与 Cypher 写入最佳实践 + Python 工具调用

Type: research
Status: resolved
Blocked by:

## Question

调研并给出可落进规格的结论：

1. neo4j 官方 **Python 驱动**（`neo4j` driver）的连接与事务用法。
2. Cypher **MERGE** 与 **唯一性约束**（per node type、on key_field）的搭配与坑。
3. **批量写入**与事务边界的最佳实践（对应既定「单事务 MERGE」取向，看是否要分块）。
4. 纯 Python 里给 LLM 暴露工具的**手工 tool-calling loop** 参考实现方式。

产出 findings 文件作为本票的上下文指针（写入 `research/neo4j-python-driver.md`）。

## Answer

调研完成，findings 见 [research/neo4j-python-driver.md](../research/neo4j-python-driver.md)。要点：

- **驱动**：`GraphDatabase.driver(...)` 全局单例复用；三种执行模式 `execute_query()`（简单用例，自动包事务）/ `execute_read|write()`（手动事务，驱动自带指数退避重试）/ `session.run()`（隐式事务，须消费结果才提交）。
- **MERGE + 唯一约束**：MERGE 幂等合并依赖「唯一性约束建在匹配的 key 上」；并发 MERGE 有死锁风险，用短事务缓解。
- **批量写入**：官方模式 = 每批 10k + `UNWIND $rows` + 一个 `execute_write` 短事务；不要无界 MERGE 循环。
- **手工 tool-calling loop**：五步循环（发 tools → 收 tool_calls → 本地执行 → 回传 `role=role="tool"`+`tool_call_id` → 再发）；必须设最大循环次数，工具名→函数注册表。

待实现阶段在无阻断网络复核：`CREATE CONSTRAINT ... REQUIRE ... IS UNIQUE` 语法与目标 Neo4j 版本的驱动 API 差异。
