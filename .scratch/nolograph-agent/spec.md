Status: ready-for-agent
Type: spec
Parent: ../map.md

# 规格：理解知识图谱模型、把 JSON 按模型摄入 neo4j 的 agent

> 由 wayfinder 地图 `.scratch/nolograph-agent/map.md` 的五张决定票（01–05）合成；本文件是可交接的 feature 规格，供实现接手。领域词汇沿用地图既定基线（模型/实体/边/key_field/MERGE/commit_batch/_validator_/摄入报告）。

## Problem Statement

我们有一个**知识图谱模型**，它定义有哪几种实体、各实体有哪些属性、有哪些种类的边及边的属性。这个模型当前只存在于设计者脑中，需要一个能落库的机器形式。同时会有一批 **JSON 数据源**（如网络拓扑导出），需要被**按模型**存入 neo4j。

但目前：模型没有机器可读的形态、JSON→图的映射要靠人手工判断、写入 neo4j 要手写 Cypher、且要防重复（同一实体多次出现不能建出重复节点）。

## Solution

做一个**纯 Python 的 agent**（不引框架）：模型存为 pgsql 四张元数据表；agent 通过只读 `load_model()` 拿到模型，在**五步 tool-calling loop** 里由 LLM 读模型 + 读 JSON、自主推断该 JSON 的对象应变成哪些节点/边/属性，然后 `validator` 确定性应用异常跳过规则，最后 `commit_batch` 把有效集**单事务原子** MERGE 进 neo4j（幂等靠 key 上的唯一约束）。模型可扩充 = 在元数据表**加行**，不改代码、不靠脏数据。

## User Stories

1. As 一位设计者, I want 把「模型有哪些实体/边、各有什么属性」写进 pgsql 元数据表, so that 模型有了机器可读的单一事实源。
2. As 一位设计者, I want 用「增一行元数据」的方式扩充模型（加实体/加属性/加边类型）, so that 新增不影响旧数据也不改代码。
3. As 一位摄入人员, I want 把一份 JSON 数据源交给 agent, so that 它自动按模型把数据存进 neo4j，不用我手工映射。
4. As 一位摄入人员, I want agent 自主推断 JSON 里哪个对象是节点、哪个是边、哪个是属性, so that 映射不用人写死。
5. As 一位摄入人员, I want agent 通过 `kind` 字段（或无则按数组键名）把对象归到正确实体类型, so that 类型判定有明确的默认规则。
6. As 一位摄入人员, I want 嵌套结构（如设备的接口列表）按模型决定——是提升为节点+边还是作为属性, so that 嵌套不靠猜、有确定性规则。
7. As 一位摄入人员, I want JSON 里的 id 引用（如 `links.a/z`）自动变成边（CONNECTED_TO）, so that 关系从引用中自动还原。
8. As 一位摄入人员, I want 字段名与模型属性同名即对齐、`id` 对齐 key_field, so that 字段映射简单可预期。
9. As 一位摄入人员, I want 不匹配实体类型的信封对象（如 `report`）不建节点, so that 元数据包裹不污染图。
10. As 一位摄入人员, I want 无法归属类型 / 缺 key_field / 类型不符 / 悬空引用的数据被告警+跳过+记日志, so that 脏数据不写进图、问题可追溯。
11. As 一位摄入人员, I want 同一实体多次出现（同批/跨批）不建重复节点, so that 图保持幂等（MERGE + key 唯一约束）。
12. As 一位摄入人员, I want 一条边的重复导入不产生重复边, so that 边幂等（源+目标+边类型）。
13. As 一位摄入人员, I want 一篇文档的所有节点/边在**一个事务**里原子写入，失败整体回滚, so that 不出现写一半的脏图。
14. As 一位摄入人员, I want 跳过项不阻断有效集（有效集仍原子写入）, so that 个别坏数据不影响整体摄入。
15. As 一位摄入人员, I want 摄入完成后看到报告（建多少节点/边、跳过多少、每类异常计数）, so that 可核对摄入结果。
16. As 一位接入方, I want 连接信息（neo4j/pgsql/LLM）全部走环境变量, so that 不硬编码、可切任意环境。
17. As 一位接入方, I want LLM 通过 OpenAI 兼容端点接（支持硅基流动 / packyCode / 自定义 URL）, so that 供应商可随意切换。
18. As 一位接入方, I want 在目标程序中嵌入这套摄入能力（后期）, so that 摄入可被其他系统调用。

## Implementation Decisions

来源：五张决定票 + 调研 findings。以下列出模块/接口/架构/契约/方案变更；不含具体文件路径与代码片段（易过时）。

- **模型表示（scheme 变更）** —— pgsql 四表（原型草案，来自票单 04）：
  - `node_type(type_id PK, name UNIQUE, key_field)`
  - `node_attribute(type_id FK→node_type, attr_name, attr_type, optional, cardinality)`，`PK(type_id, attr_name)`
  - `edge_type(edge_id PK, name UNIQUE, source_type FK→node_type, target_type FK→node_type)`
  - `edge_attribute(edge_id FK→edge_type, attr_name, attr_type, optional, cardinality)`，`PK(edge_id, attr_name)`
  - 属性类型词表（8）：`text/int/float/bool/ip/cidr/datetime/json`；`cardinality`=`scalar/list`，`optional`=可空。
- **模型读取** —— 只读聚合接口 `load_model()` 返回结构化模型；agent 不直接碰 SQL。
- **映射规则（A–F，票单 02）** —— A 实体类型按 `kind` 优先、退按数组键名；B 嵌套结构由模型决定（有对应类型则提升为节点+连边，否则作属性）；C `a`/`z` 型 id 引用→边，边类型优先 `type`、缺省按键约定；D 字段同名对齐、`id`→key_field；E 信封对象不入图；F 四类异常（无法归属/缺 key_field/类型不符/悬空引用）→告警+跳过+记日志。
- **工具契约（票单 03）** —— 签名：`load_model()`、`sync_schema()`（建 node key 唯一约束）、`commit_batch(nodes, edges)`（单事务原子，内部 `UNWIND`+短事务，先节点后边，悬空引用整批回滚）、`upsert_node(entity_type, key, properties)`、`upsert_edge(edge_type, source_key, target_key, properties)`、`match(label?, filter?)`（只读）、`delete(what, key?)`（首版可选）。错误一律结构化 `{error:{code,message,detail}}`，不抛异常。
- **身份与幂等** —— 实体 MRGE on key_field（靠 sync_schema 唯一约束）；边 MERGE on（源、目标、边类型）。
- **实现骨架（票单 05）** —— 5 模块：`model_loader`（load_model）、`neo4j_tools`（工具实现+结构化错误）、`validator`（应用 F 规则）、`agent_loop`（五步 tool-calling loop + MAX_LOOP 守卫 + 工具注册表）、`main`（入口+摄入报告）。
- **架构决策** —— LLM 自主映射（读模型+JSON→提议节点/边）+ **validator 确定性兜底**；跳过项不阻断，有效集仍单事务原子写。
- **LLM 接入** —— OpenAI 兼容 chat completions，`openai` SDK，`base_url/api_key/model` 环境变量，provider 无关。
- **配置（API 契约）** —— 全环境变量：`NEO4J_URI`/`NEO4J_AUTH`、`PGSQL_DSN`、`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`。
- **调研依据** —— 驱动全局单例、三种执行模式、MERGE 依赖 key 唯一约束、并发死锁风险、官方批量模式（每批 10k + `UNWIND` + 一个短事务）、五步 tool-loop。见 `research/neo4j-python-driver.md`。

## Testing Decisions

- **好的测试 = 只测外部行为**：一次摄入产生预期节点/边数量、跳过行为符合 F 规则、幂等（重复导入不增量）、原子性（坏数据让整批回滚）。不测内部实现细节。
- **待测模块**：`validator`（F 规则的确定性），`neo4j_tools`（commit_batch 原子性/幂等/结构化错误），以及**端到端**的摄入入口 `main.ingest`。
- **LLM 非确定性的处理**：确定性测试用**注入的桩 proposer**（一段预设的工具调用序列）替代真 LLM，从而只测 validator+commit_batch+报告这些确定性部分；真 LLM 循环只做一次手工冒烟，不进自动化测试。
- **neo4j 依赖的隔离**：对 `commit_batch`/工具层用**假驱动/事务桩**验证原子性与幂等，不必每次都起真 neo4j；端到端测试对暂时性/一次性 neo4j（或假驱动）跑。
- **优先参照**：映射/幂等行为以 `samples/topology-sample.json` 为运转样例断言预期图。
- **拟议测试缝（一处）**：端到端缝 `ingest(model, json_source)` + 注入桩 proposer 与假驱动；另外 validator 与工具层做单测。若这处缝不合你预期，指一下，我调。

## Out of Scope

- 完整生产级程序的实现、部署与运维（本规格只交付设计，实现由 `ready-for-agent` 接手）。
- 与目标嵌入程序的集成细节（后期才做）。
- 多数据源流式/并发摄入、批性能上限调优（先单文档串行）。
- neo4j 认证加固、网络安全层。
- 「模型由脏数据自动扩充」（已明确否定——扩充是有意的元数据加行）。

## Further Notes

- 资产：样例 `samples/topology-sample.json`；调研 `research/neo4j-python-driver.md`。
- **pgsql 现状事实**：已勘测——全新集群、无既有元数据表，草案即事实源；库/schema 命名实现阶段定（如 `model_meta`，连接 `postgres@127.0.0.1:5432`）。
- **待实现阶段核实**：neo4j 实例地址/凭据（尚未提供，对应 `NEO4J_URI`/`NEO4J_AUTH`）。
- 本规格由 wayfinder 地图（`.scratch/nolograph-agent/map.md`）驱动产生：五张决定票（01–05）均已解析，无剩余待定项；实现阶段如遇与既有决定冲突，回地图相关票单核实后再改，改完记一笔到 `map.md`。
