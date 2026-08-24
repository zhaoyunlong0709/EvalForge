"""并发控制 + 速率限制测试。

重点验证：
  1. RateLimiter 令牌桶：正常获取、桶满突发、桶空等待
  2. Orchestrator 并发模式：结果保序、Semaphore 限流
  3. 并发模式与串行模式结果一致
  4. AgentRunner 集成 RateLimiter
"""
import asyncio
import json
import time

import httpx
import pytest

from eval_core.models import AgentCase
from eval_core.runner.agent_runner import AgentRunner
from eval_core.runner.orchestrator import Orchestrator
from eval_core.evaluators.pipeline import EvaluatorPipeline
from eval_core.evaluators.rule_evaluator import RuleEvaluator
from eval_core.utils import RateLimiter


# ==================== RateLimiter 测试 ====================

class TestRateLimiter:
    async def test_acquire_within_burst(self):
        """burst 内的请求立即通过，不等待。"""
        limiter = RateLimiter(rate=100, burst=5)
        start = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1  # 5 个请求都在 burst 内，不应该等待

    async def test_acquire_waits_when_empty(self):
        """桶空后需要等待令牌补充。"""
        limiter = RateLimiter(rate=10, burst=1)
        # 先消耗掉 burst 的 1 个令牌
        await limiter.acquire()

        # 第 2 个请求需要等 ~0.1s（rate=10/s -> 0.1s/个）
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05  # 至少等了一半的补充时间

    async def test_from_rpm(self):
        """from_rpm(60) = rate=1/s。"""
        limiter = RateLimiter.from_rpm(60)
        assert limiter.rate == pytest.approx(1.0)
        assert limiter.burst == 1

    def test_invalid_rate_raises(self):
        with pytest.raises(ValueError):
            RateLimiter(rate=0)
        with pytest.raises(ValueError):
            RateLimiter(rate=-1)

    async def test_concurrent_acquire(self):
        """并发获取令牌不崩溃，总数正确。"""
        limiter = RateLimiter(rate=1000, burst=10)

        async def worker():
            await limiter.acquire()
            return 1

        results = await asyncio.gather(*[worker() for _ in range(20)])
        assert sum(results) == 20


# ==================== Orchestrator 并发测试 ====================

def _make_case(case_id: str) -> AgentCase:
    return AgentCase.model_validate({
        "case_id": case_id,
        "title": f"并发测试-{case_id}",
        "version": "v0.1",
        "priority": "P1",
        "scenario": "测试",
        "conversation": [{"turn": 1, "user": "hello"}],
        "evaluation": {
            "method": "automated",
            "rules": [
                {"field": "$.status", "operator": "eq", "value": "ok"},
            ],
        },
    })


class RecordingTransport(httpx.AsyncBaseTransport):
    """记录请求到达顺序的 Transport。"""

    def __init__(self, delay_ms: int = 10):
        self.arrival_order: list[str] = []
        self.max_concurrent = 0
        self._current = 0
        self._delay_ms = delay_ms

    async def handle_async_request(self, request):
        # 从 URL 或 body 中提取 case 信息
        body = json.loads(request.content)
        session_id = body.get("session_id", "")
        self.arrival_order.append(session_id)

        # 追踪并发数
        self._current += 1
        self.max_concurrent = max(self.max_concurrent, self._current)

        await asyncio.sleep(self._delay_ms / 1000)

        self._current -= 1
        return httpx.Response(200, json={"reply": '{"status": "ok"}', "trace": {}})


def _make_orchestrator(transport, concurrency=1):
    runner = AgentRunner(
        "http://agent.example.com",
        max_attempts=1,
        transport=transport,
    )
    pipeline = EvaluatorPipeline(rule_evaluator=RuleEvaluator())
    return Orchestrator(
        api_executor=None,
        agent_runner=runner,
        evaluator_pipeline=pipeline,
        concurrency=concurrency,
    )


class TestOrchestratorConcurrency:
    async def test_serial_mode_default(self):
        """默认 concurrency=1，行为和以前一样。"""
        transport = RecordingTransport(delay_ms=5)
        cases = [_make_case(f"C{i:03d}") for i in range(5)]
        orch = _make_orchestrator(transport, concurrency=1)

        results = await orch.run(cases)

        assert len(results) == 5
        assert all(r.result.passed for r in results)
        assert transport.max_concurrent == 1  # 串行

    async def test_concurrent_mode_runs_parallel(self):
        """concurrency=3 时，最多 3 个请求同时进行。"""
        transport = RecordingTransport(delay_ms=50)
        cases = [_make_case(f"C{i:03d}") for i in range(6)]
        orch = _make_orchestrator(transport, concurrency=3)

        results = await orch.run(cases)

        assert len(results) == 6
        assert all(r.result.passed for r in results)
        assert transport.max_concurrent > 1  # 确实并发了
        assert transport.max_concurrent <= 3  # 不超过限制

    async def test_concurrent_results_preserve_order(self):
        """并发模式下结果顺序与输入一致。"""
        transport = RecordingTransport(delay_ms=10)
        cases = [_make_case(f"C{i:03d}") for i in range(5)]
        orch = _make_orchestrator(transport, concurrency=5)

        results = await orch.run(cases)

        result_ids = [r.case_id for r in results]
        expected_ids = [c.case_id for c in cases]
        assert result_ids == expected_ids

    async def test_concurrent_faster_than_serial(self):
        """并发模式实际比串行快。"""
        n_cases = 10
        delay_ms = 30

        # 串行
        transport_serial = RecordingTransport(delay_ms=delay_ms)
        cases_serial = [_make_case(f"C{i:03d}") for i in range(n_cases)]
        orch_serial = _make_orchestrator(transport_serial, concurrency=1)
        start = time.monotonic()
        await orch_serial.run(cases_serial)
        serial_time = time.monotonic() - start

        # 并发
        transport_conc = RecordingTransport(delay_ms=delay_ms)
        cases_conc = [_make_case(f"C{i:03d}") for i in range(n_cases)]
        orch_conc = _make_orchestrator(transport_conc, concurrency=5)
        start = time.monotonic()
        await orch_conc.run(cases_conc)
        concurrent_time = time.monotonic() - start

        # 并发应该比串行快（理论上 5 倍，保守断言 2 倍）
        assert concurrent_time < serial_time / 2

    async def test_case_exception_does_not_crash_others(self):
        """并发模式下单个 case 异常不影响其他 case。"""

        class FailFirstTransport(httpx.AsyncBaseTransport):
            """第一个请求失败，其余成功。"""

            def __init__(self):
                self.call_count = 0

            async def handle_async_request(self, request):
                self.call_count += 1
                if self.call_count == 1:
                    return httpx.Response(500, json={"error": "server error"})
                return httpx.Response(200, json={"reply": '{"status": "ok"}', "trace": {}})

        transport = FailFirstTransport()
        cases = [_make_case(f"C{i:03d}") for i in range(3)]
        orch = _make_orchestrator(transport, concurrency=3)

        results = await orch.run(cases)

        assert len(results) == 3
        # 至少 2 个通过（失败的那个可能是任何一个，取决于调度顺序）
        passed_count = sum(1 for r in results if r.result.passed)
        assert passed_count >= 2


class TestAgentRunnerRateLimit:
    async def test_rate_limiter_integration(self):
        """AgentRunner 配置 rate_limiter 后请求间隔符合预期。"""
        limiter = RateLimiter(rate=50, burst=1)  # 50/s = 20ms/个
        timestamps: list[float] = []

        class TimingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                timestamps.append(time.monotonic())
                return httpx.Response(200, json={"reply": "ok", "trace": {}})

        runner = AgentRunner(
            "http://agent.example.com",
            max_attempts=1,
            transport=TimingTransport(),
            rate_limiter=limiter,
        )

        case = AgentCase.model_validate({
            "case_id": "C_RL",
            "title": "速率限制测试",
            "version": "v0.1",
            "priority": "P1",
            "scenario": "测试",
            "conversation": [
                {"turn": 1, "user": "a"},
                {"turn": 2, "user": "b"},
                {"turn": 3, "user": "c"},
            ],
            "evaluation": {"method": "automated", "rules": []},
        })

        await runner.execute(case)

        # 3 轮请求，burst=1，第 2/3 个请求应该有间隔
        assert len(timestamps) == 3
        if len(timestamps) >= 2:
            gap = timestamps[1] - timestamps[0]
            # rate=50/s -> 间隔约 20ms，允许误差
            assert gap >= 0.015  # 至少 15ms

    async def test_no_rate_limiter_no_delay(self):
        """不配置 rate_limiter 时不产生额外延迟。"""
        timestamps: list[float] = []

        class TimingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                timestamps.append(time.monotonic())
                return httpx.Response(200, json={"reply": "ok", "trace": {}})

        runner = AgentRunner(
            "http://agent.example.com",
            max_attempts=1,
            transport=TimingTransport(),
        )

        case = AgentCase.model_validate({
            "case_id": "C_NO_RL",
            "title": "无速率限制测试",
            "version": "v0.1",
            "priority": "P1",
            "scenario": "测试",
            "conversation": [
                {"turn": 1, "user": "a"},
                {"turn": 2, "user": "b"},
            ],
            "evaluation": {"method": "automated", "rules": []},
        })

        await runner.execute(case)

        assert len(timestamps) == 2
        # 没有限速，两个请求几乎同时发出
        assert timestamps[1] - timestamps[0] < 0.05
