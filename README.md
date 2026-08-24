# EvalForge
> 轻量、可扩展、工程化的 Agent 评测框架。支持规则断言/LLM-judge/Trace 链路校验三种评测方式共存，按需组合，分层执行。

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Tests](https://github.com/zhaoyunlong0709/EvalForge/actions/workflows/tests.yml/badge.svg)](https://github.com/zhaoyunlong0709/EvalForge/actions)

## 🎯 设计目标

现有 Agent 评测框架要么过于笨重（依赖多、复杂度高），要么过于简单（只支持 LLM-judge 不支持规则/Trace 校验）。EvalForge 旨在做一个**轻量但工程化**的框架，满足日常测试需求：

| 特性 | EvalForge | 其他框架 |
|---|---|---|
| 轻量依赖 | ✅ 核心仅 12 个依赖 | ❌ 经常 20+ 依赖 |
| 三种评测方式共存 | ✅ 规则/LLM-judge/Trace 按需组合 | ❌ 大多只支持 LLM-judge |
| APICase/AgentCase 统一管线 | ✅ 两种用例复用规则评测器 | ❌ 一般只做 Agent，不支持 API |
| 插件化扩展 | ✅ 评测器/操作符/Case 类型全可扩展 | ❌ 扩展成本高 |
| 工程化特性 | ✅ 并发控制/Mock/pass@k/基线对比/成本追踪 | ❌ 很多基础工程特性缺失 |

## ✅ 当前进度（2026-08 更新）

| Phase | 名称 | 进度 | 状态 |
|---|---|---|---|
| Phase 1 | 核心评测链路 | 100% | ✅ 已完成，215 个单元测试全绿 |
| Phase 2 | 评测引擎完善 | 60% | PassK ✅ / 记忆召回校验 ✅ / Judge 校准 ⚠️ / CaseMigrator ⚠️ |
| Phase 3 | Mock + 工程化 | 80% | MockManager ✅ / 并发+速率限制 ✅ / SafetyEvaluator ⚠️ / --max-cost ⚠️ |
| Phase 4 | 用例管理 + 搜集 | 20% | CaseRepository+SQLite ✅ / 搜集管道 ⚠️ / 生命周期状态机 ⚠️ |
| Phase 5 | 报告 + CI | 100% | ✅ 已完成（Markdown 报告 / Baseline 对比 / Dry-Run / GitHub Actions） |
| Phase 6 | 进阶 | 0% | ⚠️ 未开始 |

## 🏗 架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              CLI 入口层                                   │
│         eval-forge run ...   python -m eval_core run ...                  │
└──────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────┴─────────────────────────────────────┐
│                             配置管理层                                    │
│  GlobalConfig / PromptTemplates / AgentConfig / Reproducibility         │
│  配置层次：CLI Override > Case Config > Agent Config > Global Config     │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────┴─────────────────────────────────────┐
│                             用例加载层                                    │
│  CaseDiscoverer → LoaderRegistry → APICaseLoader / AgentCaseLoader      │
│  根据 case_type 字段分发到对应 Loader                                     │
│  ┌──────────────┐  ┌──────────────┐                                    │
│  │  APICase     │  │  AgentCase   │                                    │
│  │  ~18 行      │  │  ~20-45 行   │                                    │
│  │              │  │              │                                    │
│  │  request    │  │  conversation │                                    │
│  │ evaluation  │  │  evaluation   │                                    │
│  └──────────────┘  └──────────────┘                                    │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────┴─────────────────────────────────────┐
│                             执行引擎层                                    │
│  Orchestrator 统一调度，根据 case_type 分发到对应执行器                   │
│  APIExecutor → 发送 HTTP 请求 → 格式化响应 → 填充 result                 │
│  AgentRunner  → 多轮对话执行 → Trace 收集 → session_id 隔离             │
│  MockManager  → 工具/记忆/时间 Mock 拦截器链 ✅                          │
│  ConcurrencyController → 并发控制 + 速率限制 ✅                          │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────┴─────────────────────────────────────┐
│                             评测引擎层（核心）                             │
│  EvaluatorPipeline 路由分发，并行执行多个评测器，结果聚合                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │RuleEvaluator │  │JudgeEvaluator│  │TraceEvaluator│  │SafetyEvaluator│ │
│  │  全自动规则   │  │ LLM-as-Judge │  │ Trace 校验   │  │  ⚠️ [P3]     │ │
│  │  JSONPath    │  │  模板+阈值   │  │ 记忆召回断言 │  │             │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘ │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────┴─────────────────────────────────────┐
│                             结果处理层                                    │
│  CostTracker → token 成本统计，定价从配置读取，不硬编码                   │
│  ResultAggregator → 按维度/优先级/版本聚合结果                           │
│  BaselineManager → 基线对比 + 退化检测 ✅                                │
│  ReportBuilder → Console/JSON/Markdown 三种格式报告 ✅                    │
└─────────────────────────────────────────────────────────────────────────┘
```

## 🚀 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置
编辑 `config/global.yaml`，配置你的 Judge 模型：
```yaml
judge:
  model: "your-judge-model"
  base_url: "https://ark.cn-beijing.volces.com/api/v3"  # 火山方舟示例
  api_key_env: "OPENAI_API_KEY"  # 从环境变量读 key，避免明文
```

### 3. 编写用例

举个最简单的 Agent 冒烟用例 (`cases/agent/companion/smoke/C001.json`):
```json
{
  "case_type": "agent",
  "case_id": "C001",
  "title": "晚间情绪陪伴-共情回应",
  "version": "v0.1",
  "priority": "P1",
  "scenario": "晚间情绪陪伴",
  "dimensions": ["D2.情绪识别", "D8.共情适当率"],
  "tags": ["陪伴", "情绪", "smoke"],
  "profile": "white_collar_insomnia",
  "conversation": [
    {"turn": 1, "user": "我今天又被老板否了方案，感觉自己特别没用。", "expect": "先共情，不急于给解决方案"}
  ],
  "evaluation": {
    "method": "llm_judge",
    "template": "共情评估",
    "params": {"user_emotion": "挫败/悲伤"},
    "threshold": 4.0
  }
}
```

### 4. 运行评测
```bash
# 全量评测
python -m eval_core run

# 指定目录
python -m eval_core run --case-dir cases/agent/companion/smoke

# pass@k 模式，对 flaky case 跑 k 次多数表决
python -m eval_core run --pass-k 3 --pass-threshold 2
```

## 📐 分层执行策略

| 层级 | 触发时机 | case 范围 | 耗时 | 阻塞发版 |
|---|---|---|---|---|
| L1 冒烟 | 每次 commit | tags=smoke (10-20个) | < 30s | 是 |
| L2 回归 | 每次 PR | P0+P1 (50-80个) | < 5min | 是 |
| L3 全量 | 发版前 | 全部 case | < 30min | 是 |

## 🎨 扩展性设计

EvalForge 所有核心组件都是插件化的，新增类型不需要修改核心代码：

- **新增评测器**：实现 `evaluate(case: EvaluableCase) → EvaluableCase` 接口，注册到 `EvaluatorPipeline`
- **新增操作符**：在 `RuleAssertion.operator` 添加定义，在 `RuleEvaluator.OPERATORS` 注册实现
- **新增 Case 类型**：定义 Pydantic 模型，实现 Loader 和 Executor，注册到对应注册表

## 🧪 可复现性设计

- 固定 Agent/Judge 温度为 0
- 支持配置随机种子，可复现
- Mock 系统：工具/记忆/时间全可 Mock，精确控制外部依赖
- pass@k：对 flaky case 跑 k 次，过 m 次算通过
- 依赖快照：每次评测记录 Agent/Judge 模型版本，方便对比

## 📖 设计背景

本框架基于作者独立设计的 **[Agent 评测维度体系](https://github.com/zhaoyunlong0709/my_python/blob/master/my_python/notes/agent/评测体系/agent评测维度体系.md)**，核心思路是：

> 区分「产品感知层」和「工程链路层」两套互补维度：
> - 产品感知层：9 个维度，加权聚合给 PM/老板看结论
> - 工程链路层：10 个一级维度沿执行链路拆解，测试工程师按这个设计用例方便问题定位
> - 不同类型 Agent（陪伴/代码/客服/数据分析）差异化调整维度和优先级

## 📄 License

MIT

## 👤 Author

赵云龙 - [github.com/zhaoyunlong0709](https://github.com/zhaoyunlong0709)
