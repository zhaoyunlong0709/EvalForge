"""MockManager 测试：完全 Mock、记忆 Mock、时间/工具 Mock 注入。"""
import pytest

from eval_core.mock import MockManager
from eval_core.models import AgentCase, MockConfig, ToolMock, FullAgentMock


@pytest.fixture
def base_case_data():
    return {
        "case_id": "C_TEST",
        "title": "Mock 测试用例",
        "version": "v0.1",
        "priority": "P1",
        "scenario": "测试",
        "dimensions": ["D9.记忆召回"],
        "conversation": [
            {"turn": 1, "user": "你记一下我不喝咖啡"},
            {"turn": 2, "user": "晚点喝什么好"},
        ],
        "evaluation": {"method": "llm_judge", "template": "test"},
    }


class TestShouldMockAgent:
    def test_no_mock_config(self, base_case_data):
        case = AgentCase.model_validate(base_case_data)
        assert MockManager().should_mock_agent(case) is False

    def test_mock_config_without_agent(self, base_case_data):
        base_case_data["mock"] = {"memory": ["测试记忆"]}
        case = AgentCase.model_validate(base_case_data)
        assert MockManager().should_mock_agent(case) is False

    def test_mock_config_with_agent(self, base_case_data):
        base_case_data["mock"] = {
            "agent": {"replies": ["好的", "热牛奶"]}
        }
        case = AgentCase.model_validate(base_case_data)
        assert MockManager().should_mock_agent(case) is True


class TestGetMockResponse:
    def test_returns_predefined_reply_per_turn(self, base_case_data):
        base_case_data["mock"] = {
            "agent": {
                "replies": ["好的，记下了", "建议喝热牛奶"],
                "trace": {"memory_write_event": True},
            }
        }
        case = AgentCase.model_validate(base_case_data)
        mgr = MockManager()

        resp1 = mgr.get_mock_response(case, 0)
        assert resp1["reply"] == "好的，记下了"
        assert resp1["trace"] == {"memory_write_event": True}

        resp2 = mgr.get_mock_response(case, 1)
        assert resp2["reply"] == "建议喝热牛奶"

    def test_insufficient_replies_raises(self, base_case_data):
        base_case_data["mock"] = {
            "agent": {"replies": ["只有一条"]}  # 2 轮对话但只有 1 条回复
        }
        case = AgentCase.model_validate(base_case_data)
        mgr = MockManager()

        with pytest.raises(IndexError, match="无预定义回复"):
            mgr.get_mock_response(case, 1)


class TestApplyContextMock:
    def test_memory_mock_overrides_preloaded(self, base_case_data):
        base_case_data["setup"] = {
            "user_profile": "测试用户",
            "preloaded_memories": ["旧记忆"],
        }
        base_case_data["mock"] = {"memory": ["新记忆A", "新记忆B"]}
        case = AgentCase.model_validate(base_case_data)

        case = MockManager().apply_context_mock(case)
        assert case.setup.preloaded_memories == ["新记忆A", "新记忆B"]

    def test_memory_mock_creates_setup_if_missing(self, base_case_data):
        base_case_data["mock"] = {"memory": ["注入记忆"]}
        case = AgentCase.model_validate(base_case_data)

        case = MockManager().apply_context_mock(case)
        assert case.setup is not None
        assert case.setup.preloaded_memories == ["注入记忆"]

    def test_no_mock_returns_case_unchanged(self, base_case_data):
        case = AgentCase.model_validate(base_case_data)
        result = MockManager().apply_context_mock(case)
        assert result.setup is case.setup

    def test_empty_memory_mock_noop(self, base_case_data):
        base_case_data["mock"] = {"memory": []}
        case = AgentCase.model_validate(base_case_data)
        result = MockManager().apply_context_mock(case)
        assert result.setup is None  # 未创建 setup


class TestBuildMockContext:
    def test_time_mock_in_context(self, base_case_data):
        base_case_data["mock"] = {"time": "2026-07-06T08:00:00"}
        case = AgentCase.model_validate(base_case_data)

        ctx = MockManager().build_mock_context(case)
        assert ctx == {"mock_time": "2026-07-06T08:00:00"}

    def test_tool_mock_in_context(self, base_case_data):
        base_case_data["mock"] = {
            "tools": [
                {
                    "tool_name": "get_weather",
                    "mode": "error",
                    "error": {"code": "location_permission_denied"},
                }
            ]
        }
        case = AgentCase.model_validate(base_case_data)

        ctx = MockManager().build_mock_context(case)
        assert len(ctx["mock_tools"]) == 1
        assert ctx["mock_tools"][0]["tool_name"] == "get_weather"
        assert ctx["mock_tools"][0]["mode"] == "error"

    def test_combined_mock_in_context(self, base_case_data):
        base_case_data["mock"] = {
            "time": "2026-07-06T08:00:00",
            "tools": [
                {"tool_name": "get_weather", "mode": "return", "result": {"temperature": 999}},
            ],
        }
        case = AgentCase.model_validate(base_case_data)

        ctx = MockManager().build_mock_context(case)
        assert "mock_time" in ctx
        assert "mock_tools" in ctx

    def test_no_mock_empty_context(self, base_case_data):
        case = AgentCase.model_validate(base_case_data)
        assert MockManager().build_mock_context(case) == {}


class TestAgentRunnerWithMock:
    """AgentRunner 集成测试：完全 Mock 时不发 HTTP 请求。"""

    async def test_full_mock_skips_http(self, base_case_data):
        from eval_core.runner.agent_runner import AgentRunner

        base_case_data["mock"] = {
            "agent": {
                "replies": ["好的记下了", "建议热牛奶"],
                "trace": {"memory_write_event": True},
            }
        }
        case = AgentCase.model_validate(base_case_data)

        # endpoint 指向不存在的地址，如果发了请求会失败
        runner = AgentRunner(
            "http://localhost:99999/never",
            max_attempts=1,
            mock_manager=MockManager(),
        )
        case = await runner.execute(case)

        assert case.result.actual_reply == "建议热牛奶"
        assert case.result.actual_trace == {"memory_write_event": True}
        # Agent 请求失败时 passed=False，但 mock 下应该没有错误
        assert case.result.error_analysis == ""

    async def test_memory_mock_injected_into_context(self, base_case_data):
        """记忆 Mock 应通过 context 传递给 Agent（真实请求时）。"""
        import json
        import httpx

        from eval_core.runner.agent_runner import AgentRunner

        base_case_data["mock"] = {"memory": ["用户膝盖受伤"]}
        base_case_data["conversation"] = [{"turn": 1, "user": "周末安排"}]
        case = AgentCase.model_validate(base_case_data)

        captured: list[dict] = []

        class FakeTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                captured.append(json.loads(request.content))
                return httpx.Response(200, json={"reply": "ok", "trace": {}})

        runner = AgentRunner(
            "http://agent.example.com",
            max_attempts=1,
            transport=FakeTransport(),
            mock_manager=MockManager(),
        )
        case = await runner.execute(case)

        # Agent 收到的 context 中应包含 mock 注入的记忆
        assert captured[0]["context"]["preloaded_memories"] == ["用户膝盖受伤"]

    async def test_tool_mock_in_context(self, base_case_data):
        """工具 Mock 应通过 context.mock_tools 传递给 Agent。"""
        import json
        import httpx

        from eval_core.runner.agent_runner import AgentRunner

        base_case_data["mock"] = {
            "tools": [
                {"tool_name": "get_weather", "mode": "error", "error": {"code": "denied"}}
            ]
        }
        base_case_data["conversation"] = [{"turn": 1, "user": "今天天气"}]
        case = AgentCase.model_validate(base_case_data)

        captured: list[dict] = []

        class FakeTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                captured.append(json.loads(request.content))
                return httpx.Response(200, json={"reply": "查不到", "trace": {}})

        runner = AgentRunner(
            "http://agent.example.com",
            max_attempts=1,
            transport=FakeTransport(),
            mock_manager=MockManager(),
        )
        case = await runner.execute(case)

        assert captured[0]["context"]["mock_tools"][0]["tool_name"] == "get_weather"
