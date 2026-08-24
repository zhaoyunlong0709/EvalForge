# EvalForge - Agent 评测框架

> 轻量、可扩展、工程化的 Agent 评测框架。支持全自动规则断言、LLM-judge 半自动评估、
> Trace 链路校验，三种评测方式共存，按需组合，分层执行。
> 同时提供原生 API 测试能力，通过独立 Case 模型承载，共用执行引擎和评测管线。

## 快速开始

```bash
# 安装依赖
make install

# 运行评测（全部 case）
eval-forge run

# 运行指定目录的 case
eval-forge run --case-dir cases/agent/companion/smoke

# 运行指定文件
eval-forge run --case cases/api/smoke/API_001.json

# 按 tags 筛选（L1 冒烟）
eval-forge run --tags smoke

# 按 priority 筛选（L2 回归：P0+P1）
eval-forge run --priority P0 --priority P1
```

## 架构

详见 `product_text/Agent评测框架设计方案.md`。

```
CLI 入口层 -> 配置管理层 -> 用例加载层 -> 执行引擎层 -> 评测引擎层 -> 结果处理层
```

- **双模型架构**：APICase（~18 行，API 测试）和 AgentCase（~20-45 行，Agent 评测）
  两个独立 Pydantic 模型，通过 `case_type` discriminator 区分，共用执行引擎和评测管线。
- **四种评测器**：RuleEvaluator（全自动）/ JudgeEvaluator（LLM-judge）/
  TraceEvaluator（链路校验）/ SafetyEvaluator（Phase 3）。
- **分层执行**：L1 冒烟（commit）-> L2 回归（PR）-> L3 全量（发版）。

## 目录结构

```
eval_core/           框架主包
  models/            Pydantic 数据模型
  loader/            用例加载（发现/加载/Profile 解析/筛选）
  runner/            执行引擎（编排/API执行/Agent执行）
  evaluators/        评测引擎（规则/Judge/Trace/管线）
  operators/         规则操作符
  result/            结果处理（成本追踪/聚合/快照）
  report/            报告生成（Console/JSON/Markdown）
  cli/               命令行入口
  utils/             工具（日志/重试）
config/              全局配置 + Agent 类型配置 + Judge prompt 模板
cases/               评测用例（api/ 和 agent/）
profiles/            可复用前置条件
reports/             评测报告输出
tests/               框架自身的单元测试
```

## 开发阶段

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | 核心评测链路（模型/加载/执行/评测/报告/CLI） | ✅ 已完成 |
| Phase 2 | Judge 校准 + PassKEvaluator + 记忆召回校验 | ⬜ |
| Phase 3 | Mock 系统 + 并发控制 + SafetyEvaluator | ⬜ |
| Phase 4 | 用例管理 + 搜集管道 + SQLite | ⬜ |
| Phase 5 | Markdown 报告 + 基线对比 + CI | ⬜ |
| Phase 6 | 多 Judge 投票 + Case 依赖管理 | ⬜ |
