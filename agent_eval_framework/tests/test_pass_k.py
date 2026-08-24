"""PassK 模式测试：Orchestrator 的 --pass-k 多数表决功能。

重点验证：
  1. 普通模式（pass_k=1）不受影响，pass_k_detail 为空
  2. PassK 模式正确统计 k 次结果并多数表决
  3. flaky case（时好时坏）的判定逻辑
"""
import json

import httpx
import pytest

from eval_core.models import AgentCase
from eval_core.runner.agent_runner import AgentRunner
from eval_core.runner.orchestrator import Orchestrator
from eval_core.evaluators.pipeline import EvaluatorPipeline
from eval_core.evaluators.rule_evaluator import RuleEvaluator


def _make_agent_case() -> AgentCase:
    return AgentCase.model_validate({
        "case_id": "C_PK",
        "title": "PassK 测试",
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


class FlakyTransport(httpx.AsyncBaseTransport):
    """模拟 flaky Agent：按调用次数交替返回成功/失败。
    reply 是 JSON 字符串，RuleEvaluator 解析后取 $.status 字段。"""

    def __init__(self, pattern: list[str]):
        """pattern: 每次调用的 status 值列表，如 ["ok", "bad", "ok"]"""
        self.pattern = pattern
        self.call_count = 0

    async def handle_async_request(self, request):
        status = self.pattern[self.call_count % len(self.pattern)]
        self.call_count += 1
        reply = json.dumps({"status": status})
        return httpx.Response(200, json={"reply": reply, "trace": {}})


def _make_orchestrator(transport, pass_k=1, pass_threshold=1):
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
        pass_k=pass_k,
        pass_threshold=pass_threshold,
    )


class TestNormalMode:
    """普通模式（pass_k=1）不受影响。"""

    async def test_pass_k_detail_empty(self):
        case = _make_agent_case()
        transport = FlakyTransport(["ok"])
        orch = _make_orchestrator(transport, pass_k=1)

        results = await orch.run([case])
        assert results[0].result.passed is True
        assert results[0].result.pass_k_detail == {}  # 普通模式不填充

    async def test_failure_normal_mode(self):
        case = _make_agent_case()
        transport = FlakyTransport(["bad"])
        orch = _make_orchestrator(transport, pass_k=1)

        results = await orch.run([case])
        assert results[0].result.passed is False
        assert results[0].result.pass_k_detail == {}


class TestPassKMode:
    async def test_majority_pass(self):
        """3 过 2 -> 阈值 2 -> 通过。"""
        case = _make_agent_case()
        transport = FlakyTransport(["ok", "bad", "ok"])  # 3 次中 2 次 ok
        orch = _make_orchestrator(transport, pass_k=3, pass_threshold=2)

        results = await orch.run([case])
        r = results[0].result
        assert r.passed is True
        assert r.pass_k_detail["k"] == 3
        assert r.pass_k_detail["pass_count"] == 2
        assert r.pass_k_detail["pass_threshold"] == 2
        assert r.pass_k_detail["individual_results"] == [True, False, True]

    async def test_majority_fail(self):
        """3 过 1 -> 阈值 2 -> 不通过。"""
        case = _make_agent_case()
        transport = FlakyTransport(["bad", "bad", "ok"])  # 3 次中 1 次 ok
        orch = _make_orchestrator(transport, pass_k=3, pass_threshold=2)

        results = await orch.run([case])
        r = results[0].result
        assert r.passed is False
        assert r.pass_k_detail["pass_count"] == 1
        assert "未达阈值" in r.error_analysis

    async def test_all_pass(self):
        """5 过 5 -> 通过。"""
        case = _make_agent_case()
        transport = FlakyTransport(["ok"])
        orch = _make_orchestrator(transport, pass_k=5, pass_threshold=3)

        results = await orch.run([case])
        r = results[0].result
        assert r.passed is True
        assert r.pass_k_detail["pass_count"] == 5

    async def test_all_fail(self):
        """5 过 0 -> 不通过。"""
        case = _make_agent_case()
        transport = FlakyTransport(["bad"])
        orch = _make_orchestrator(transport, pass_k=5, pass_threshold=3)

        results = await orch.run([case])
        r = results[0].result
        assert r.passed is False
        assert r.pass_k_detail["pass_count"] == 0

    async def test_threshold_one_means_any_pass(self):
        """阈值 1 = 只要有一次过就算过。"""
        case = _make_agent_case()
        transport = FlakyTransport(["bad", "bad", "ok"])  # 只 1 次过
        orch = _make_orchestrator(transport, pass_k=3, pass_threshold=1)

        results = await orch.run([case])
        assert results[0].result.passed is True


class TestPassKConfig:
    def test_pass_k_minimum_is_1(self):
        """pass_k=0 或负数 -> 自动纠正为 1（普通模式）。"""
        case = _make_agent_case()
        transport = FlakyTransport(["ok"])
        pipeline = EvaluatorPipeline(rule_evaluator=RuleEvaluator())
        orch = Orchestrator(
            api_executor=None,
            agent_runner=AgentRunner("http://x", max_attempts=1, transport=transport),
            evaluator_pipeline=pipeline,
            pass_k=0,
        )
        assert orch.pass_k == 1

    def test_threshold_capped_at_k(self):
        """threshold > k -> 自动截断为 k。"""
        pipeline = EvaluatorPipeline(rule_evaluator=RuleEvaluator())
        orch = Orchestrator(
            api_executor=None,
            agent_runner=AgentRunner("http://x"),
            evaluator_pipeline=pipeline,
            pass_k=3,
            pass_threshold=10,  # 大于 k
        )
        assert orch.pass_threshold == 3
