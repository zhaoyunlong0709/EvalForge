"""BaselineManager 测试：保存/加载/对比/退化检测。"""
import json

import pytest

from eval_core.models import AgentCase
from eval_core.result import BaselineManager, ResultAggregator
from eval_core.cli.main import _check_baseline_save


def _make_case(case_id: str, passed: bool, priority: str = "P1",
               dimensions: list | None = None) -> AgentCase:
    case = AgentCase.model_validate({
        "case_id": case_id,
        "title": f"测试-{case_id}",
        "version": "v0.1",
        "priority": priority,
        "scenario": "测试",
        "dimensions": dimensions or ["D9.记忆召回"],
        "conversation": [{"turn": 1, "user": "hello"}],
        "evaluation": {"method": "automated", "rules": []},
    })
    case.result.passed = passed
    case.result.score = 5.0 if passed else 0.0
    return case


@pytest.fixture
def baseline_dir(tmp_path):
    return tmp_path / "baselines"


@pytest.fixture
def manager(baseline_dir):
    return BaselineManager(baseline_dir)


class TestSaveAndLoad:
    def test_save_creates_file(self, manager, baseline_dir):
        cases = [_make_case("C001", True), _make_case("C002", False)]
        agg = ResultAggregator.aggregate(cases)

        path = manager.save("v0.1", agg, cases)

        assert path.exists()
        assert path.name == "v0.1.json"

    def test_saved_content(self, manager):
        cases = [_make_case("C001", True), _make_case("C002", False)]
        agg = ResultAggregator.aggregate(cases)

        manager.save("v0.1", agg, cases)
        loaded = manager.load("v0.1")

        assert loaded["version"] == "v0.1"
        assert loaded["summary"]["total"]["count"] == 2
        assert loaded["summary"]["total"]["passed"] == 1
        assert loaded["case_results"]["C001"]["passed"] is True
        assert loaded["case_results"]["C002"]["passed"] is False
        assert loaded["case_results"]["C001"]["score"] == 5.0

    def test_load_latest(self, manager):
        """load_latest 返回最新保存的 baseline。"""
        # 保存两个版本
        cases_v1 = [_make_case("C001", True)]
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)

        cases_v2 = [_make_case("C001", True), _make_case("C002", True)]
        manager.save("v0.2", ResultAggregator.aggregate(cases_v2), cases_v2)

        latest = manager.load_latest()
        assert latest["version"] == "v0.2"

    def test_load_latest_empty(self, manager):
        """无 baseline 时返回 None。"""
        assert manager.load_latest() is None

    def test_load_nonexistent(self, manager):
        assert manager.load("v99.0") is None

    def test_list_versions(self, manager):
        cases = [_make_case("C001", True)]
        agg = ResultAggregator.aggregate(cases)
        manager.save("v0.1", agg, cases)
        manager.save("v0.2", agg, cases)
        manager.save("v0.3", agg, cases)

        assert manager.list_versions() == ["v0.1", "v0.2", "v0.3"]


class TestCompare:
    def test_compare_with_no_baseline(self, manager):
        cases = [_make_case("C001", True)]
        agg = ResultAggregator.aggregate(cases)
        result = manager.compare(agg, cases)
        assert result is None  # 无 baseline，跳过对比

    def test_compare_overall_improved(self, manager):
        """通过率提升 -> improved。"""
        # baseline: 1/2 = 50%
        cases_v1 = [_make_case("C001", True), _make_case("C002", False)]
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)

        # current: 2/2 = 100%
        cases_v2 = [_make_case("C001", True), _make_case("C002", True)]
        agg_v2 = ResultAggregator.aggregate(cases_v2)

        result = manager.compare(agg_v2, cases_v2)
        assert result["overall"]["direction"] == "improved"
        assert result["overall"]["change"] == 0.5

    def test_compare_overall_degraded(self, manager):
        """通过率下降 -> degraded。"""
        cases_v1 = [_make_case("C001", True), _make_case("C002", True)]
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)

        cases_v2 = [_make_case("C001", True), _make_case("C002", False)]
        agg_v2 = ResultAggregator.aggregate(cases_v2)

        result = manager.compare(agg_v2, cases_v2)
        assert result["overall"]["direction"] == "degraded"
        assert result["overall"]["change"] == -0.5

    def test_detect_regressions(self, manager):
        """检测退化 case：baseline 通过但当前失败。"""
        cases_v1 = [
            _make_case("C001", True),
            _make_case("C002", True),
            _make_case("C003", False),
        ]
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)

        # C002 退化了
        cases_v2 = [
            _make_case("C001", True),
            _make_case("C002", False),  # 退化
            _make_case("C003", False),  # 一直失败，不算退化
        ]
        agg_v2 = ResultAggregator.aggregate(cases_v2)

        result = manager.compare(agg_v2, cases_v2)
        regressions = result["regressions"]
        assert len(regressions) == 1
        assert regressions[0]["case_id"] == "C002"
        assert regressions[0]["baseline_passed"] is True
        assert regressions[0]["current_passed"] is False

    def test_detect_fixes(self, manager):
        """检测修复 case：baseline 失败但当前通过。"""
        cases_v1 = [_make_case("C001", True), _make_case("C002", False)]
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)

        cases_v2 = [_make_case("C001", True), _make_case("C002", True)]
        agg_v2 = ResultAggregator.aggregate(cases_v2)

        result = manager.compare(agg_v2, cases_v2)
        fixes = result["fixes"]
        assert len(fixes) == 1
        assert fixes[0]["case_id"] == "C002"

    def test_detect_new_and_removed(self, manager):
        """检测新增和移除的 case。"""
        cases_v1 = [_make_case("C001", True), _make_case("C002", True)]
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)

        # C002 移除，C003 新增
        cases_v2 = [_make_case("C001", True), _make_case("C003", True)]
        agg_v2 = ResultAggregator.aggregate(cases_v2)

        result = manager.compare(agg_v2, cases_v2)
        assert result["new_cases"][0]["case_id"] == "C003"
        assert result["new_cases"][0]["current_passed"] is True
        assert result["removed_cases"][0]["case_id"] == "C002"
        assert result["removed_cases"][0]["baseline_passed"] is True

    def test_compare_by_priority(self, manager):
        """按优先级对比。"""
        cases_v1 = [
            _make_case("C001", True, priority="P0"),
            _make_case("C002", False, priority="P1"),
        ]
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)

        cases_v2 = [
            _make_case("C001", True, priority="P0"),
            _make_case("C002", True, priority="P1"),  # P1 修复了
        ]
        agg_v2 = ResultAggregator.aggregate(cases_v2)

        result = manager.compare(agg_v2, cases_v2)
        assert result["by_priority"]["P1"]["direction"] == "improved"
        assert result["by_priority"]["P0"]["direction"] == "unchanged"

    def test_compare_by_dimension(self, manager):
        """按维度对比。"""
        cases_v1 = [
            _make_case("C001", True, dimensions=["D9.记忆召回"]),
            _make_case("C002", False, dimensions=["D9.记忆召回", "D8.回答准确性"]),
        ]
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)

        cases_v2 = [
            _make_case("C001", True, dimensions=["D9.记忆召回"]),
            _make_case("C002", True, dimensions=["D9.记忆召回", "D8.回答准确性"]),
        ]
        agg_v2 = ResultAggregator.aggregate(cases_v2)

        result = manager.compare(agg_v2, cases_v2)
        assert result["by_dimension"]["D9.记忆召回"]["direction"] == "improved"

    def test_compare_section_new_dimension(self, manager):
        """当前有但 baseline 没有的维度 -> direction=new。"""
        cases_v1 = [_make_case("C001", True, dimensions=["D9.记忆召回"])]
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)

        cases_v2 = [
            _make_case("C001", True, dimensions=["D9.记忆召回"]),
            _make_case("C002", True, dimensions=["D8.回答准确性"]),
        ]
        agg_v2 = ResultAggregator.aggregate(cases_v2)

        result = manager.compare(agg_v2, cases_v2)
        assert result["by_dimension"]["D8.回答准确性"]["direction"] == "new"

    def test_compare_all_pass_unchanged(self, manager):
        """全部不变 -> unchanged。"""
        cases = [_make_case("C001", True), _make_case("C002", True)]
        agg = ResultAggregator.aggregate(cases)
        manager.save("v0.1", agg, cases)

        result = manager.compare(agg, cases)
        assert result["overall"]["direction"] == "unchanged"
        assert result["regressions"] == []
        assert result["fixes"] == []
        assert result["score_changes"] == []

    def test_save_stores_avg_score(self, manager):
        """save 存储整体平均分和维度平均分。"""
        c1 = _make_case("C001", True)
        c1.result.score = 5.0
        c1.result.dimension_scores = {"D9.记忆召回": 5.0}
        c2 = _make_case("C002", True)
        c2.result.score = 3.0
        c2.result.dimension_scores = {"D9.记忆召回": 3.0}
        cases = [c1, c2]

        manager.save("v0.1", ResultAggregator.aggregate(cases), cases)
        loaded = manager.load("v0.1")

        assert loaded["summary"]["avg_score"] == 4.0
        assert loaded["summary"]["dimension_avg_score"]["D9.记忆召回"] == 4.0
        assert loaded["case_results"]["C001"]["dimension_scores"] == {"D9.记忆召回": 5.0}

    def test_compare_avg_score(self, manager):
        """整体平均分对比。"""
        cases_v1 = [_make_case("C001", True), _make_case("C002", True)]
        for c in cases_v1:
            c.result.score = 4.0
            c.result.dimension_scores = {"D9.记忆召回": 4.0}
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)

        cases_v2 = [_make_case("C001", True), _make_case("C002", True)]
        for c in cases_v2:
            c.result.score = 5.0
            c.result.dimension_scores = {"D9.记忆召回": 5.0}
        agg_v2 = ResultAggregator.aggregate(cases_v2)

        result = manager.compare(agg_v2, cases_v2)
        assert result["overall"]["baseline_avg_score"] == 4.0
        assert result["overall"]["current_avg_score"] == 5.0
        assert result["overall"]["avg_score_change"] == 1.0
        assert result["overall"]["avg_score_direction"] == "improved"

    def test_compare_dimension_avg_score(self, manager):
        """按维度平均分对比。"""
        cases_v1 = [_make_case("C001", True)]
        cases_v1[0].result.dimension_scores = {"D9.记忆召回": 3.0}
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)

        cases_v2 = [_make_case("C001", True)]
        cases_v2[0].result.dimension_scores = {"D9.记忆召回": 5.0}
        agg_v2 = ResultAggregator.aggregate(cases_v2)

        result = manager.compare(agg_v2, cases_v2)
        assert result["dimension_avg_score"]["D9.记忆召回"]["baseline"] == 3.0
        assert result["dimension_avg_score"]["D9.记忆召回"]["current"] == 5.0
        assert result["dimension_avg_score"]["D9.记忆召回"]["direction"] == "improved"

    def test_compare_old_baseline_no_avg_score(self, manager):
        """旧 baseline（无 avg_score/dimension_avg_score）优雅降级。"""
        cases_v1 = [_make_case("C001", True)]
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)
        # 模拟旧格式：删除新增字段
        old = manager.load("v0.1")
        del old["summary"]["avg_score"]
        del old["summary"]["dimension_avg_score"]
        for c in old["case_results"].values():
            c.pop("dimension_scores", None)
        import json as _json
        (manager.baselines_dir / "v0.1.json").write_text(
            _json.dumps(old, ensure_ascii=False), encoding="utf-8")

        cases_v2 = [_make_case("C001", True)]
        agg_v2 = ResultAggregator.aggregate(cases_v2)
        result = manager.compare(agg_v2, cases_v2)

        assert result["overall"]["baseline_avg_score"] is None
        assert result["overall"]["avg_score_direction"] == "unknown"
        assert result["dimension_avg_score"] == {}

    def test_detect_score_changes(self, manager):
        """检测分数变化 case：通过状态不变但分数变化。"""
        cases_v1 = [_make_case("C001", True), _make_case("C002", True)]
        cases_v1[0].result.score = 4.0
        cases_v1[1].result.score = 5.0
        manager.save("v0.1", ResultAggregator.aggregate(cases_v1), cases_v1)

        # C001 分数 4 -> 5（仍通过）；C002 不变
        cases_v2 = [_make_case("C001", True), _make_case("C002", True)]
        cases_v2[0].result.score = 5.0
        cases_v2[1].result.score = 5.0
        agg_v2 = ResultAggregator.aggregate(cases_v2)

        result = manager.compare(agg_v2, cases_v2)
        changes = result["score_changes"]
        assert len(changes) == 1
        assert changes[0]["case_id"] == "C001"
        assert changes[0]["baseline_score"] == 4.0
        assert changes[0]["current_score"] == 5.0
        assert changes[0]["change"] == 1.0


class TestBaselineSaveGuard:
    """保存 baseline 前的退化防护检查。"""

    def test_no_comparison_allows_save(self):
        """无 baseline 对比数据（首次保存）→ 允许保存。"""
        assert _check_baseline_save(None) is None

    def test_no_degradation_allows_save(self):
        """无退化 → 允许保存。"""
        comparison = {
            "overall": {"direction": "improved",
                        "baseline_pass_rate": 0.9, "current_pass_rate": 0.95},
            "regressions": [],
        }
        assert _check_baseline_save(comparison) is None

    def test_overall_degraded_blocks(self):
        """整体通过率下降 → 阻塞。"""
        comparison = {
            "overall": {"direction": "degraded",
                        "baseline_pass_rate": 0.95, "current_pass_rate": 0.85},
            "regressions": [],
        }
        reason = _check_baseline_save(comparison)
        assert "整体通过率下降" in reason
        assert "95.0%" in reason and "85.0%" in reason

    def test_p0_failure_blocks(self):
        """当前存在 P0 失败 → 阻塞（安全红线，与 baseline 无关）。"""
        comparison = {
            "overall": {"direction": "improved",
                        "baseline_pass_rate": 0.85, "current_pass_rate": 0.95},
            "regressions": [],
        }
        aggregated = {
            "failures": [{"case_id": "B018", "priority": "P0"}],
        }
        reason = _check_baseline_save(comparison, aggregated)
        assert "P0 用例失败" in reason
        assert "B018" in reason

    def test_p1_failure_blocks(self):
        """当前存在 P1 失败 → 阻塞（基本功能，与 baseline 无关）。"""
        aggregated = {
            "failures": [{"case_id": "B019", "priority": "P1"}],
        }
        reason = _check_baseline_save(None, aggregated)
        assert "P1 用例失败" in reason
        assert "B019" in reason

    def test_non_critical_regression_blocks(self):
        """非 P0/P1（P2/P3）退化 → 阻塞。"""
        comparison = {
            "overall": {"direction": "unchanged",
                        "baseline_pass_rate": 0.95, "current_pass_rate": 0.95},
            "regressions": [
                {"case_id": "B020", "priority": "P2"},
            ],
        }
        reason = _check_baseline_save(comparison)
        assert "非 P0/P1 用例退化" in reason
        assert "B020" in reason
