# 知识图谱模型 → neo4j 摄取 agent：设计规格

> 本图是 wayfinder 地图（本地 markdown tracker）。决定票单是 `issues/` 下的子文件。状态：**✅ 目的地达成**——五张票单全部解析，规格正文见 `spec.md`；无可再定，进入实现阶段。
>
> charting 阶段基线（Q1–Q13）：目的地=可交接规格；模型在 pgsql 元数据表；agent 读模型自主映射；工具以函数或 MCP 暴露；纯 Python 简单实现、不引框架；后期嵌入目标程序。

## Destination

一份**可交接的设计规格**，规定一个「理解知识图谱模型、把 JSON 数据源按模型写入 neo4j」的 agent。规格必须锁定三件事：

1. **模型表示**：模型以 pgsql 元数据表存储（实体类型/属性、边类型/属性），可扩充；agent 通过只读 `load_model` 入口读取。
2. **映射规则**：agent 读模型定义、自主推断 JSON→节点/边/属性的归属，含身份与幂等、未知/缺失数据处理策略。
3. **工具契约**：agent 调用的 neo4j 工具清单与签名（load_model / upsert_node / upsert_edge / match / delete / sync_schema）。

本图到「规格完整、无可再定」即告完成；**不实现完整代码**（后续再实现）。

## Notes

- 领域：网络拓扑知识图谱（实体≈网络节点，边≈连接/归属关系）。
- 现状：`/home/jess/PJ/nolograph` 目录为空、非 git 仓库；tracker 用本地 markdown。
- 既定技术取向：模型在 pgsql 元数据表；agent 读模型自主映射；工具以函数或 MCP 暴露；纯 Python 简单实现、不引 agent 框架；后期嵌入目标程序。
- 处理票单时 consult **grilling** + **domain-modeling**；调研 consult **research**。

## Decisions so far

<!-- 既定基线（charting 阶段对话确认，非票单解析）： -->
- **目的地 = 可交接设计规格**：先出规格，后续再实现。
- **身份与幂等**：实体以 `key_field` 为唯一键、写入用 Cypher `MERGE` 而非 `CREATE`；边幂等 = 「源+目标+边类型」三元组。
- **工具契约形态**：粒度工具 + agent 编排（load_model / upsert_node / upsert_edge / match / delete / sync_schema）。
- **未知/缺失/类型不符**：告警并跳过该节点/边并记日志；**不**靠脏数据自动扩充模型。
- **事务边界**：按一次调用粒度一个事务；mapper 先产出节点/边清单（不落库），校验后 writer 单事务 MERGE，失败整体回滚。
- **模型读取**：包一层只读 `load_model()`，agent 不直接碰 SQL。
- **Python 骨架**：纯 Python + 手工 tool-calling loop，neo4j 驱动 + psycopg + LLM 客户端，不引框架。

<!-- 目的地 → spec.md，实现阶段由此接手：先建 pgsql 四表 → 配环境变量 → 跑 main.py 摄入样例。 -->

<!-- 票单解析（随解析追加） -->
- [调研 neo4j 驱动与 Cypher 写入最佳实践 + Python 工具调用](issues/01-调研-neo4j驱动与写入最佳实践.md): 已解析——驱动全局单例、三种执行模式；MERGE 依赖 key 上的唯一约束、并发有死锁风险；官方批量模式=每批 10k + `UNWIND` + `execute_write` 短事务；手工 tool-calling loop 五步流程。findings 见 `research/neo4j-python-driver.md`。
- [定义 JSON→图映射规则](issues/02-定义-JSON到图映射规则.md): 已解析——六条映射规则（实体类型判定 / 嵌套提升或作属性 / id 引用变边 / 字段同名对齐 / 信封不入图 / 四类异常告警跳）。样例资产 `samples/topology-sample.json`。
- [定稿 neo4j 工具契约](issues/03-定稿-neo4j工具契约.md): 已解析——两层写入（主写 `commit_batch` 单事务原子 + 单条 upsert）；`sync_schema` 只建节点 key 唯一性约束；`match` 只读进首版；`delete` 首版可选；统一结构化错误不回退。签名：load_model / sync_schema / commit_batch / upsert_node / upsert_edge / match / delete(可选)。
- [勘测并定稿模型表示](issues/04-勘测并定稿-模型表示.md): 已解析——四表（node_type/node_attribute/edge_type/edge_attribute）+ FK/UNIQUE 约束；8 种属性类型词表（含 ip/cidr/json）；cardinality=scalar/list、optional=可空；可扩充=纯增量元数据行。勘测完成：全新集群、无既有表，草案即事实源。
- [定稿 Python 实现骨架](issues/05-定稿-Python实现骨架.md): 已解析——5 模块（model_loader/neo4j_tools/validator/agent_loop/main）；LLM 自主映射+validator 确定性兜底；OpenAI 兼容 SDK、环境变量配 provider 无关（支持硅基流动/packyCode/自定义 URL）；依赖仅 neo4j+psycopg2-binary+openai；单事务原子+摄入报告；配置全环境变量。

## Not yet specified

- neo4j 实例地址/凭据位置；批处理规模与性能上限（随实现推进再定）。

## Out of scope

- 完整生产级程序的实现与部署（目的地是规格）。
- 与目标嵌入程序的集成细节（后期才做）。
