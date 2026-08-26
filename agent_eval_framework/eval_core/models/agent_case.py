"""AgentCase 模型，Agent 评测用例。

严格对应《Agent评测框架设计方案》4.1.3 节。~20-45 行 JSON。
注意：Pydantic 要求被引用的类型先定义，子模型在前、主模型在后。
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .shared import CaseLifecycle, CaseResult, Priority, RuleAssertion


# ==================== 子模型（先定义） ====================

class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn: int
    user: str
    # 自然语言期望描述，替代原 {intent, behavior} 嵌套对象
    expect: str = ""


class AgentEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["automated", "llm_judge", "manual"]
    template: str = ""  # llm_judge 时填写，对应 prompt_templates.json 中的模板名
    params: dict[str, Any] = {}
    threshold: float = 4.0
    rules: list[RuleAssertion] = []


class TraceAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    # bool / ">=N" / "<=N" / "contains:X" / "not_null"
    expected: Any


class TraceExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 断言列表，有则启用 TraceEvaluator
    assertions: list[TraceAssertion] = []
    # 期望召回的记忆
    retrieved_memories: list[str] = []
    # 可观测性盲区
    missing_fields: list[str] = []


class FailureInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: str = ""
    category: str = ""
    root_cause: str = ""


class GoldenAnnotation(BaseModel):
    """Golden 标注，可选。正面 case 无 failure 字段"""
    model_config = ConfigDict(extra="forbid")
    annotator: str = ""
    annotated_at: str = ""
    expected_answer: str = ""
    key_points: list[str] = []
    overall_score: float = 0.0
    dimension_scores: dict[str, float] = {}
    failure: FailureInfo | None = None


class AgentSetup(BaseModel):
    """前置条件，由 ProfileResolver 从 profile 文件解析填充。case JSON 文件中不直接填写"""
    model_config = ConfigDict(extra="forbid")
    user_profile: str = ""
    preloaded_memories: list[str] = []


class ToolMock(BaseModel):
    """单个工具的 Mock 配置。

    mode:
      - error: 让工具返回错误（如 location_permission_denied）
      - return: 让工具返回指定结果（如温度 999 测异常识别）
      - delay: 让工具延迟返回（测超时兜底）
    """
    model_config = ConfigDict(extra="forbid")
    tool_name: str
    mode: Literal["error", "return", "delay"]
    error: dict[str, Any] = {}
    result: dict[str, Any] = {}
    delay_ms: int = 0


class FullAgentMock(BaseModel):
    """完全 Mock Agent 响应：不调用真实 Agent，直接返回预定义数据。

    用途：测 Judge 校准（固定 Agent 回复只测 judge 评分）、CI 冒烟（不依赖 Agent 服务）。
    """
    model_config = ConfigDict(extra="forbid")
    replies: list[str]
    trace: dict[str, Any] = {}


class MockConfig(BaseModel):
    """Mock 配置。有则启用 MockManager（框架设计方案 4.4 + Phase 3）。

    - agent 非空：完全 Mock，不调 Agent
    - memory 非空：覆盖 preloaded_memories（注入冲突记忆等）
    - time 非空：覆盖时间感知（测纪念日/过期记忆）
    - tools 非空：拦截工具调用（测工具失败/异常返回兜底）
    """
    model_config = ConfigDict(extra="forbid")
    tools: list[ToolMock] = []
    memory: list[str] = []
    time: str = ""
    agent: FullAgentMock | None = None


# ==================== 主模型（后定义） ====================

class AgentCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_type: Literal["agent"] = "agent"

    # ---- 测试规范（必填） ----
    case_id: str
    title: str
    version: str
    priority: Priority
    scenario: str
    dimensions: list[str] = []
    tags: list[str] = []
    lifecycle: CaseLifecycle = CaseLifecycle()  # ⚠️ [Phase 4] 启用

    # ---- 前置条件 ----
    # 引用名（case 文件中填写，如 "white_collar_insomnia"）
    profile: str = ""
    # 解析后的数据（Loader 填充，case 文件中不写）
    setup: AgentSetup | None = None

    # ---- 对话（必填） ----
    conversation: list[ConversationTurn]

    # ---- 依赖管理（可选） ----
    # 依赖的 case_id，当前 case 在该 case 执行完成后才执行
    depends_on: str = ""

    # ---- 评测配置（必填） ----
    evaluation: AgentEvaluation

    # ---- 可选 section（按需填写） ----
    # 有则启用 TraceEvaluator
    trace: TraceExpectation | None = None
    # 有则启用校准 + 回归保障
    golden: GoldenAnnotation | None = None
    # ⚠️ [Phase 3] 有则启用 MockManager
    mock: MockConfig | None = None

    # ---- 测试结果（运行时填充，不在 case 文件中） ----
    result: CaseResult = CaseResult()
