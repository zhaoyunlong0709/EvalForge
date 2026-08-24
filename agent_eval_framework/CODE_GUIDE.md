# EvalForge Phase 1 代码导读

> 目标：读完本文档后能快速定位和理解 2607 行框架代码的每个文件。
> 全文按**依赖顺序**组织（先读被依赖的），配合 `产品文档/Agent评测框架设计方案.md` 使用。
> 阅读时间预估：文档 15 分钟 + 代码 2-3 小时。

---

## 一、全局鸟瞰：一个 Case 的完整生命周期

理解下面这张图，就读懂了框架的一半。以 AgentCase C002（记忆评测）为例：

```
eval-forge run --tags smoke
   │
   ▼
cli/main.py::_run()                    总装配：读配置 -> 组装所有组件
   │
   ▼
loader/discoverer.py::load_all()       扫描 cases/ 目录，发现 JSON 文件
   │
   ▼
loader/registry.py::load()             读 case_type 字段，分发给对应 Loader
   │
   ▼
loader/agent_loader.py::load()         解析 profile 引用 -> 填充 setup
   │                                    （loader/filter.py 按 tags/priority 筛选）
   ▼
runner/orchestrator.py::run()          串行调度每个 case
   │
   ├── runner/agent_runner.py          多轮对话执行：
   │     │                               生成唯一 session_id（隔离）
   │     │                               逐轮 POST Agent API
   │     │                               trace 浅合并 -> result.actual_trace
   │     ▼                               最后一轮回复 -> result.actual_reply
   │
   └── runner/api_executor.py          （APICase 走这里：HTTP 请求 + 响应格式化）
   │
   ▼
evaluators/pipeline.py::evaluate()     路由（文档 5.3 规则）：
   │                                     method=llm_judge -> JudgeEvaluator（主）
   │                                     rules 非空     -> + RuleEvaluator（附加）
   │                                     trace 存在     -> + TraceEvaluator（附加）
   │
   ├── evaluators/judge/evaluator.py     TemplateEngine 组装 prompt
   │     │                               JudgeClient 调 LLM 打分（重试 3 次）
   │     ▼                               score >= threshold ? passed=True
   │
   ├── evaluators/rule_evaluator.py    11 个操作符断言（失败拉低 passed）
   │
   └── evaluators/trace_evaluator.py   trace 断言 + 盲区检测（不影响 passed）
   │
   ▼
result/aggregator.py::aggregate()     按优先级/维度/标签聚合
result/cost_tracker.py                统计 Judge/Agent token 成本
result/snapshot.py                    记录依赖快照（可复现性）
   │
   ▼
report/builder.py::generate_all()     Console 输出 + JSON 落盘 reports/
   │
   ▼
退出码：全部通过=0，有失败=1（供 CI 阻塞）
```

**核心设计思想（贯穿全代码）：**
1. **双模型不继承**：APICase 和 AgentCase 是独立 Pydantic 模型，通过 `case_type` discriminator 统一为 `EvaluableCase`，共用执行引擎和评测管线
2. **评测器不感知 case 类型**：RuleEvaluator 只读 `evaluation.rules` + `result`，对 API/Agent 通吃
3. **`result` 是数据总线**：执行器写入（actual_reply/actual_trace），评测器读取并回写（passed/score/trace_errors）

---

## 二、推荐阅读路线（7 层，由浅入深）

```
第 1 层 地基    models/（4 文件，260 行）        不读懂模型读不懂其他任何代码
第 2 层 工具    operators/ + utils/（3 文件）     独立无依赖，可单独理解
第 3 层 加载    loader/（6 文件，340 行）         JSON 文件 -> 内存对象
第 4 层 评测    evaluators/（8 文件，590 行）     ★核心★ 框架的灵魂
第 5 层 执行    runner/（3 文件，324 行）         对外发起 HTTP 调用
第 6 层 结果    result/ + report/（6 文件，340 行）
第 7 层 入口    cli/main.py（186 行）             总装配，看懂它就看懂全流程
```

---

## 三、逐文件导读

### 第 1 层：数据模型 `eval_core/models/`（先读，260 行）

#### `shared.py`（73 行）—— 两种 case 共用的类型

| 类/函数 | 行号 | 说明 |
|---------|:---:|------|
| `Priority` | 11 | P0-P3 枚举（str+Enum，序列化友好） |
| `CaseResult` | 19 | **★数据总线★** 测试结果。执行器写 actual_reply/actual_trace，评测器写 passed/score/judge_reason/trace_errors/trace_gaps。**永远不在 case JSON 文件中出现** |
| `RuleAssertion` | 43 | 一条规则断言：field + operator（11 个 Literal）+ value。docstring 写明字段提取规则（`$.` 前缀走 jsonpath） |
| `RuleBasedEvaluation` | 59 | APICase 的评测配置（method 固定为 automated） |
| `CaseLifecycle` | 67 | 生命周期（Phase 4 才启用，Phase 1-3 用目录结构替代） |

#### `api_case.py`（44 行）—— API 测试用例

| 类 | 行号 | 说明 |
|----|:---:|------|
| `APIRequest` | 12 | HTTP 请求定义。`follow_redirects` 是重定向跟随（注释明确"不是请求重试"） |
| `APICase` | 24 | ~18 行 JSON。只有 request + evaluation.rules 两个核心 section，没有 conversation/trace/golden |

#### `agent_case.py`（127 行）—— Agent 评测用例（模型核心）

**注意文件内类的定义顺序：子模型在前、主模型在后**（Pydantic 要求被引用类型先定义，文件顶部有注释说明）。

| 类 | 行号 | 说明 |
|----|:---:|------|
| `ConversationTurn` | 15 | 一轮对话：turn + user + expect（自然语言期望，替代原 {intent,behavior} 嵌套） |
| `AgentEvaluation` | 23 | method（automated/llm_judge/manual）+ template + params + threshold + rules |
| `TraceAssertion` | 32 | expected 支持 bool / ">=N" / "contains:X" / "not_null" |
| `TraceExpectation` | 39 | assertions + retrieved_memories（Phase 2 用）+ missing_fields（盲区） |
| `GoldenAnnotation` | 56 | golden 标注。**正面 case 无 failure 字段**（可选子对象） |
| `AgentSetup` | 68 | 前置条件。**由 ProfileResolver 填充，case 文件中不写** |
| `MockConfig` | 81 | Phase 3 用，模型已预留 |
| `AgentCase` | 92 | 主模型。`profile: str` 是引用名，`setup: AgentSetup | None` 是解析后数据 |

#### `__init__.py`（52 行）—— Union 类型

核心只有 5 行：

```python
EvaluableCase = Annotated[
    APICase | AgentCase,
    Field(discriminator="case_type"),
]
```

加载 JSON 时 Pydantic 自动按 `case_type` 字段路由到正确模型。**对应测试：** `tests/test_models.py`（20 用例：必填缺失、extra 拒绝、Union 路由）

---

### 第 2 层：工具 `eval_core/operators/` + `utils/`

#### `operators/operators.py`（197 行）—— 11 个比较操作符 + 字段提取

| 函数 | 行号 | 说明 |
|------|:---:|------|
| `parse_dot_path()` | 26 | "tool_calls[0].name" -> ["tool_calls", 0, "name"] |
| `extract_by_dot_path()` | 58 | 按点号路径取值，找不到返回 None |
| `extract_by_jsonpath()` | 75 | jsonpath-ng 提取（`$.` 前缀时使用） |
| `extract_field()` | 84 | **入口**：`$.` 前缀走 jsonpath，否则走点号路径 |
| `_op_eq()` | 109 | 数字宽松比较（200==200.0），其余严格 |
| `OPERATORS` | 174 | **操作符注册表**。新增操作符在此注册 + 同步更新 RuleAssertion 的 Literal（两处，文档 7.2） |
| `evaluate_assertion()` | 189 | 入口：未知操作符/比较异常都返回 False（不抛异常） |

**设计点：** operator 是 Literal 闭集（类型安全优先，文档 7.2 说明取舍）；jsonpath 是字段提取方式而非操作符。

#### `utils/logger.py`（25 行）/ `retry.py`（47 行）

- `setup_logger()`：loguru 配置，CLI 启动调一次
- `api_retry_strategy()` / `fixed_retry_strategy()`：tenacity 封装（注意：实际代码中 APIExecutor/AgentRunner/JudgeClient 用的是各自显式重试循环而非这两个装饰器，保留作通用工具）

**对应测试：** `tests/test_operators.py`（25 用例）

---

### 第 3 层：用例加载 `eval_core/loader/`（340 行）

#### `registry.py`（54 行）—— 分发中枢

`load()` 读 JSON 的 `case_type` 字段 -> 查注册表 -> 分发给对应 Loader。`register()` 是扩展点（新 Case 类型注册即用，不改现有代码，文档 7.3）。

#### `api_loader.py`（24 行）—— 最简单的 Loader

读 JSON -> `APICase.model_validate()`，一行核心逻辑。**先读这个理解 Loader 模式**，再看复杂的 agent_loader。

#### `agent_loader.py`（84 行）—— 含 ProfileResolver

| 类 | 行号 | 说明 |
|----|:---:|------|
| `ProfileResolver` | 17 | 递归搜索 profiles/ 找 `<name>.json`；同名冲突报错；**只映射 AgentSetup 已知字段**（profile_id/description 被忽略） |
| `AgentCaseLoader` | 63 | 读 JSON -> resolve profile（填充 setup）-> Pydantic 校验 |

#### `discoverer.py`（95 行）—— 发现与批量加载

`discover()`：无参数全量扫描 / `case_files` 指定文件 / `case_dirs` 指定目录（去重）。
`load_all()`：**坏文件跳过不中断**（对应 LLM-as-Judge 文档"校验失败记入 skipped"），返回 `(cases, errors)`。

#### `filter.py`（49 行）—— 筛选

同一类型内部 OR（tags=["smoke","regression"] 任一匹配），跨类型 AND（tags 和 priorities 同时满足）。语义写在模块 docstring。

**对应测试：** `tests/test_loader.py`（17 用例）

---

### 第 4 层：评测引擎 `eval_core/evaluators/`（★核心★，590 行）

#### `rule_evaluator.py`（81 行）—— 两种 case 通吃的规则断言

**读代码时抓两个点：**

1. **`_build_context()`（行 60）—— 数据源合并视图**：actual_reply 能解析为 JSON dict 就用（APICase 的 status_code/body 来自这里），再叠加 actual_trace（AgentCase 的 tool_calls 来自这里）。这是 API 测试和 Agent 评测共用一个评测器的关键。
2. **`evaluate()`（行 22）—— 双角色**：method=automated 时是主评测器（全过 -> passed=True）；method=llm_judge 且 rules 非空时是附加评测器（失败拉低 passed，全过不改变 Judge 的判定）。失败信息**追加**到 error_analysis 而非覆盖。

#### `judge/` 子目录 —— LLM-judge 四件套

**建议阅读顺序：template_engine -> response_parser -> judge_client -> evaluator**

##### `template_engine.py`（81 行）

`build_prompt()`：查模板 -> 组变量（conversation_history 只含用户消息/golden_reference 有 golden 用 golden.expected_answer 否则"无参考答案"/params 合并）-> 填充。
`_fill()` 用**单趟正则替换**（防止变量值本身含 `{xxx}` 被二次替换污染）；列表值顿号连接（"咖啡、拿铁"）。

##### `response_parser.py`（98 行）

`_extract_json()` 三级提取：直接解析 -> 剥 ```json 代码块 -> 首个平衡花括号块。
`parse_judge_response()` 校验：score 缺失/越界抛异常（触发重试）；dimension_scores 整体缺失抛异常；**个别维度缺失只记 missing_dimensions 警告**（docstring 详细说明了为什么——模板输出维度和 case.dimensions 语义不同）。

##### `judge_client.py`（127 行）

`__init__`：无 API Key 时**延迟失败**（self._client=None，score() 时返回 error）——纯规则评测（L1 冒烟）无需 Judge 也能跑。
`score()`：显式重试循环（最多 retry_count 次，相同 prompt），网络异常/解析失败都触发重试，耗尽后返回 error 结果不抛异常。成功时调 cost_tracker.track() 记账。

##### `evaluator.py`（67 行）

JudgeEvaluator：只处理 AgentCase + method=llm_judge；执行阶段已失败（error_analysis 非空且无回复）直接跳过；阈值判定写 passed/score/judge_reason。

#### `trace_evaluator.py`（83 行）

`evaluate_trace_assertion()`（行 16）：独立函数，处理 bool/`>=N`/`<=N`/`contains:X`/`not_null`/精确匹配六种 expected 形态。
`TraceEvaluator.evaluate()`：断言失败记 trace_errors，missing_fields 中确实为空的记 trace_gaps，**不碰 passed**（文档 5.3：附加评测器只记录）。

#### `pipeline.py`（84 行）—— 路由核心，整个评测的中枢

`evaluate()`（行 38）完整实现了文档 5.3 路由规则，读它对照下表：

```
0. 执行失败 -> 跳过全部评测 + 防御性 passed=False
1. method=automated          -> RuleEvaluator（主）
2. method=llm_judge          -> JudgeEvaluator（主）+ rules 非空再跑 RuleEvaluator（附加）
3. method=manual             -> 不自动判断
4. trace section 存在        -> TraceEvaluator（附加）
5. 盖 evaluated_at 时间戳
```

**对应测试：** `tests/test_evaluators.py`（30 用例，含 7 个 Pipeline 路由场景）

---

### 第 5 层：执行引擎 `eval_core/runner/`（324 行）

#### `api_executor.py`（126 行）

| 函数 | 行号 | 说明 |
|------|:---:|------|
| `resolve_variables()` | 24 | `{{VAR}}` -> 环境变量。**未定义的保持原样**（便于发现配置缺失） |
| `execute()` | 47 | 成功：格式化响应进 actual_reply（passed 留给评测器判）；失败：passed=False + error_analysis |
| `_send_with_retry()` | 63 | **只对超时/传输错误重试**（指数退避）；HTTP 5xx 是有效响应不重试。返回 (response, latency_ms) |
| `_format_response()` | 107 | 响应 -> `{"status_code", "headers", "body", "latency_ms"}` JSON 字符串 |

**设计点：** latency 用 perf_counter 自测（httpx 的 response.elapsed 在异步场景不可靠，开发时踩过坑）。

#### `agent_runner.py`（142 行）—— 多轮对话执行

**读代码时抓四个点：**

1. **session_id 隔离**（行 50 附近）：`eval_{case_id}_{uuid8}`，每个 case 唯一
2. **conversation_history 跨轮传递**：上一轮的 user+assistant 传给下一轮
3. **trace 浅合并**（行 75 附近）：`merged_trace.update(trace)` 每轮叠加，后轮覆盖同名字段——"第 1 轮 memory_write_event + 第 2 轮 memory_retrieval_query"都能在合并视图里断言
4. **失败终止**：某轮失败 -> passed=False + error_analysis -> 不再执行后续轮次（LLM-as-Judge 文档第 2 步）

Agent API 契约（请求/响应格式）写在模块 docstring，被测系统照此实现。

#### `orchestrator.py`（56 行）

串行 for 循环：`_execute()`（isinstance 分发到 APIExecutor/AgentRunner）-> `pipeline.evaluate()`。单个 case 异常被 catch 住标记失败，**不中断整体**。

**对应测试：** `tests/test_runner.py`（13 用例，MockTransport 模拟 HTTP/Agent）

---

### 第 6 层：结果处理 + 报告

#### `result/cost_tracker.py`（81 行）

`track(caller, model, input_tokens, output_tokens)` 记一笔账；定价从 global.yaml 读取；未知定价模型按 0 计并在 summary 标注（不会悄悄算错）。

#### `result/aggregator.py`（110 行）

`aggregate()` 返回一个大 dict：total / by_priority / by_dimension / by_tag / failures（含归因明细）/ skipped。按维度聚合只统计 AgentCase 的 dimensions 字段。

#### `result/snapshot.py`（45 行）

`record()` 生成依赖快照 dict（agent/judge 配置 + 种子 + 时间），随报告输出。

#### `report/console.py`（103 行）

`_fmt_rate()` 实现样本量规则（<30 报 X/Y，>=30 报百分比）；`generate()` 拼终端报告文本（总览/优先级/维度/失败明细/成本/快照）。

#### `report/json_reporter.py`（56 行）

完整报告落盘（含每个 case 的 result 全字段），文件名 `report_{timestamp}.json`。

#### `report/builder.py`（43 行）

Console（打印）+ JSON（落盘）统一调度。

**对应测试：** `tests/test_result.py`（11）+ `tests/test_report.py`（5）

---

### 第 7 层：总装配 `eval_core/cli/main.py`（186 行）

**读这一个文件 = 复习全流程。** `_run()`（行 92）按注释分 7 步：

```
1. 加载配置（global.yaml）
2. 加载用例（discoverer.load_all）
3. 筛选（CaseFilter）
4. 组装组件（CostTracker -> APIExecutor/AgentRunner -> JudgeEvaluator -> Pipeline -> Orchestrator）
5. 执行（orchestrator.run）
6. 聚合 + 快照 + 报告
7. 退出码（有失败=1，供 CI 阻塞）
```

`run` 命令的 click 参数即 CLI 全部能力（--case/--case-dir/--tags/--priority/--dimension/--config/--report-dir）。

---

## 四、配置与用例文件

```
config/
├── global.yaml             # agent(judge 定价 种子 重试) —— 所有组件的参数源
├── prompt_templates.json   # 5 个 Judge 模板（共情/记忆/安全/工具/分析）
└── agents/                 # 各 Agent 类型配置（companion 完整，其余占位）

cases/                      # 示例用例（对应文档附录 A）
├── api/smoke/API_001.json        # A.1：18 行 API case
└── agent/companion/
    ├── smoke/C001.json           # A.2：20 行最小 smoke（无 trace/golden）
    └── regression/C002.json       # A.3：45 行完整（trace + golden + failure）

profiles/companion/white_collar_insomnia.json   # A.4：可复用 profile
```

---

## 五、关键设计决策速查（代码里的"为什么"）

| 设计决策 | 代码位置 | 一句话理由 |
|----------|----------|-----------|
| 双模型不继承 | `models/__init__.py` | 15 个类只有 7 个字段交集，继承收益小于复杂度 |
| extra="forbid" | 所有 BaseModel | 拼写错误在加载时拦截，而非运行到断言才发现 |
| RuleEvaluator 不感知 case 类型 | `rule_evaluator.py::_build_context` | 合并视图让 API/Agent 共用一个评测器 |
| 主/附加评测器 | `pipeline.py::evaluate` | 文档 5.3：method 定主（决定 passed），section 定附加（只记录或拉低） |
| trace 不影响 passed | `trace_evaluator.py` | trace 是归因信息，不是通过标准 |
| dimension 缺失只警告 | `response_parser.py` docstring | 模板输出维度 ≠ case 覆盖维度，严格校验会误杀合法用例 |
| 5xx 不重试 | `api_executor.py::_send_with_retry` | 5xx 是有效响应，只有超时/传输错误才值得重试 |
| Judge 无 Key 延迟失败 | `judge_client.py::__init__` | L1 冒烟只跑规则，不应因缺 Judge 配置而崩 |
| 失败 case 终止后续轮次 | `agent_runner.py::execute` | 第 1 轮都失败了，第 2 轮没有意义 |
| 单 case 异常不中断整体 | `orchestrator.py::run` | 评测跑批，一个 case 崩了不能毁掉整轮报告 |

---

## 六、测试与验证

```
.venv/bin/python -m pytest tests/ -v     # 133 个用例，约 1 秒
make test                                # 同上
```

| 测试文件 | 验证什么 | 阅读建议 |
|----------|----------|----------|
| `test_models.py` | 模型即校验器（Pydantic 承担全部校验） | 最先读，建立"什么数据合法"的直觉 |
| `test_evaluators.py` | 路由规则 + Judge 解析边界 | **和 pipeline.py 对照读**，7 个路由场景就是文档 5.3 的可执行版本 |
| `test_runner.py` | MockTransport 模拟外部系统 | 看 mock handler 的构造方式，理解 Agent API 契约 |
| 其余 4 个 | 各自模块的边界情况 | 按需查阅 |

**测试边界（如实声明）：** JudgeClient 的 openai SDK 真实调用路径、真实网络均通过 Mock 绕过，逻辑已覆盖，真实联调待接入被测系统后进行。
