"""AgentRunner：多轮对话执行 + Trace 收集 + 超时/重试 + session_id 隔离。

对应《Agent评测框架设计方案》4.4 执行引擎模块 + Agent API 契约。

Agent API 契约（被测系统需实现）：
  请求 POST {endpoint}:
    {"session_id", "user_message", "context": {...}, "temperature"}
  响应:
    {"reply": str, "trace": dict, "usage": {"input_tokens", "output_tokens"}}

多轮 trace 合并策略：各轮 trace 浅合并（后轮覆盖同名字段）写入
result.actual_trace，支持"第 1 轮写入 + 第 2 轮召回"类断言。
某轮失败时标记 passed=False 并终止后续轮次（LLM-as-Judge 文档第 2 步）。
"""
import asyncio
import time
import uuid

import httpx

from eval_core.models import AgentCase
from eval_core.utils import logger

# 避免循环导入，仅做类型标注
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eval_core.mock import MockManager


class AgentRunner:
    """Agent 对话执行器"""

    def __init__(
        self,
        endpoint: str,
        timeout_ms: int = 30000,
        temperature: float = 0,
        max_attempts: int = 3,
        wait_min_ms: int = 1000,
        wait_max_ms: int = 10000,
        cost_tracker=None,
        transport: httpx.AsyncBaseTransport | None = None,
        mock_manager: "MockManager | None" = None,
        rate_limiter=None,  # RateLimiter，可选，控制请求速率
    ):
        self.endpoint = endpoint
        self.timeout_ms = timeout_ms
        self.temperature = temperature
        self.max_attempts = max_attempts
        self.wait_min_ms = wait_min_ms
        self.wait_max_ms = wait_max_ms
        self.cost_tracker = cost_tracker
        self._transport = transport
        self.mock_manager = mock_manager
        self.rate_limiter = rate_limiter

    async def execute(self, case: AgentCase) -> AgentCase:
        """执行多轮对话，填充 result.actual_reply / actual_trace。

        某轮失败：passed=False，error_analysis 记录原因，终止后续轮次。
        case.mock 存在时先应用 Mock（完全 Mock 跳过 HTTP，其余注入 context）。
        """
        start = time.perf_counter()
        # 状态隔离：每个 case 唯一 session_id（文档 4.4）
        session_id = f"eval_{case.case_id}_{uuid.uuid4().hex[:8]}"
        conversation_history: list[dict] = []
        merged_trace: dict = {}

        # Mock：应用记忆/时间/工具 Mock 到 case
        if self.mock_manager:
            case = self.mock_manager.apply_context_mock(case)

        try:
            for i, turn in enumerate(case.conversation):
                # Mock：完全 Mock 时跳过 HTTP 调用
                if (
                    self.mock_manager
                    and self.mock_manager.should_mock_agent(case)
                ):
                    response = self.mock_manager.get_mock_response(case, i)
                else:
                    response = await self._send_with_retry(
                        case, session_id, turn.user, conversation_history
                    )

                reply = str(response.get("reply", ""))
                trace = response.get("trace") or {}
                usage = response.get("usage") or {}

                # 成本追踪（Agent API 返回 usage 时）
                if self.cost_tracker and usage:
                    self.cost_tracker.track(
                        caller="agent",
                        model=self.endpoint,
                        input_tokens=int(usage.get("input_tokens", 0)),
                        output_tokens=int(usage.get("output_tokens", 0)),
                    )

                # 多轮 trace 浅合并（后轮覆盖同名字段）
                merged_trace.update(trace)

                conversation_history.append({"user": turn.user, "assistant": reply})

            case.result.actual_reply = (
                conversation_history[-1]["assistant"] if conversation_history else ""
            )
            case.result.actual_trace = merged_trace
            case.result.execution_time_ms = int((time.perf_counter() - start) * 1000)
            # passed 由评测器判定，此处不设置

        except Exception as e:  # noqa: BLE001 - 超时/重试耗尽/响应异常
            case.result.execution_time_ms = int((time.perf_counter() - start) * 1000)
            case.result.passed = False
            case.result.error_analysis = f"Agent 执行失败: {e}"
            logger.warning(f"case {case.case_id} Agent 执行失败: {e}")

        return case

    async def _send_with_retry(
        self,
        case: AgentCase,
        session_id: str,
        user_message: str,
        conversation_history: list[dict],
    ) -> dict:
        """单轮对话请求，超时/传输错误时重试。"""
        setup = case.setup
        context = {
            "user_profile": setup.user_profile if setup else "",
            "preloaded_memories": setup.preloaded_memories if setup else [],
            "conversation_history": conversation_history,
        }
        # Mock：注入时间/工具 Mock（Agent 系统需支持这些字段）
        if self.mock_manager:
            context.update(self.mock_manager.build_mock_context(case))

        payload = {
            "session_id": session_id,
            "user_message": user_message,
            "context": context,
            "temperature": self.temperature,
        }

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                # 速率限制：每次请求前获取令牌（未配置时不生效）
                if self.rate_limiter:
                    await self.rate_limiter.acquire()

                async with httpx.AsyncClient(
                    timeout=self.timeout_ms / 1000,
                    transport=self._transport,
                ) as client:
                    resp = await client.post(self.endpoint, json=payload)
                    resp.raise_for_status()
                    return resp.json()
            except (httpx.TimeoutException, httpx.TransportError,
                    httpx.HTTPStatusError) as e:
                last_error = e
                if attempt < self.max_attempts:
                    delay = min(
                        self.wait_min_ms * (2 ** (attempt - 1)),
                        self.wait_max_ms,
                    ) / 1000
                    logger.warning(
                        f"case {case.case_id} 第 {attempt} 轮请求失败: {e}，"
                        f"{delay:.1f}s 后重试"
                    )
                    await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]
