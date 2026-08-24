"""重试策略封装，基于 tenacity。

对应《Agent评测框架设计方案》技术栈表"重试"层。
两种策略：
- api_retry_strategy: HTTP 请求重试（指数退避），用于 APIExecutor / AgentRunner
- fixed_retry_strategy: 固定间隔重试，用于 JudgeEvaluator（返回格式异常时重发相同 prompt）
"""
from tenacity import retry, stop_after_attempt, wait_exponential, wait_fixed


def api_retry_strategy(
    max_attempts: int = 3,
    wait_min_ms: int = 1000,
    wait_max_ms: int = 10000,
):
    """HTTP 请求重试策略（指数退避）。

    参数默认值对应 config/global.yaml 的 api_retry 段。
    用法：
        @api_retry_strategy()
        async def request(): ...
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(
            multiplier=1,
            min=wait_min_ms / 1000,
            max=wait_max_ms / 1000,
        ),
        reraise=True,
    )


def fixed_retry_strategy(
    max_attempts: int = 3,
    wait_ms: int = 1000,
):
    """固定间隔重试策略。

    参数默认值对应 config/global.yaml 的 judge.retry_count / retry_delay_ms。
    用于 JudgeEvaluator：返回非 JSON / score 越界 / 维度缺失时，重发相同 prompt。
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_fixed(wait_ms / 1000),
        reraise=True,
    )
