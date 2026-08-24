"""执行引擎测试：APIExecutor、AgentRunner、Orchestrator（MockTransport 模拟）。"""
import json

import httpx
import pytest

from eval_core.evaluators import EvaluatorPipeline
from eval_core.evaluators.judge import JudgeClient, JudgeResult
from eval_core.evaluators.judge.evaluator import JudgeEvaluator
from eval_core.evaluators import TemplateEngine
from eval_core.models import AgentCase, APICase
from eval_core.runner import APIExecutor, AgentRunner, Orchestrator
from eval_core.result import CostTracker


# ---------- APIExecutor ----------

class TestAPIExecutor:
    def _transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "/health" in str(request.url):
                return httpx.Response(200, json={"status": "ok"})
            if "/error" in str(request.url):
                return httpx.Response(500, json={"status": "error"})
            raise httpx.ConnectError("connection refused")
        return httpx.MockTransport(handler)

    async def test_success_formats_response(self, api_case):
        executor = APIExecutor(max_attempts=1, transport=self._transport())
        result = await executor.execute(api_case)
        parsed = json.loads(result.result.actual_reply)
        assert parsed["status_code"] == 200
        assert parsed["body"]["status"] == "ok"
        assert isinstance(parsed["latency_ms"], int)
        assert result.result.passed is False  # passed 由评测器判定

    async def test_error_status_still_formatted(self, api_case):
        api_case.request.url = "https://api.example.com/error"
        executor = APIExecutor(max_attempts=1, transport=self._transport())
        result = await executor.execute(api_case)
        parsed = json.loads(result.result.actual_reply)
        assert parsed["status_code"] == 500  # 5xx 是有效响应，不标记失败

    async def test_transport_failure(self, api_case):
        api_case.request.url = "https://api.example.com/unreachable"
        executor = APIExecutor(max_attempts=1, transport=self._transport())
        result = await executor.execute(api_case)
        assert result.result.passed is False
        assert "API 请求失败" in result.result.error_analysis

    async def test_variable_replacement(self, api_case, monkeypatch):
        """{{VAR}} 从环境变量替换。"""
        monkeypatch.setenv("API_TOKEN", "secret-token")
        captured = {}
        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={})
        executor = APIExecutor(
            max_attempts=1, transport=httpx.MockTransport(handler)
        )
        await executor.execute(api_case)
        assert captured["auth"] == "Bearer secret-token"

    async def test_undefined_variable_kept(self, api_case, monkeypatch):
        """未定义的环境变量保持原样。"""
        monkeypatch.delenv("API_TOKEN", raising=False)
        captured = {}
        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={})
        executor = APIExecutor(max_attempts=1, transport=httpx.MockTransport(handler))
        await executor.execute(api_case)
        assert captured["auth"] == "Bearer {{API_TOKEN}}"


# ---------- AgentRunner ----------

class TestAgentRunner:
    def _transport(self):
        """模拟两轮 Agent：第 1 轮记忆写入，第 2 轮记忆召回。"""
        requests = []
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            requests.append(body)
            n = len([r for r in requests if r["session_id"] == body["session_id"]])
            if n == 1:
                return httpx.Response(200, json={
                    "reply": "好的，记下了。",
                    "trace": {"memory_write_event": True},
                    "usage": {"input_tokens": 50, "output_tokens": 20},
                })
            return httpx.Response(200, json={
                "reply": "建议温热牛奶。",
                "trace": {"memory_retrieval_query": "饮品"},
                "usage": {"input_tokens": 80, "output_tokens": 30},
            })
        return httpx.MockTransport(handler), requests

    async def test_multi_turn_trace_merge(self, agent_case_data):
        data = dict(agent_case_data)
        data["profile"] = ""
        data["conversation"] = [
            {"turn": 1, "user": "记住我不喝咖啡"},
            {"turn": 2, "user": "推荐饮品"},
        ]
        case = AgentCase.model_validate(data)
        transport, _ = self._transport()
        runner = AgentRunner("https://agent.example.com", max_attempts=1, transport=transport)
        result = await runner.execute(case)
        # 浅合并：第 1 轮 + 第 2 轮的 trace 都在
        assert result.result.actual_trace["memory_write_event"] is True
        assert result.result.actual_trace["memory_retrieval_query"] == "饮品"
        # actual_reply 是最后一轮
        assert result.result.actual_reply == "建议温热牛奶。"

    async def test_session_id_isolation(self, agent_case_data):
        data = dict(agent_case_data)
        data["profile"] = ""
        data["conversation"] = [
            {"turn": 1, "user": "a"}, {"turn": 2, "user": "b"},
        ]
        case = AgentCase.model_validate(data)
        transport, requests = self._transport()
        runner = AgentRunner("https://agent.example.com", max_attempts=1, transport=transport)
        await runner.execute(case)
        sids = {r["session_id"] for r in requests}
        assert len(sids) == 1
        assert next(iter(sids)).startswith("eval_C001_")

    async def test_history_passed_between_turns(self, agent_case_data):
        data = dict(agent_case_data)
        data["profile"] = ""
        data["conversation"] = [
            {"turn": 1, "user": "a"}, {"turn": 2, "user": "b"},
        ]
        case = AgentCase.model_validate(data)
        transport, requests = self._transport()
        runner = AgentRunner("https://agent.example.com", max_attempts=1, transport=transport)
        await runner.execute(case)
        assert requests[1]["context"]["conversation_history"][0]["user"] == "a"
        assert requests[1]["context"]["conversation_history"][0]["assistant"] == "好的，记下了。"

    async def test_setup_injected_into_context(self, agent_case_data, profile_dir):
        from eval_core.loader import AgentCaseLoader
        case = AgentCaseLoader(profile_dir).load(dict(agent_case_data))
        transport, requests = self._transport()
        runner = AgentRunner("https://agent.example.com", max_attempts=1, transport=transport)
        await runner.execute(case)
        assert requests[0]["context"]["user_profile"] == "职场白领，最近睡眠质量差"
        assert requests[0]["context"]["preloaded_memories"] == ["用户偏好晚上喝咖啡"]

    async def test_failure_marks_and_stops(self, agent_case_data):
        data = dict(agent_case_data)
        data["profile"] = ""
        data["conversation"] = [
            {"turn": 1, "user": "a"}, {"turn": 2, "user": "b"},
        ]
        case = AgentCase.model_validate(data)
        call_count = []
        def handler(request: httpx.Request) -> httpx.Response:
            call_count.append(1)
            return httpx.Response(500, json={"error": "internal"})
        runner = AgentRunner(
            "https://agent.example.com", max_attempts=1,
            transport=httpx.MockTransport(handler),
        )
        result = await runner.execute(case)
        assert result.result.passed is False
        assert "Agent 执行失败" in result.result.error_analysis
        assert len(call_count) == 1  # 第 1 轮失败即终止

    async def test_cost_tracking(self, agent_case_data):
        data = dict(agent_case_data)
        data["profile"] = ""
        data["conversation"] = [{"turn": 1, "user": "a"}]
        case = AgentCase.model_validate(data)
        transport, _ = self._transport()
        tracker = CostTracker({"https://agent.example.com": {"input": 1.0, "output": 2.0}})
        runner = AgentRunner(
            "https://agent.example.com", max_attempts=1,
            cost_tracker=tracker, transport=transport,
        )
        await runner.execute(case)
        assert tracker.summary()["total_calls"] == 1
        assert tracker.agent_cost > 0


# ---------- Orchestrator ----------

class TestOrchestrator:
    async def test_dispatch_and_evaluate(self, api_case, agent_case, profile_dir):
        """端到端：API 走 RuleEvaluator，Agent 走 Judge。"""
        api_case.result.actual_reply = json.dumps({
            "status_code": 200, "body": {"status": "ok"}, "latency_ms": 10,
        })
        agent_case.result.actual_reply = "回复"

        class MockJudge(JudgeClient):
            def __init__(self):
                pass  # 跳过父类 __init__（不需要真实 openai 客户端）

            async def score(self, prompt, expected_dimensions=None):
                return JudgeResult(score=5, reason="好", dimension_scores={}, model="m")

        templates = {"共情评估": "【历史】{conversation_history}【回复】{actual_reply}【参考】{golden_reference}【情绪】{user_emotion}"}
        pipeline = EvaluatorPipeline(
            judge_evaluator=JudgeEvaluator(TemplateEngine(templates), MockJudge())
        )
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"reply": "r", "trace": {}})
        )
        orchestrator = Orchestrator(
            api_executor=APIExecutor(max_attempts=1, transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json={"status": "ok"})
            )),
            agent_runner=AgentRunner("https://x", max_attempts=1, transport=transport),
            evaluator_pipeline=pipeline,
        )
        # 预填 result 模拟执行完成，直接验证评测路由
        results = await orchestrator.run([api_case, agent_case])
        # api_case 被重新执行（transport 200 ok） -> passed=True
        # agent_case 被重新执行 -> mock reply -> Judge=5 -> passed=True
        assert all(c.result.passed for c in results)
