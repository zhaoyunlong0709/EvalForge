"""共享类型，APICase 和 AgentCase 共用。

严格对应《Agent评测框架设计方案》4.1.1 节。
"""
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Priority(str, Enum):
    """用例优先级。P0=阻塞发版，P1=版本达标，P2=迭代优化，P3=技术储备"""
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class CaseResult(BaseModel):
    """测试结果，两种 case 共用。

    ⚠️ 永远不在 case JSON 文件中出现，运行时填充。
    """
    model_config = ConfigDict(extra="forbid")
    # AgentCase=Agent 回复文本；APICase=格式化后的 HTTP 响应 JSON 字符串
    actual_reply: str = ""
    # AgentRunner 填充 Trace 数据，TraceEvaluator 读取
    actual_trace: dict[str, Any] = {}
    score: float = 0.0
    passed: bool = False
    error_analysis: str = ""
    # JudgeEvaluator 填充评分理由
    judge_reason: str = ""
    dimension_scores: dict[str, float] = {}
    # TraceEvaluator 填充断言失败项
    trace_errors: list[dict] = []
    # TraceEvaluator 填充可观测性盲区
    trace_gaps: list[str] = []
    execution_time_ms: int = 0
    evaluated_at: str = ""
    # PassK 模式（--pass-k）时填充，普通模式保持空 dict 不序列化输出
    pass_k_detail: dict[str, Any] = {}


class RuleAssertion(BaseModel):
    """规则断言，两种 case 共用。

    field 提取规则：以 "$." 开头 -> jsonpath 提取；否则 -> 点号路径提取（如 "body.status"）
    operator 只负责比较，不做字段提取。
    """
    model_config = ConfigDict(extra="forbid")
    field: str
    operator: Literal[
        "eq", "neq", "gt", "gte", "lt", "lte",
        "contains", "not_contains", "regex",
        "not_null", "is_null",
    ]
    value: Any = None


class RuleBasedEvaluation(BaseModel):
    """规则评测配置，两种 case 共用（APICase 使用此类型）"""
    model_config = ConfigDict(extra="forbid")
    method: Literal["automated"] = "automated"
    rules: list[RuleAssertion] = []
    on_fail: Literal["block", "warn"] = "block"


class CaseLifecycle(BaseModel):
    """用例生命周期 ⚠️ [Phase 4] 引入，Phase 1-3 用目录结构替代"""
    model_config = ConfigDict(extra="forbid")
    status: Literal["draft", "review", "active", "deprecated"] = "draft"
    created_at: str = ""
    created_by: str = ""
    source: str = "manual"
