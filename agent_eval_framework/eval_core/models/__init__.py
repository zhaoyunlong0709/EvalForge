"""数据模型包，导出所有 Case 类型及共享类型。

严格对应《Agent评测框架设计方案》4.1.4 节。
"""
from typing import Annotated

from pydantic import Field

from .shared import CaseLifecycle, CaseResult, Priority, RuleAssertion, RuleBasedEvaluation
from .api_case import APICase, APIRequest
from .agent_case import (
    AgentCase,
    AgentEvaluation,
    AgentSetup,
    ConversationTurn,
    FailureInfo,
    FullAgentMock,
    GoldenAnnotation,
    MockConfig,
    ToolMock,
    TraceAssertion,
    TraceExpectation,
)

# 统一类型：任何一个 case 都是 EvaluableCase。
# 通过 case_type 字段（discriminator）自动路由到 APICase 或 AgentCase。
EvaluableCase = Annotated[
    APICase | AgentCase,
    Field(discriminator="case_type"),
]

__all__ = [
    # 共享类型
    "Priority",
    "CaseResult",
    "RuleAssertion",
    "RuleBasedEvaluation",
    "CaseLifecycle",
    # API Case
    "APICase",
    "APIRequest",
    # Agent Case
    "AgentCase",
    "AgentEvaluation",
    "AgentSetup",
    "ConversationTurn",
    "TraceAssertion",
    "TraceExpectation",
    "FailureInfo",
    "GoldenAnnotation",
    "MockConfig",
    "ToolMock",
    "FullAgentMock",
    # Union 类型
    "EvaluableCase",
]
