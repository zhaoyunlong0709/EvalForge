"""APICase 模型，传统 API 测试用例。

严格对应《Agent评测框架设计方案》4.1.2 节。~18 行 JSON。
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .shared import CaseLifecycle, CaseResult, Priority, RuleBasedEvaluation


class APIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]
    url: str
    headers: dict[str, str] = {}
    query_params: dict[str, str] = {}
    body: dict[str, Any] | str | None = None
    timeout_ms: int = 30000
    # HTTP 重定向跟随（301/302），不是请求重试
    follow_redirects: bool = True


class APICase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_type: Literal["api"] = "api"

    # ---- 测试规范（必填） ----
    case_id: str
    title: str
    version: str
    priority: Priority
    scenario: str
    tags: list[str] = []
    lifecycle: CaseLifecycle = CaseLifecycle()  

    # ---- API 请求（必填） ----
    request: APIRequest

    # ---- 评测配置（必填，只有规则评测） ----
    evaluation: RuleBasedEvaluation

    # ---- 测试结果（运行时填充，不在 case 文件中） ----
    result: CaseResult = CaseResult()
