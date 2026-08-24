"""数据模型测试：共享类型、APICase、AgentCase、Union discriminator、extra=forbid。"""
import pytest
from pydantic import TypeAdapter, ValidationError

from eval_core.models import (
    AgentCase, AgentSetup, APICase, CaseResult, EvaluableCase, Priority,
    RuleAssertion,
)


class TestPriority:
    def test_values(self):
        assert {p.value for p in Priority} == {"P0", "P1", "P2", "P3"}

    def test_from_string(self):
        assert Priority("P0") is Priority.P0

    def test_invalid(self):
        with pytest.raises(ValueError):
            Priority("P9")


class TestCaseResult:
    def test_defaults(self):
        r = CaseResult()
        assert r.actual_reply == ""
        assert r.actual_trace == {}
        assert r.score == 0.0
        assert r.passed is False
        assert r.trace_errors == []
        assert r.trace_gaps == []


class TestRuleAssertion:
    def test_operator_literal(self):
        RuleAssertion(field="x", operator="eq", value=1)

    def test_unknown_operator_rejected(self):
        with pytest.raises(ValidationError):
            RuleAssertion(field="x", operator="between", value=1)

    def test_jsonpath_not_in_operators(self):
        """jsonpath 是字段提取方式而非比较操作符（文档 5.4 修正）。"""
        with pytest.raises(ValidationError):
            RuleAssertion(field="x", operator="jsonpath", value=1)


class TestAPICase:
    def test_valid(self, api_case_data):
        case = APICase.model_validate(api_case_data)
        assert case.case_type == "api"
        assert case.priority is Priority.P0
        assert case.request.method == "GET"
        assert len(case.evaluation.rules) == 3
        assert case.result.passed is False

    def test_missing_required(self, api_case_data):
        for field in ["title", "version", "priority", "request", "evaluation"]:
            data = {k: v for k, v in api_case_data.items() if k != field}
            with pytest.raises(ValidationError):
                APICase.model_validate(data)

    def test_extra_field_rejected(self, api_case_data):
        api_case_data["conversation"] = [{"turn": 1, "user": "hi"}]
        with pytest.raises(ValidationError):
            APICase.model_validate(api_case_data)

    def test_invalid_http_method(self, api_case_data):
        api_case_data["request"]["method"] = "FETCH"
        with pytest.raises(ValidationError):
            APICase.model_validate(api_case_data)

    def test_result_field_omitted(self, api_case_data):
        """case 文件不写 result，运行时使用默认值。"""
        case = APICase.model_validate(api_case_data)
        assert case.result == CaseResult()


class TestAgentCase:
    def test_valid_minimal(self, agent_case_data):
        case = AgentCase.model_validate(agent_case_data)
        assert case.case_type == "agent"
        assert len(case.conversation) == 1
        assert case.conversation[0].expect == "先共情，不急于给解决方案"
        assert case.trace is None
        assert case.golden is None
        assert case.mock is None
        assert case.setup is None  # Loader 填充前为 None
        assert case.profile == "test_profile"

    def test_extra_field_rejected(self, agent_case_data):
        agent_case_data["request"] = {"method": "GET", "url": "x"}
        with pytest.raises(ValidationError):
            AgentCase.model_validate(agent_case_data)

    def test_typo_rejected(self, agent_case_data):
        agent_case_data["conversationss"] = []
        with pytest.raises(ValidationError):
            AgentCase.model_validate(agent_case_data)

    def test_invalid_eval_method(self, agent_case_data):
        agent_case_data["evaluation"]["method"] = "unknown"
        with pytest.raises(ValidationError):
            AgentCase.model_validate(agent_case_data)

    def test_trace_and_golden_sections(self, agent_case_data):
        agent_case_data["trace"] = {
            "assertions": [{"field": "memory_write_event", "expected": True}],
            "missing_fields": ["memory_policy_decision"],
        }
        agent_case_data["golden"] = {
            "annotator": "allen",
            "expected_answer": "不推荐咖啡",
            "overall_score": 0,
            "dimension_scores": {"D9.上下文记忆": 0},
            "failure": {
                "severity": "P1", "category": "记忆召回失败", "root_cause": "未触发写入",
            },
        }
        case = AgentCase.model_validate(agent_case_data)
        assert case.trace.assertions[0].field == "memory_write_event"
        assert case.golden.failure.severity == "P1"

    def test_golden_without_failure(self, agent_case_data):
        """正面 case 无 failure 字段。"""
        agent_case_data["golden"] = {
            "expected_answer": "先共情",
            "overall_score": 5,
        }
        case = AgentCase.model_validate(agent_case_data)
        assert case.golden.failure is None

    def test_setup_populated_by_loader(self, agent_case_data):
        """setup 由 Loader 填充，模型层面允许。"""
        agent_case_data["setup"] = {
            "user_profile": "测试用户",
            "preloaded_memories": ["m1"],
        }
        case = AgentCase.model_validate(agent_case_data)
        assert case.setup.user_profile == "测试用户"


class TestUnionDiscriminator:
    """EvaluableCase = Annotated[APICase | AgentCase, discriminator="case_type"]"""

    adapter = TypeAdapter(EvaluableCase)

    def test_routes_to_api(self, api_case_data):
        result = self.adapter.validate_python(api_case_data)
        assert isinstance(result, APICase)

    def test_routes_to_agent(self, agent_case_data):
        result = self.adapter.validate_python(agent_case_data)
        assert isinstance(result, AgentCase)

    def test_unknown_case_type_rejected(self, api_case_data):
        api_case_data["case_type"] = "webhook"
        with pytest.raises(ValidationError):
            self.adapter.validate_python(api_case_data)

    def test_missing_case_type_rejected(self, api_case_data):
        del api_case_data["case_type"]
        with pytest.raises(ValidationError):
            self.adapter.validate_python(api_case_data)
