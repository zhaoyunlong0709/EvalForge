"""APIExecutor：发送 HTTP 请求 -> 格式化响应 -> 填充 result。

对应《Agent评测框架设计方案》4.4 执行引擎模块。
能力：变量替换（{{VAR}} 环境变量）、重定向跟随、超时/重试（tenacity）。
Mock 能力 ⚠️ [Phase 3] 通过 HTTPMock 拦截器实现。

响应格式化：将 HTTP 响应序列化为结构化 JSON 字符串写入
result.actual_reply，供 RuleEvaluator 断言（status_code / body.* / latency_ms）。
"""
import asyncio
import json
import os
import re
import time

import httpx

from eval_core.models import APICase
from eval_core.utils import logger

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def resolve_variables(value: str) -> str:
    """将 {{VAR}} 替换为环境变量值。未定义的变量保持原样（便于发现配置缺失）。"""
    def repl(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))
    return _VAR_RE.sub(repl, value)


class APIExecutor:
    """API 请求执行器"""

    def __init__(
        self,
        max_attempts: int = 3,
        wait_min_ms: int = 1000,
        wait_max_ms: int = 10000,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        """transport 参数供测试注入 MockTransport，生产环境留空。"""
        self.max_attempts = max_attempts
        self.wait_min_ms = wait_min_ms
        self.wait_max_ms = wait_max_ms
        self._transport = transport

    async def execute(self, case: APICase) -> APICase:
        """执行 HTTP 请求并填充 result。失败时标记 passed=False。"""
        start = time.perf_counter()
        try:
            response, latency_ms = await self._send_with_retry(case)
            case.result.execution_time_ms = int((time.perf_counter() - start) * 1000)
            case.result.actual_reply = self._format_response(response, latency_ms)
            # passed 由 RuleEvaluator 判定，此处不设置
        except Exception as e:  # noqa: BLE001 - 超时/重试耗尽/连接失败
            elapsed = int((time.perf_counter() - start) * 1000)
            case.result.execution_time_ms = elapsed
            case.result.passed = False
            case.result.error_analysis = f"API 请求失败: {e}"
            logger.warning(f"case {case.case_id} API 请求失败: {e}")
        return case

    async def _send_with_retry(self, case: APICase) -> tuple[httpx.Response, int]:
        """带重试的请求发送，返回 (响应, 请求耗时 ms)。

        超时/传输错误重试；HTTP 4xx/5xx 是有效响应不重试。
        """
        req = case.request
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=req.timeout_ms / 1000,
                    follow_redirects=req.follow_redirects,
                    transport=self._transport,
                ) as client:
                    t0 = time.perf_counter()
                    response = await client.request(
                        method=req.method,
                        url=resolve_variables(req.url),
                        headers={k: resolve_variables(v) for k, v in req.headers.items()},
                        params={k: resolve_variables(v) for k, v in req.query_params.items()},
                        json=req.body if isinstance(req.body, dict) else None,
                        content=req.body if isinstance(req.body, str) else None,
                    )
                    await response.aread()  # 确保响应体完整读取
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    return response, latency_ms
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_error = e
                if attempt < self.max_attempts:
                    # 指数退避
                    delay = min(
                        self.wait_min_ms * (2 ** (attempt - 1)),
                        self.wait_max_ms,
                    ) / 1000
                    logger.warning(
                        f"case {case.case_id} 第 {attempt} 次请求失败: {e}，"
                        f"{delay:.1f}s 后重试"
                    )
                    await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    @staticmethod
    def _format_response(response: httpx.Response, latency_ms: int) -> str:
        """将 HTTP 响应格式化为结构化 JSON 字符串。

        结构：{"status_code", "headers", "body", "latency_ms"}
        """
        try:
            body: object = response.json()
        except Exception:  # noqa: BLE001 - 非 JSON 响应体原样返回文本
            body = response.text

        return json.dumps(
            {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body,
                "latency_ms": latency_ms,
            },
            ensure_ascii=False,
            default=str,
        )
