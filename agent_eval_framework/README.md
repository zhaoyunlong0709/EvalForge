# EvalForge - Agent 评测框架

> 轻量、可扩展、工程化的 Agent 评测框架。支持全自动规则断言、LLM-judge 半自动评估、
> Trace 链路校验，三种评测方式共存，按需组合，分层执行。
> 同时提供原生 API 测试能力，通过独立 Case 模型承载，共用执行引擎和评测管线。

---

## 目录

- [快速开始](#快速开始)
- [CLI 命令模版](#cli-命令模版)
  - [运行评测](#运行评测)
  - [筛选用例](#筛选用例)
  - [Mock 模式（离线）](#mock-模式离线)
  - [PassK 与并发](#passk-与并发)
  - [Baseline 基线管理](#baseline-基线管理)
  - [用例校验与格式检查](#用例校验与格式检查)
- [报告说明](#报告说明)
- [目录结构](#目录结构)
- [开发阶段](#开发阶段)

---

## 快速开始

```bash
# 1. 安装依赖 + 注册 eval-forge 命令（editable 模式，改代码立即生效）
pip install -e .

# 2. 运行全部用例
eval-forge run

# 3. 查看帮助
eval-forge run --help
```

> 也可不安装，直接用模块方式运行：`python -m eval_core.cli.main run`
>
> **注意**：`run` 等命令的默认路径（`config/global.yaml`、`cases/`、`profiles/`）都是相对
> 当前目录解析的，请在 `agent_eval_framework/` 目录下执行命令。

---

## CLI 命令模版

### 运行评测

```bash
# 运行全部用例
eval-forge run

# 运行指定目录（可多次）
eval-forge run --case-dir cases/agent/companion/benchmark
eval-forge run --case-dir cases/agent/companion --case-dir cases/api/smoke

# 运行指定文件（可多次）
eval-forge run --case cases/api/smoke/API_001.json
eval-forge run --case cases/agent/companion/golden/G001.json

# 指定报告输出目录（默认 reports/）
eval-forge run --report-dir reports/v1.1
```

### 筛选用例

所有筛选条件均为 **OR 语义**，多个条件任一匹配即选中。

```bash
# 按 tags 筛选（L1 冒烟）
eval-forge run --tags smoke
eval-forge run --tags smoke --tags regression

# 按优先级筛选（L2 回归：P0+P1）
eval-forge run --priority P0 --priority P1

# 按维度筛选
eval-forge run --dimension "D9.上下文记忆"
eval-forge run --dimension "D1.情绪识别" --dimension "D3.安全合规"
```

### Mock 模式（离线）

不调真实 Agent 和 Judge API，使用每个 case 的 `golden` 标注作为评分依据，
适合离线验证框架功能、调试用例、CI 冒烟。

```bash
# 完整 mock：Agent 返回通用回复，Judge 按 golden.overall_score 打分
eval-forge run --case-dir cases/agent/companion/benchmark --mock

# 可与筛选等所有参数组合
eval-forge run --case-dir cases/agent/companion/benchmark --mock --priority P0
```

> Mock 模式下 pass/fail 判定：`golden.overall_score >= evaluation.threshold` 即通过。

### PassK 与并发

```bash
# PassK 模式：每个 case 执行 3 次，至少过 2 次算通过（诊断 flaky case）
eval-forge run --pass-k 3 --pass-threshold 2

# 并发执行（配合 API 速率限制，如 --concurrency 5）
eval-forge run --concurrency 5
```

### Baseline 基线管理

基线 = "最近一次已发布版本的快照"，是后续每次评测的退化检测参照物。

**核心规则：**

- 保存 baseline 始终**手动触发**（`--save-baseline`），不会自动保存
- 报告生成 + 基线对比**每次自动执行**，报告里必有比对结果
- 保存时带**退化防护**：P0（安全红线）/ P1（基本功能）存在失败、整体通过率下降、
  或非 P0/P1 用例退化，都会拒绝保存，除非加 `--force`

```bash
# 首次保存（无历史基线，跳过比对直接保存）
eval-forge run --case-dir cases/agent/companion/benchmark --mock --save-baseline v1.0

# 之后每次运行自动对比最新基线（无需 --save-baseline，只跑评测+对比）
eval-forge run --case-dir cases/agent/companion/benchmark --mock

# 保存新基线：有退化时会被拒绝
eval-forge run --case-dir cases/agent/companion/benchmark --mock --save-baseline v1.1
#   → 若检测到退化，提示"拒绝保存 baseline"并给出原因

# 强制保存（明知更差仍要发，如刻意缩减用例范围）
eval-forge run --case-dir cases/agent/companion/benchmark --mock --save-baseline v1.1 --force

# 指定 baseline 存储目录（默认 baselines/，可用于隔离不同项目的基线）
eval-forge run --save-baseline v1.0 --baselines-dir baselines/companion
```

**查看历史基线：**

```bash
# 列出所有已保存的基线
eval-forge baseline list

# 查看某个版本的基线详情
eval-forge baseline show v1.0
```

### 用例校验与格式检查

```bash
# Dry-run：只校验用例格式，不执行评测（用例开发阶段快速检查）
eval-forge run --case-dir cases/agent/companion/benchmark --dry-run
```

---

## 报告说明

每次运行生成 **Console（终端）+ JSON + Markdown** 三种报告，输出到 `--report-dir`
（默认 `reports/`）。

**Markdown 报告结构：**

| 章节 | 内容 |
|------|------|
| 一、结论 | 发版判定 + P0 指标 + **基线对比一句话摘要**（每次必含） |
| 二、按优先级 | P0/P1/P2 通过率 |
| 三、按维度 | 各维度通过率 |
| 四、失败 case 明细 | 失败 case 及原因 |
| 五、Baseline 对比 | 总分比对（通过率+平均分）+ 详细比对（按优先级/维度）+ case 级变化 |
| 附录 | 评测配置 + 成本 |

**基线对比章节行为：**

- **首次评测（无基线）**：显示"首次评测，无历史基线可比"，并提示如何保存首个基线
- **非首次**：展示完整三层对比——总分、按维度（通过率 + 平均分）、case 级变化
  （退化 / 修复 / 分数变化 / 新增 / 移除）

---

## 目录结构

```
eval_core/           框架主包
  models/            Pydantic 数据模型
  loader/            用例加载（发现/加载/Profile 解析/筛选）
  runner/            执行引擎（编排/API执行/Agent执行）
  evaluators/        评测引擎（规则/Judge/Trace/管线）
  operators/         规则操作符
  result/            结果处理（成本追踪/聚合/基线/快照）
  report/            报告生成（Console/JSON/Markdown）
  cli/               命令行入口
  utils/             工具（日志/限流等）
config/              全局配置 + Judge prompt 模板
cases/               评测用例
  api/               API 测试用例（smoke/regression）
  agent/             Agent 评测用例（companion/code_agent/customer_service/data_analysis）
    companion/        陪伴型 Agent
      benchmark/      （基准集：覆盖全维度，95%+ 通过率）
      candidate/      （候选集）
      golden/         （金标集）
      smoke/          （冒烟集）
      regression/     （回归集）
      adversarial/    （对抗集）
profiles/            可复用前置条件
reports/             评测报告输出（运行时生成，不入 git）
baselines/           基线文件（运行时生成，不入 git）
tests/               框架自身的单元测试
```

---

## 开发阶段

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | 核心评测链路（模型/加载/执行/评测/报告/CLI） | ✅ 已完成 |
| Phase 2 | Judge 校准 + PassKEvaluator + 记忆召回校验 | ⬜ |
| Phase 3 | Mock 系统 + 并发控制 + SafetyEvaluator | ⬜ |
| Phase 4 | 用例管理 + 搜集管道 + SQLite | ⬜ |
| Phase 5 | Markdown 报告 + 基线对比 + CI | ✅ 已完成 |
| Phase 6 | 多 Judge 投票 + Case 依赖管理 | ⬜ |
