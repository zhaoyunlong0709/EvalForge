"""Judge 客户端：LLM API 调用 + 重试。

对应《LLM-as-Judge评测执行流程》第 4 步。
重试策略：返回非 JSON / score 越界 / dimension_scores 缺失时，
使用相同 prompt 重试（最多 retry_count 次，固定间隔）。
JudgeEvaluator 基础版（Phase 1）：单 Judge 模型，不做校准、多 Judge。
"""
import asyncio
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from eval_core.utils import logger

from .response_parser import JudgeResponseError, parse_judge_response


@dataclass
class JudgeResult:
    """Judge 评分结果"""
    score: float | None = None
    reason: str = ""
    dimension_scores: dict = field(default_factory=dict)
    error: str | None = None
    # 成本追踪数据（CostTracker 使用）
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class JudgeClient:
    """LLM Judge 客户端"""

    def __init__(
        self,
        model: str,
        temperature: float = 0,
        max_tokens: int = 500,
        retry_count: int = 3,
        retry_delay_ms: int = 1000,
        api_key: str | None = None,
        base_url: str | None = None,
        cost_tracker=None,  # CostTracker，可选，用于成本统计
        rate_limiter=None,  # RateLimiter，可选，控制请求速率
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.retry_count = retry_count
        self.retry_delay_ms = retry_delay_ms
        self.cost_tracker = cost_tracker
        self.rate_limiter = rate_limiter

        # api_key 默认读 OPENAI_API_KEY 环境变量；base_url 支持国产模型代理。
        # 凭证缺失时延迟失败（不阻断启动）：纯规则类评测（L1 冒烟）无需 Judge。
        self._init_error: str | None = None
        self._client = None
        try:
            self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        except Exception as e:  # noqa: BLE001 - 凭证缺失等配置问题
            self._init_error = (
                f"Judge 客户端初始化失败（检查 OPENAI_API_KEY / api_key 配置）: {e}"
            )
            logger.warning(self._init_error)

    async def score(self, prompt: str, expected_dimensions: list[str] | None = None) -> JudgeResult:
        """调用 Judge 对 prompt 打分，含业务级重试。

        重试条件：网络异常 / 非 JSON / score 越界 / dimension_scores 缺失。
        全部重试失败后返回 error 结果（不抛异常，由调用方标记 case 失败）。
        """
        if self._client is None:
            return JudgeResult(error=self._init_error or "Judge 客户端未初始化", model=self.model)

        last_error: Exception | None = None

        for attempt in range(1, self.retry_count + 1):
            try:
                # 速率限制：每次请求前获取令牌（未配置时不生效）
                if self.rate_limiter:
                    await self.rate_limiter.acquire()

                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                raw = response.choices[0].message.content or ""
                usage = getattr(response, "usage", None)

                parsed = parse_judge_response(raw, expected_dimensions)
                if parsed.get("missing_dimensions"):
                    logger.warning(
                        f"Judge 评分缺少期望维度（不影响判定）: {parsed['missing_dimensions']}"
                    )

                input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
                output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

                if self.cost_tracker:
                    self.cost_tracker.track(
                        caller="judge",
                        model=self.model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )

                return JudgeResult(
                    score=parsed["score"],
                    reason=parsed["reason"],
                    dimension_scores=parsed["dimension_scores"],
                    model=self.model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

            except JudgeResponseError as e:
                last_error = e
            except Exception as e:  # noqa: BLE001 - 网络/API 异常同样触发重试
                last_error = e

            if attempt < self.retry_count:
                logger.warning(
                    f"Judge 第 {attempt} 次失败: {last_error}，"
                    f"{self.retry_delay_ms}ms 后重试（相同 prompt）"
                )
                await asyncio.sleep(self.retry_delay_ms / 1000)

        return JudgeResult(
            error=f"Judge 评分失败（重试 {self.retry_count} 次）: {last_error}",
            model=self.model,
        )
