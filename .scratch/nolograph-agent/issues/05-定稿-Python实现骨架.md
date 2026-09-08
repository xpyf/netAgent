# 定稿 Python 实现骨架

Type: grilling
Status: resolved
Blocked by:

## Question

定稿规格中的 Python 实现骨架与组件选型：
neo4j 驱动、psycopg、LLM 客户端（供应商/模型）、手工 tool-calling loop 的**模块划分**、事务循环、模拟样例的生成方式。

Blocked by 票单「调研 neo4j 驱动与 Cypher 写入最佳实践」与「定稿 neo4j 工具契约」（签名与驱动能力决定模块边界）。01、03 已解，阻塞解除。

## Answer

Python 实现骨架（六条，全部经 grilling 拍板）：

1. **模块切分**（5 文件）：`model_loader.py`（psycopg 查 4 表→结构化模型）/ `neo4j_tools.py`（工具实现，结构化错误）/ `validator.py`（应用 02 异常规则：未知/缺失/类型/悬空→跳过+记日志）/ `agent_loop.py`（五步 tool-calling loop + MAX_LOOP 守卫 + 工具注册表）/ `main.py`（入口：JSON→load_model→跑 agent→摄入报告）。
2. **映射职责**：LLM 在工具循环里做映射（读模型+JSON→提议节点/边，符合 Q3 自主推断）；`validator` 在写前确定性过滤异常项；非只读字段机械切。
3. **LLM 接入**：OpenAI 兼容 chat completions、`openai` SDK，`base_url`/`api_key`/`model` 环境变量配，provider 无关。用户可用：硅基流动、packyCode、自定义 URL（均为 OpenAI 兼容端点，只需指 `LLM_BASE_URL`）。
4. **依赖清单**：`neo4j`（官方驱动）+ `psycopg2-binary`（pgsql）+ `openai`（LLM）+ 标准库；不引框架。装依赖用 `uv`（已验证可用）。
5. **摄入报告与原子性**：输出建/跳计数（按异常类计数）与结构化错误；跳过项不阻断——validator 过滤后有效集仍由 `commit_batch` 单事务原子写（03），符合 Q12。
6. **配置来源**：全部环境变量（`NEO4J_URI`/`NEO4J_AUTH`、`PGSQL_DSN`、`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`）；不留配置文件、不硬编码。
