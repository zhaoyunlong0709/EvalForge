"""评测器测试：RuleEvaluator、TemplateEngine、ResponseParser、TraceEvaluator、Pipeline。"""
import json
import pytest

from eval_core.evaluators import EvaluatorPipeline, RuleEvaluator, TemplateEngine, TraceEvaluator
from eval_core.evaluators.judge import JudgeResponseError, parse_judge_response
from eval_core.evaluators.judge.evaluator import JudgeEvaluator
from eval_core.evaluators.judge.judge_client import JudgeClient, JudgeResult
from eval_core.evaluators.trace_evaluator import evaluate_trace_assertion
from eval_core.models import AgentCase, APICase


# ---------- 辅助 ----------

class MockJudgeClient(JudgeClient):
    """Mock Judge，按预设分数返回。"""
    def __init__(self, score=4.5, reason="mock reason", error=None):
        self.score_val, self.reason_val, self.error_val = score, reason, error

    async def score(self, prompt, expected_dimensions=None):
        if self.error_val:
            return JudgeResult(error=self.error_val)
        return JudgeResult(
            score=self.score_val, reason=self.reason_val,
            dimension_scores={"D9.上下文记忆": self.score_val},
            model="mock", input_tokens=100, output_tokens=50,
        )


def _fill_api_result(case: APICase, status_code=200, body=None, latency_ms=150):
    case.result.actual_reply = json.dumps({
        "status_code": status_code, "headers": {},
        "body": body if body is not None else {"status": "ok"},
        "latency_ms": latency_ms,
    }, ensure_ascii=False)
    return case


def _agent_llm_judge_case(agent_case: AgentCase) -> AgentCase:
    c = agent_case.model_copy(deep=True)
    c.result.actual_reply = "测试回复"
    return c


# ---------- RuleEvaluator ----------

class TestRuleEvaluator:
    async def test_api_case_pass(self, api_case):
        _fill_api_result(api_case)
        result = await RuleEvaluator().evaluate(api_case)
        assert result.result.passed is True
        assert result.result.error_analysis == ""

    async def test_api_case_fail(self, api_case):
        _fill_api_result(api_case, status_code=500, body={"status": "error"}, latency_ms=2000)
        result = await RuleEvaluator().evaluate(api_case)
        assert result.result.passed is False
        assert "status_code" in result.result.error_analysis
        assert "latency_ms" in result.result.error_analysis

    async def test_agent_case_trace_source(self, agent_case_data):
        """AgentCase 的 rules 从 actual_trace 提取数据。"""
        data = dict(agent_case_data)
        data["profile"] = ""
        data["evaluation"] = {
            "method": "automated",
            "rules": [
                {"field": "tool_calls[0].name", "operator": "eq", "value": "create_reminder"},
                {"field": "tool_calls[0].status", "operator": "eq", "value": "success"},
            ],
        }
        case = AgentCase.model_validate(data)
        case.result.actual_reply = "好的，已设置提醒。"
        case.result.actual_trace = {"tool_calls": [
            {"name": "create_reminder", "status": "success"},
        ]}
        result = await RuleEvaluator().evaluate(case)
        assert result.result.passed is True

    async def test_no_rules_noop(self, api_case):
        api_case.evaluation.rules = []
        result = await RuleEvaluator().evaluate(api_case)
        assert result.result.passed is False  # 未判定，保持默认

    async def test_additional_role_appends_error(self, agent_case_data):
        """附加评测器（llm_judge+rules）失败时追加而非覆盖 error_analysis。"""
        data = dict(agent_case_data)
        data["profile"] = ""
        data["evaluation"]["rules"] = [
            {"field": "memory_write_event", "operator": "eq", "value": True},
        ]
        case = AgentCase.model_validate(data)
        case.result.actual_reply = "回复"
        case.result.passed = False
        case.result.error_analysis = "Judge 评分 2 < 阈值 4"
        case.result.actual_trace = {"memory_write_event": False}

        result = await RuleEvaluator().evaluate(case)
        assert result.result.passed is False
        assert "Judge 评分" in result.result.error_analysis  # 原 analysis 保留
        assert "断言失败" in result.result.error_analysis    # 新 failure 追加


# ---------- TemplateEngine ----------

class TestTemplateEngine:
    TEMPLATES = {
        "测试模板": (
            "【对话历史】\n{conversation_history}\n"
            "【回复】\n{actual_reply}\n"
            "【参考】\n{golden_reference}\n"
            "【要求】{memory_requirement}\n【禁止】{forbidden_content}"
        ),
    }

    def _engine(self):
        return TemplateEngine(self.TEMPLATES)

    def test_build_prompt(self, agent_case):
        agent_case.evaluation.template = "测试模板"
        agent_case.evaluation.params = {
            "memory_requirement": "晚上别喝咖啡",
            "forbidden_content": ["咖啡", "拿铁"],
        }
        agent_case.result.actual_reply = "推荐牛奶"
        prompt = self._engine().build_prompt(agent_case)
        assert "第1轮 用户：我今天又被老板否了方案" in prompt
        assert "推荐牛奶" in prompt
        assert "无参考答案" in prompt
        assert "晚上别喝咖啡" in prompt
        assert "咖啡、拿铁" in prompt  # 列表顿号连接

    def test_golden_reference_used(self, agent_case):
        agent_case.evaluation.template = "测试模板"
        agent_case.evaluation.params = {"memory_requirement": "x", "forbidden_content": []}
        agent_case.result.actual_reply = "回复"
        agent_case.golden = type(agent_case).model_fields["golden"].annotation.__args__[0](
            expected_answer="标准答案内容",
        )
        prompt = self._engine().build_prompt(agent_case)
        assert "标准答案内容" in prompt

    def test_unknown_template(self, agent_case):
        agent_case.evaluation.template = "不存在的模板"
        with pytest.raises(Exception, match="未定义"):
            self._engine().build_prompt(agent_case)

    def test_missing_variable(self, agent_case):
        """模板变量未在 params 中提供时报错。"""
        agent_case.evaluation.template = "测试模板"
        agent_case.evaluation.params = {}  # 缺 memory_requirement
        agent_case.result.actual_reply = "x"
        with pytest.raises(Exception, match="memory_requirement"):
            self._engine().build_prompt(agent_case)


# ---------- ResponseParser ----------

class TestParseJudgeResponse:
    def test_normal(self):
        r = parse_judge_response(
            '{"score": 4, "reason": "好", "dimension_scores": {"D8.x": 4}}'
        )
        assert r["score"] == 4.0
        assert r["dimension_scores"] == {"D8.x": 4.0}

    def test_markdown_block(self):
        r = parse_judge_response(
            '好的，评分：\n```json\n{"score": 5, "reason": "r", "dimension_scores": {}}\n```'
        )
        assert r["score"] == 5.0

    def test_dirty_text(self):
        r = parse_judge_response('说明 {"score": 3, "reason": "r", "dimension_scores": {}} 结尾')
        assert r["score"] == 3.0

    @pytest.mark.parametrize("raw", [
        "不是 JSON",
        '{"score": 8, "reason": "r", "dimension_scores": {}}',   # score 越界
        '{"score": -1, "reason": "r", "dimension_scores": {}}',  # score 越界
        '{"reason": "r", "dimension_scores": {}}',               # score 缺失
        '{"score": "abc", "reason": "r", "dimension_scores": {}}',  # score 非数字
        '{"score": 4, "reason": "r"}',                            # dimension_scores 缺失
    ])
    def test_invalid_rejected(self, raw):
        with pytest.raises(JudgeResponseError):
            parse_judge_response(raw)

    def test_missing_dimensions_is_warning(self):
        """个别维度缺失仅警告（missing_dimensions 字段），不抛异常。"""
        r = parse_judge_response(
            '{"score": 4, "reason": "r", "dimension_scores": {"D8.x": 4}}',
            expected_dimensions=["D8.x", "D9.y"],
        )
        assert r["missing_dimensions"] == ["D9.y"]


# ---------- TraceEvaluator ----------

class TestEvaluateTraceAssertion:
    def test_bool(self):
        assert evaluate_trace_assertion(True, True)
        assert not evaluate_trace_assertion(False, True)

    def test_gte_lte(self):
        assert evaluate_trace_assertion(3, ">=1")
        assert not evaluate_trace_assertion(0, ">=1")
        assert evaluate_trace_assertion(100, "<=200")

    def test_contains(self):
        assert evaluate_trace_assertion("用户偏好饮品", "contains:饮品")
        assert not evaluate_trace_assertion("x", "contains:y")

    def test_not_null(self):
        assert evaluate_trace_assertion("x", "not_null")
        assert not evaluate_trace_assertion(None, "not_null")

    def test_exact_match(self):
        assert evaluate_trace_assertion("success", "success")


class TestTraceEvaluator:
    async def test_assertions_and_gaps(self, agent_case_data):
        data = dict(agent_case_data)
        data["profile"] = ""
        data["trace"] = {
            "assertions": [
                {"field": "memory_write_event", "expected": True},
                {"field": "memory_write_status", "expected": "success"},
            ],
            "missing_fields": ["memory_policy_decision"],
        }
        case = AgentCase.model_validate(data)
        case.result.actual_trace = {
            "memory_write_event": False,   # 断言失败
            "memory_write_status": "success",  # 断言通过
            # memory_policy_decision 缺失 -> 盲区
        }
        case.result.passed = True
        result = await TraceEvaluator().evaluate(case)
        assert len(result.result.trace_errors) == 1
        assert result.result.trace_errors[0]["field"] == "memory_write_event"
        assert result.result.trace_gaps == ["memory_policy_decision"]
        assert result.result.passed is True  # trace 不影响 passed

    async def test_skips_non_agent(self, api_case):
        result = await TraceEvaluator().evaluate(api_case)
        assert result is api_case


# ---------- EvaluatorPipeline ----------

class TestEvaluatorPipeline:
    def _pipeline(self, judge_score=4.5):
        templates = {"共情评估": "【历史】{conversation_history}\n【回复】{actual_reply}\n【参考】{golden_reference}\n【情绪】{user_emotion}"}
        engine = TemplateEngine(templates)
        return EvaluatorPipeline(judge_evaluator=JudgeEvaluator(engine, MockJudgeClient(judge_score)))

    async def test_llm_judge_main_path(self, agent_case):
        case = _agent_llm_judge_case(agent_case)
        result = await self._pipeline().evaluate(case)
        assert result.result.passed is True
        assert result.result.score == 4.5
        assert result.result.evaluated_at != ""

    async def test_llm_judge_below_threshold(self, agent_case):
        case = _agent_llm_judge_case(agent_case)
        result = await self._pipeline(judge_score=2).evaluate(case)
        assert result.result.passed is False
        assert "阈值" in result.result.error_analysis

    async def test_automated_main_path(self, api_case):
        _fill_api_result(api_case)
        result = await self._pipeline().evaluate(api_case)
        assert result.result.passed is True

    async def test_execution_failure_skips_eval(self, agent_case):
        case = agent_case.model_copy(deep=True)
        case.result.error_analysis = "Agent 请求超时"
        result = await self._pipeline().evaluate(case)
        assert result.result.passed is False  # 防御性兜底
        assert result.result.score == 0.0     # 未调 Judge

    async def test_manual_no_auto_judge(self, agent_case_data):
        data = dict(agent_case_data)
        data["profile"] = ""
        data["evaluation"] = {"method": "manual"}
        case = AgentCase.model_validate(data)
        case.result.actual_reply = "回复"
        result = await self._pipeline().evaluate(case)
        assert result.result.passed is False
        assert result.result.score == 0.0

    async def test_judge_not_configured(self, agent_case):
        case = _agent_llm_judge_case(agent_case)
        result = await EvaluatorPipeline(judge_evaluator=None).evaluate(case)
        assert result.result.passed is False
        assert "Judge 未配置" in result.result.error_analysis

    async def test_llm_judge_with_additional_rules(self, agent_case_data):
        """Judge 通过但附加 rules 失败 -> 拉低 passed。"""
        data = dict(agent_case_data)
        data["profile"] = ""
        data["evaluation"]["rules"] = [
            {"field": "memory_write_event", "operator": "eq", "value": True},
        ]
        case = AgentCase.model_validate(data)
        case.result.actual_reply = "回复"
        case.result.actual_trace = {"memory_write_event": False}
        result = await self._pipeline(judge_score=5).evaluate(case)
        assert result.result.passed is False
        assert result.result.score == 5.0
        assert "断言失败" in result.result.error_analysis
