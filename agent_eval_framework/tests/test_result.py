"""结果处理测试：CostTracker、ResultAggregator、DependencySnapshotRecorder。"""
import pytest

from eval_core.models import AgentCase, APICase, CaseResult
from eval_core.result import CostTracker, DependencySnapshotRecorder, ResultAggregator


class TestCostTracker:
    PRICING = {
        "model-a": {"input": 2.5, "output": 10.0},
        "model-b": {"input": 0.15, "output": 0.6},
    }

    def test_track_and_totals(self):
        ct = CostTracker(self.PRICING)
        ct.track("judge", "model-a", 1_000_000, 500_000)  # 2.5 + 5.0
        ct.track("agent", "model-b", 1_000_000, 1_000_000)  # 0.15 + 0.6
        assert ct.judge_cost == pytest.approx(7.5)
        assert ct.agent_cost == pytest.approx(0.75)
        assert ct.total_cost == pytest.approx(8.25)

    def test_unknown_model_zero_cost(self):
        ct = CostTracker(self.PRICING)
        ct.track("judge", "unknown-model", 1000, 1000)
        assert ct.total_cost == 0.0
        assert ct.summary()["unknown_pricing_models"] == ["unknown-model"]

    def test_summary(self):
        ct = CostTracker(self.PRICING)
        ct.track("judge", "model-a", 1000, 1000)
        s = ct.summary()
        assert s["total_calls"] == 1
        assert s["total_cost_usd"] > 0


class TestResultAggregator:
    def _make_cases(self):
        api_pass = APICase(
            case_id="A1", title="t", version="v", priority="P0", scenario="s",
            request={"method": "GET", "url": "x"},
            evaluation={"method": "automated", "rules": []},
        )
        api_pass.result.passed = True

        agent_fail = AgentCase(
            case_id="C1", title="t", version="v", priority="P1", scenario="s",
            dimensions=["D9.上下文记忆"], tags=["regression"],
            conversation=[{"turn": 1, "user": "u"}],
            evaluation={"method": "llm_judge"},
        )
        agent_fail.result.passed = False
        agent_fail.result.error_analysis = "Judge 评分 2 < 阈值 4"

        agent_pass = AgentCase(
            case_id="C2", title="t", version="v", priority="P1", scenario="s",
            dimensions=["D9.上下文记忆", "D8.共情适当率"], tags=["smoke"],
            conversation=[{"turn": 1, "user": "u"}],
            evaluation={"method": "llm_judge"},
        )
        agent_pass.result.passed = True
        return [api_pass, agent_fail, agent_pass]

    def test_totals(self):
        agg = ResultAggregator.aggregate(self._make_cases())
        assert agg["total"]["count"] == 3
        assert agg["total"]["passed"] == 2
        assert agg["total"]["failed"] == 1
        assert agg["total"]["pass_rate"] == pytest.approx(0.6667)

    def test_by_priority(self):
        agg = ResultAggregator.aggregate(self._make_cases())
        assert agg["by_priority"]["P0"] == {"count": 1, "passed": 1, "pass_rate": 1.0}
        assert agg["by_priority"]["P1"]["count"] == 2
        assert agg["by_priority"]["P1"]["passed"] == 1

    def test_by_dimension(self):
        agg = ResultAggregator.aggregate(self._make_cases())
        assert agg["by_dimension"]["D9.上下文记忆"]["count"] == 2
        assert agg["by_dimension"]["D9.上下文记忆"]["passed"] == 1
        assert agg["by_dimension"]["D8.共情适当率"]["count"] == 1

    def test_failures_detail(self):
        agg = ResultAggregator.aggregate(self._make_cases())
        assert len(agg["failures"]) == 1
        assert agg["failures"][0]["case_id"] == "C1"
        assert "阈值" in agg["failures"][0]["error_analysis"]

    def test_skipped_recorded(self):
        agg = ResultAggregator.aggregate([], load_errors=[{"file": "x.json", "error": "bad"}])
        assert agg["skipped"] == [{"file": "x.json", "error": "bad"}]

    def test_empty(self):
        agg = ResultAggregator.aggregate([])
        assert agg["total"]["count"] == 0
        assert agg["total"]["pass_rate"] == 0.0


class TestSnapshot:
    def test_record(self):
        snap = DependencySnapshotRecorder.record(
            agent_config={"endpoint": "https://a", "temperature": 0},
            judge_config={"model": "gpt-4o", "temperature": 0},
            reproducibility_config={"agent_seed": 42, "judge_seed": 42},
            case_count=10,
        )
        assert snap["agent"]["endpoint"] == "https://a"
        assert snap["judge"]["model"] == "gpt-4o"
        assert snap["seeds"]["agent_seed"] == 42
        assert snap["case_count"] == 10
        assert snap["recorded_at"]
