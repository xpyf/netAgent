# Research: Neo4j Python 驱动 + MERGE/唯一性约束 + 批量写入 + LLM 手工 tool-calling

> 用途：票单 01 的上下文指针。本文件汇总官方文档与第一手资料，供后续会话直接引用。
> 证据强度说明：本次运行中对外部主机（neo4j.com / graphaware.com / gist.github.com）的直接 fetch 在网络层被阻断，以下内容基于 web_search 对各主要来源的抓取快照（含官方 Neo4j driver manual 原文摘录）。关键结论均已标注证据类型与置信度。官方文档页码可在后续无阻断网络环境中复核。

---

## 1) neo4j 官方 Python 驱动（`neo4j` 包）：连接与事务用法

### 连接与驱动生命周期

- 用 `GraphDatabase.driver("neo4j+s://host", auth=(user, password))` 创建**一个** `Driver` 实例，进程内复用；批量迁移最常见的错误之一就是"每个请求新建一个 driver"（应为全局单例）。(来源: graph-data-modeling.org 集成模式; 置信度: 中)

### 三种执行模式（官方文档明确区分）

1. **`execute_query()`** —— 便捷封装，自动包事务，适合"简单用例、无需手动管理 session/transaction"。带 `routing_=neo4j.RoutingControl.WRITE`、`result_transformer_` 等参数。(API 文档: neo4j.com/docs/api/python-driver/current/api.html; 置信度: 高)
2. **`execute_read(fn)` / `execute_write(fn)`** —— 手动事务函数的入口。回调在事务内执行；**驱动在服务端故障时自动重跑回调（指数退避重试）**。(官方 manual; 置信度: 高)
3. **`session.run()`** —— 隐式事务。返回 `Result` 对象；**必须消费所有记录（迭代全部记录或调用 `.consume()`）才会提交**。(官方 manual query-advanced; 置信度: 高)

### 事务语义要点

- `execute_query()` / `execute_read` / `execute_write` 都会把查询包进事务，"保证数据库始终处于一致状态（无论期间发生什么，如断电、崩溃）"。
- 驱动对失败事务作指数退避重试，这是健壮性兜底。(官方 performance 页; 置信度: 高)
- 一个事务是"要么整体提交要么整体回滚"的工作单元；但**单个查询里不能插入客户端逻辑**——多条 Cypher 语句可放同一条查询里（如 `MATCH` 后接 `CREATE`），但"无法在多个查询之间穿插客户端逻辑"。更复杂场景才需要用显式 session/transaction。(官方 transactions 页; 置信度: 高)

### 关键坑

- **driver 必须跨请求复用**；**session 不能跨线程共享**；读操作别钉在陈旧集群成员上。(graph-data-modeling.org; 置信度: 中，属集成模式建议)
- 隐式事务不消费结果 = 不提交。批量写入尤需注意。

---

## 2) Cypher MERGE 与按 key_field 的唯一性约束

### MERGE 语义

- `MERGE` 是"有则匹配、无则创建"。**当定义了唯一性约束时，`MERGE` 期望模式最多匹配到一个节点**，从而允许你用业务 key 做幂等合并，并可用 `ON CREATE SET` / `ON MATCH SET` 分别处理新建与命中两种情况。(GraphAware《Cypher MERGE explained》; 置信度: 中，属教程解读)

### 与唯一性约束搭配的坑

- **必须把唯一性约束建在 MERGE 所匹配的 key_field 上**。若 MERGE 匹配的属性没有对应唯一约束，同一业务实体会被重复创建（竞态下尤甚）。官方 `neo4j-import-skill` 的 driver 批量写法就是 `MERGE (p:Person {id: row.id})` 配合 id 为唯一 key。(neo4j-skills / driver-batch-write.md 示例; 置信度: 高)
- **并发 MERGE 死锁风险**：`MERGE` 涉及写锁（文档指出执行前事务须获取写锁），两个事务同时改同一节点/关系会互相阻塞导致死锁；社区与 StackOverflow 对"并发 MERGE 导致死锁"有大量案例。(Cypher manual / 社区讨论; 置信度: 中)
- 缓解：把 MERGE 放进短事务（每批一个小事务），尽量让并发写不落在同一批节点，靠驱动重试或应用层重试兜底。

---

## 3) 批量写入与事务边界的最佳实践

### 官方推荐模式（neo4j-skills / driver-batch-write.md）

```python
from neo4j import GraphDatabase
driver = GraphDatabase.driver("neo4j+s://xxx", auth=("neo4j", "password"))
BATCH_SIZE = 10_000

def import_batch(tx, rows):
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (p:Person {id: row.id})
        ON CREATE SET p.name = row.name, p.age = row.age
        """,
        rows=rows,
    )

all_rows = [...]   # 源数据
for i in range(0, len(all_rows), BATCH_SIZE):
    batch = all_rows[i:i+BATCH_SIZE]
    with driver.session() as session:
        session.execute_write(import_batch, batch)
```

(来源: github.com/neo4j-contrib/neo4j-skills .../driver-batch-write.md; 置信度: 高——官方仓库参考实现)

### 关键实践要点

- **不要做无界 `MERGE` 循环**——会让 JVM 堆、事务日志、连接池失控；用 `UNWIND $rows AS row` 每一批一条 Cypher 语句完成多个节点的合并。(graph-data-modeling.org 批处理页; 置信度: 中，工程经验)
- **每个 batch 一个短事务**（`execute_write`），事务足迹有界、吞吐确定、内存隔离。Batch 内要么全部提交要么全部回滚。(同上; 置信度: 中)
- 注意事务 `tx.run` 结果同样须消费/提交；`execute_write` 里驱动会管理提交。(官方; 置信度: 高)
- BATCH_SIZE 取 10_000 量级（官方示例默认 10_000）。

---

## 4) 纯 Python 给 LLM 暴露工具的手工 tool-calling loop

### 官方/主流流程（OpenAI function calling 指南 + openai-cookbook + 社区参考实现）

标准五步循环：

1. 把用户消息 + `tools`（工具定义，含 `function`/`parameters` JSON schema）发给模型。
2. 若响应 `finish_reason == "tool_calls"`（或含 `message.tool_calls`），取出工具名与 `arguments`（JSON）。
3. 在本地执行对应 Python 工具函数。
4. 把结果作为 `role="tool"` 的 `tool_call_id` 消息追加回对话（引用模型给出的 `tool_call_id`）。
5. 把完整消息历史再次发给模型；重复直到模型给出最终答案（`finish_reason == "stop"`）。

(来源: developers.openai.com/docs/guides/function-calling; github openai-cookbook How_to_call_functions_with_chat_models.ipynb; dataquest "Python Function Calling"; 置信度: 高)

### 参考实现骨架（无 LangChain/框架）

```python
def run_agent(history, tools, tool_registry, max_loops=8):
    history.append({"role": "user", "content": ...})  # 用户问题
    for _ in range(max_loops):                          # MAX_LOOP 守卫，防死循环
        resp = client.chat.completions.create(
            model=..., messages=history, tools=tools)
        msg = resp.choices[0].message
        if msg.tool_calls:                              # 模型要调工具
            history.append(msg)                          # 附 assistant 的 tool_calls
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                result = tool_registry[tc.function.name](**args)
                history.append({"role": "tool",
                                "tool_call_id": tc.id,
                                "content": json.dumps(result)})
        else:
            return msg.content                            # 最终答案
    raise RuntimeError("max loop exceeded")
```

(来源: gist "Agent tool-calling loop from scratch"、medium 全例、openai-cookbook; 置信度: 中——社区参考实现，结构上与官方流程图一致)

### 实践要点

- **结果必须带 `tool_call_id`**，对应模型的调用；多工具调用时逐个追加。
- **必须设最大循环次数**（`MAX_LOOP` / `max_loops`）防止模型反复调用工具导致死循环。
- 工具名 → Python 函数的注册表（dict），参数用 JSON schema 校验。流式场景下 `finish_reason` 为 `null` 直到流结束才确定为 `tool_calls`。(OpenAI 社区; 置信度: 中)

---

## Contradictions

- 未见实质矛盾。仅需注意：官方文档明示"`execute_query` 适合简单用例"，而 `neo4j-import-skill` 批量参考实现用 `session` + `execute_write`；两者定位不同（简单查询 vs 事务控制），并非冲突。官方 API 文档版本为 6.3，若项目用 neo4j 5.x/4.x，个别参数略有差异。

## Missing evidence / 研究限制

- 本次对外部主机的直接 fetch 被网络层阻断，未能逐字核对官方 manual 全文；关键结论基于搜索抓取快照中的官方原文摘录，置信度已标注。
- MERGE 死锁缓解的"精确并发控制策略"（如显式锁、单写者批次调度）未从第一手文档逐字验证，建议在无阻断网络中复核 Cypher manual 的 subqueries-in-transactions 页。
- 唯一性约束的建表语法（`CREATE CONSTRAINT ... FOR (p:Person) REQUIRE p.id IS UNIQUE`）本次未逐字核验，属公认语法但未列具体来源。

## Sources

- Kept:
  - Neo4j Python Driver Manual — Run your own transactions (neo4j.com/docs/python-manual/current/transactions/) — 事务/execute_read/write 语义一手来源
  - Neo4j Python Driver Manual — Performance (…/performance/) — 自动重试、一致性保证
  - Neo4j Python Driver Manual — Query advanced (…/query-advanced/) — session.run 隐式事务、consume() 提交
  - Neo4j Python Driver API (neo4j.com/docs/api/python-driver/current/api.html) — execute_query 参数签名
  - neo4j-contrib/neo4j-skills driver-batch-write.md — 官方批量写入参考实现（BATCH_SIZE 10k + UNWIND + MERGE）
  - graph-data-modeling.org（批处理+集成模式） — 事务分块、driver/session 生命周期教训
  - GraphAware Cypher MERGE explained — MERGE 与唯一性约束语义
  - OpenAI Function calling guide + openai-cookbook How_to_call_functions_with_chat_models.ipynb — 工具调用循环官方流程
  - gist "AI agent tool-calling loop from scratch" — 无框架的手工 tool-calling loop 参考实现
- Rejected/deprioritized: 一般 SEO 类教程（dataquest、medium、tutorials.technology 的通用"2026 guide"）作为佐证但非第一手，未作为决定性证据。

## Next steps

- 建议在无阻断网络环境复核并逐字摘录：Cypher manual 的 `CREATE CONSTRAINT ... REQUIRE ... IS UNIQUE` 语法和 subqueries-in-transactions（死锁）部分，并确认项目目标 Neo4j 版本与驱动的 API 差异。

## 输出路径说明

研究文件已写入运行时指定路径（见 `writtenTo`），后续会话可直接引用该文件作为票单 01 的上下文指针，并据此实现驱动批量写入与 tool-calling loop。
