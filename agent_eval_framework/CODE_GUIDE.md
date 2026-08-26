# EvalForge 代码导读（全 6 Phase 完成版）

> 目标：读完本文档后能快速定位和理解框架代码的每个文件。
> 全文按**依赖顺序**组织（先读被依赖的），配合 `product_text/Agent评测框架设计方案.md` 使用。
> 阅读时间预估：文档 20 分钟 + 代码 3-4 小时。

---

## 一、当前状态

| 指标 | 数值 |
|------|------|
| Phase | 1-6 全部完成 |
| 测试总数 | 269 个，全部通过 |
| CLI 命令 | 10 个（run / collect / case / baseline / calibrate / calibration-status） |
| 核心模块 | 8 个（models / loader / runner / evaluators / mock / collector / case_manager / report） |

---

## 二、全局鸟瞰：一个 Case 的完整生命周期

```
eval-forge run --tags smoke
   │
   ▼
cli/main.py::_run()                    总装配：读配置 -> 组装所有组件
   │
   ├── 依赖排序（case 有 depends_on 时）
   │
   ▼
loader/discoverer.py::load_all()       扫描 cases/ 目录，发现 JSON 文件
   │
   ▼
loader/registry.py::load()             读 case_type 字段，分发给对应 Loader
   │
   ▼
loader/agent_loader.py::load()         解析 profile 引用 -> 填充 setup
   │                                   （loader/filter.py 按 tags/priority 筛选）
   ▼
case_manager/repository.py::sync()     SQLite 索引同步（增量/全量）
   │
   ▼
runner/orchestrator.py::run()          串行/并发调度（--concurrency N）
   │
   ├── mock/manager.py                 拦截器：工具 Mock / 记忆注入 / 时间覆盖 / 完全 Mock
   │
   ├── runner/agent_runner.py          多轮对话执行（session_id 隔离 + trace 合并）
   │     │                              rate_limiter 令牌桶控制请求速率
   │     └── runner/api_executor.py    （APICase 走这里）
   │
   ▼
evaluators/pipeline.py::evaluate()     路由（文档 5.3 规则）：
   │
   ├── evaluators/rule_evaluator.py    11 个操作符断言（主/附加）
   ├── evaluators/judge/evaluator.py   单/多 Judge 模型，并行 + 加权平均
   ├── evaluators/trace_evaluator.py   Trace 断言 + 盲区检测
   └── evaluators/safety_evaluator.py  有害内容/PII/越狱检测（始终执行）
   │
   ▼
result/aggregator.py::aggregate()     按优先级/维度/标签聚合
result/cost_tracker.py                统计 Judge/Agent token 成本
result/snapshot.py                    记录依赖快照（可复现性）
result/baseline.py                    基线对比（退化检测）
   │
   ▼
report/builder.py::generate_all()     Console + JSON + Markdown 三份报告
   │
   ▼
退出码：全部通过=0，有失败=1（供 CI 阻塞）
```

**核心设计思想（贯穿全代码）：**
1. **双模型不继承**：APICase 和 AgentCase 是独立 Pydantic 模型，通过 `case_type` discriminator 统一为 `EvaluableCase`
2. **评测器不感知 case 类型**：RuleEvaluator 只读 `evaluation.rules` + `result`，对 API/Agent 通吃
3. **`result` 是数据总线**：执行器写入，评测器读取并回写
4. **Mock 拦截器链**：MockManager 在 AgentRunner 之前拦截，命中返回 Mock 结果，未命中透传
5. **SafetyEvaluator 始终执行**：安全是红线，零成本正则检测，不依赖外部 API

---

## 三、推荐阅读路线（8 层，由浅入深）

```
第 1 层 地基    models/（4 文件）           不读懂模型读不懂其他任何代码
第 2 层 工具    operators/ + utils/（4 文件） 独立无依赖，可单独理解
第 3 层 加载    loader/（6 文件）            JSON 文件 -> 内存对象
第 4 层 Mock    mock/（1 文件）              拦截器链，在评测引擎之前
第 5 层 评测    evaluators/（10 文件）       ★核心★ 框架的灵魂
第 6 层 执行    runner/（3 文件）            对外发起 HTTP 调用
第 7 层 结果    result/ + report/ + case_manager/（9 文件）
第 8 层 入口    cli/main.py                 总装配 + 10 个子命令
```

---

## 四、逐模块导读

### 第 1 层：数据模型 `eval_core/models/`

| 文件 | 说明 |
|------|------|
| `shared.py` | CaseResult（数据总线）/ Priority（P0-P3）/ RuleAssertion（11 个操作符）/ CaseLifecycle（draft/review/active/deprecated） |
| `api_case.py` | APIRequest + APICase（~18 行 JSON，只有 request + evaluation.rules） |
| `agent_case.py` | AgentCase 主模型（~20-45 行 JSON）。包含：ConversationTurn / AgentEvaluation / TraceExpectation / GoldenAnnotation / AgentSetup / FullAgentMock / ToolMock / MockConfig / depends_on 字段 |
| `__init__.py` | EvaluableCase Union 类型（case_type discriminator 自动路由） |

### 第 2 层：工具 `eval_core/operators/` + `utils/`

| 文件 | 说明 |
|------|------|
| `operators/operators.py` | 11 个操作符（eq/neq/gt/lt/contains/regex/jsonpath...）+ 字段提取 |
| `utils/logger.py` | loguru 配置 |
| `utils/rate_limiter.py` | 令牌桶算法 RateLimiter，支持 RPS 和 RPM（from_rpm） |

### 第 3 层：用例加载 `eval_core/loader/`

| 文件 | 说明 |
|------|------|
| `registry.py` | LoaderRegistry：读 case_type -> 分发到对应 Loader |
| `api_loader.py` | APICaseLoader：JSON -> Pydantic 校验 |
| `agent_loader.py` | AgentCaseLoader + ProfileResolver：profile 引用 -> 填充 setup |
| `discoverer.py` | CaseDiscoverer：扫描目录/指定文件/指定目录，坏文件跳过不中断 |
| `filter.py` | CaseFilter：按 tags/priority/dimensions 筛选 |

### 第 4 层：Mock 系统 `eval_core/mock/`

| 文件 | 说明 |
|------|------|
| `manager.py` | MockManager 拦截器链：四种 Mock（完全/记忆/时间/工具）。完全 Mock 跳过 HTTP，记忆 Mock 覆盖 preloaded_memories，时间/工具 Mock 注入 context |

### 第 5 层：评测引擎 `eval_core/evaluators/`（★核心★）

| 文件 | 说明 |
|------|------|
| `pipeline.py` | EvaluatorPipeline：路由核心（主评测器 + 附加评测器 + SafetyEvaluator 始终执行） |
| `rule_evaluator.py` | RuleEvaluator：11 个操作符断言，API/Agent 通吃（合并视图） |
| `trace_evaluator.py` | TraceEvaluator：断言求值（bool/>=/contains/not_null）+ 盲区检测 |
| `safety_evaluator.py` | SafetyEvaluator：有害内容（关键词）/PII 泄露（正则）/越狱检测（正则），PII 脱敏显示 |
| `judge/` 子目录 | |
| ├── `evaluator.py` | JudgeEvaluator：单/多模型并行，加权平均分，缓解同源偏见 |
| ├── `template_engine.py` | 模板查找 + 变量填充（conversation_history/golden_reference/params） |
| ├── `judge_client.py` | JudgeClient：LLM API 调用 + 重试（3 次，相同 prompt） |
| ├── `response_parser.py` | JSON 解析：三级提取（直接/代码块/花括号），维度缺失只警告 |
| └── `calibration.py` | CalibrationTracker：5 指标（Spearman/MAE/ExactMatch/±1/QWK）+ 过期检测 + 防标签泄露 |

### 第 6 层：执行引擎 `eval_core/runner/`

| 文件 | 说明 |
|------|------|
| `orchestrator.py` | Orchestrator：串行/并发（Semaphore）+ PassK 多数表决 + depends_on 拓扑排序 |
| `agent_runner.py` | AgentRunner：多轮对话（session_id 隔离）+ trace 浅合并 + rate_limiter 令牌桶 |
| `api_executor.py` | APIExecutor：HTTP 请求 + 变量替换 + 响应格式化 |

### 第 7 层：结果处理 + 报告 + 用例管理

| 文件 | 说明 |
|------|------|
| `result/aggregator.py` | 按维度/优先级/标签聚合 |
| `result/cost_tracker.py` | 成本统计（按调用方），pricing 从配置读取 |
| `result/snapshot.py` | 依赖快照（Agent/Judge 模型 + 种子 + 时间） |
| `result/baseline.py` | BaselineManager：保存/加载/对比/退化检测/修复检测/新增移除 |
| `report/console.py` | 终端报告（样本量规则：<30 报 X/Y，>=30 报百分比） |
| `report/json_reporter.py` | JSON 报告落盘 |
| `report/markdown_reporter.py` | Markdown 报告（给 PM 看，含 Baseline 对比 + 可复现性声明） |
| `report/builder.py` | ReportBuilder：Console + JSON + Markdown 统一调度 |
| `case_manager/repository.py` | CaseRepository：SQLite 索引 + 搜索 + 统计 + 逻辑删除 + 增量同步 |
| `collector/` | 用例搜集管道（ManualSource/Classifier/Formatter/Deduplicator/ReviewQueue/Pipeline） |

### 第 8 层：CLI 入口 `eval_core/cli/main.py`

| 命令 | 说明 |
|------|------|
| `run` | 执行评测（原有功能，支持 --pass-k / --concurrency / --save-baseline / --dry-run） |
| `collect interactive` | 交互式粘贴对话 -> 自动生成 case 草稿 |
| `collect review` | 查看审核队列 |
| `collect approve/reject` | 审核通过/驳回 |
| `case search/stats/deprecate` | 用例搜索/统计/作废 |
| `baseline list/show` | 基线列表/详情 |
| `calibrate` | Judge 校准（5 指标 + 防标签泄露） |
| `calibration-status` | 校准状态检查（是否过期/模型变更） |

---

## 五、关键设计决策速查

| 设计决策 | 代码位置 | 一句话理由 |
|----------|----------|-----------|
| 双模型不继承 | `models/__init__.py` | 15 个类只有 7 个字段交集，继承收益小于复杂度 |
| RuleEvaluator 不感知 case 类型 | `rule_evaluator.py::_build_context` | 合并视图让 API/Agent 共用 |
| 主/附加评测器 | `pipeline.py::evaluate` | method 定主（决定 passed），section 定附加 |
| SafetyEvaluator 始终执行 | `pipeline.py::evaluate` | 安全是红线，零成本正则，不依赖外部 API |
| 多 Judge 并行 + 加权平均 | `judge/evaluator.py` | 缓解同源偏见 |
| 防标签泄露 | `judge/calibration.py::calibrate` | 校准前 deepcopy + 剥离 golden，人工分不进 judge prompt |
| Mock 拦截器链 | `mock/manager.py` | 命中返回 Mock，未命中透传真实 Agent |
| PassK 多数表决 | `orchestrator.py::_run_with_pass_k` | k 次执行 + 多数表决，flaky case 诊断 |
| 并发 + 令牌桶 | `orchestrator.py` + `utils/rate_limiter.py` | Semaphore 限并发 + 令牌桶限速率 |
| 增量同步 | `case_manager/repository.py::sync_incremental` | 1000+ case 不扫全量 |
| 逻辑删除 | `case_manager/repository.py::update_status` | deprecated 标记，不删数据 |
| 单 case 异常不中断整体 | `orchestrator.py::run` | 评测跑批，一个 case 崩了不能毁掉整轮报告 |
| Judge 无 Key 延迟失败 | `judge/judge_client.py::__init__` | L1 冒烟只跑规则，不应因缺 Judge 配置而崩 |

---

## 六、测试与验证

```
python -m pytest tests/ -v     # 269 个用例，约 2.5 秒
```

| 测试文件 | 用例数 | 验证什么 |
|----------|:------:|----------|
| `test_models.py` | 20 | 模型即校验器 |
| `test_operators.py` | 25 | 11 个操作符 + 字段提取 |
| `test_loader.py` | 21 | 用例加载 + 分发 + 筛选 |
| `test_evaluators.py` | 30 | 路由规则 + Judge 解析边界 |
| `test_runner.py` | 13 | MockTransport 模拟 Agent/API |
| `test_result.py` | 11 | 聚合 + 成本 + 快照 |
| `test_report.py` | 5 | Console + JSON 报告 |
| `test_mock.py` | 16 | MockManager 四种 Mock |
| `test_case_manager.py` | 19 | SQLite 索引 + 搜索 + 统计 + 逻辑删除 |
| `test_pass_k.py` | 9 | PassK 多数表决 + 兼容性 |
| `test_baseline.py` | 16 | 基线保存/加载/对比/退化检测 |
| `test_markdown_report.py` | 10 | Markdown 报告结构 |
| `test_concurrency.py` | 12 | 并发控制 + 速率限制 |
| `test_collector.py` | 24 | 搜集管道（ManualSource/Classifier/Formatter/Deduplicator/ReviewQueue） |
| `test_calibration.py` | 16 | Judge 校准（5 指标 + 过期检测 + 防泄露） |
| `test_safety.py` | 14 | SafetyEvaluator（有害内容/PII/越狱） |
| **合计** | **269** | |

---

## 七、配置与用例文件

```
config/
├── global.yaml             # Agent/Judge/Reproducibility/Pricing/Retry 配置
├── prompt_templates.json   # 5 个 Judge 模板（共情/记忆/安全/工具/分析）
└── agents/                 # 各 Agent 类型配置

cases/
├── api/smoke/              # API 测试用例
└── agent/companion/        # Agent 评测用例
    ├── smoke/              # L1 冒烟
    ├── regression/         # L2 回归
    ├── golden/             # L3 全量
    └── adversarial/        # 对抗测试

profiles/                   # 可复用前置条件
baselines/                  # 基线数据（版本对比）
reports/                    # 评测报告输出
```
