"""MarkdownReporter 测试：报告结构、结论判定、baseline 对比。"""
import json

import pytest

from eval_core.models import AgentCase, APICase
from eval_core.report import MarkdownReporter
from eval_core.result import ResultAggregator


def _make_case(case_id: str, passed: bool, priority: str = "P1",
               dimensions: list | None = None, error: str = "") -> AgentCase:
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
    case.result.error_analysis = error
    return case


class TestMarkdownReporter:
    def test_generate_returns_text(self):
        cases = [_make_case("C001", True)]
        agg = ResultAggregator.aggregate(cases)
        text = MarkdownReporter().generate(cases, agg)
        assert "# Agent 评测报告" in text
        assert "100.0%" in text

    def test_generate_to_file(self, tmp_path):
        cases = [_make_case("C001", True)]
        agg = ResultAggregator.aggregate(cases)
        output = tmp_path / "report.md"
        MarkdownReporter().generate(cases, agg, output_path=output)
        assert output.exists()
        assert "Agent 评测报告" in output.read_text(encoding="utf-8")

    def test_all_pass_conclusion(self):
        cases = [_make_case("C001", True), _make_case("C002", True)]
        agg = ResultAggregator.aggregate(cases)
        text = MarkdownReporter().generate(cases, agg)
        assert "建议发版" in text

    def test_p0_fail_blocks_release(self):
        cases = [
            _make_case("C001", False, priority="P0", error="Agent 超时"),
            _make_case("C002", True),
        ]
        agg = ResultAggregator.aggregate(cases)
        text = MarkdownReporter().generate(cases, agg)
        assert "阻塞发版" in text
        assert "P0 阻塞发版指标" in text

    def test_p1_fail_blocks_release(self):
        cases = [
            _make_case("C001", True, priority="P0"),
            _make_case("C002", False, priority="P1", error="共情不足"),
        ]
        agg = ResultAggregator.aggregate(cases)
        text = MarkdownReporter().generate(cases, agg)
        assert "阻塞发版" in text
        assert "P1 阻塞发版指标" in text

    def test_critical_pass_non_critical_fail(self):
        cases = [
            _make_case("C001", True, priority="P0"),
            _make_case("C002", True, priority="P1"),
            _make_case("C003", False, priority="P2", error="个性化不足"),
        ]
        agg = ResultAggregator.aggregate(cases)
        text = MarkdownReporter().generate(cases, agg)
        assert "建议发版" in text
        assert "P0/P1 全过" in text

    def test_failure_table(self):
        cases = [
            _make_case("C001", True),
            _make_case("C002", False, error="记忆召回失败"),
        ]
        agg = ResultAggregator.aggregate(cases)
        text = MarkdownReporter().generate(cases, agg)
        assert "C002" in text
        assert "记忆召回失败" in text

    def test_baseline_section(self):
        cases = [_make_case("C001", True)]
        agg = ResultAggregator.aggregate(cases)
        baseline = {
            "baseline_version": "v0.2",
            "overall": {
                "baseline_pass_rate": 0.90,
                "current_pass_rate": 1.0,
                "change": 0.10,
                "direction": "improved",
            },
            "regressions": [],
            "fixes": [],
            "new_cases": [],
            "removed_cases": [],
        }
        text = MarkdownReporter().generate(cases, agg, baseline_comparison=baseline)
        assert "Baseline 对比" in text
        assert "v0.2" in text
        assert "improved" in text or "✅" in text

    def test_baseline_regression(self):
        cases = [_make_case("C001", True)]
        agg = ResultAggregator.aggregate(cases)
        baseline = {
            "baseline_version": "v0.2",
            "overall": {
                "baseline_pass_rate": 1.0,
                "current_pass_rate": 1.0,
                "change": 0.0,
                "direction": "unchanged",
            },
            "regressions": [
                {"case_id": "C005", "title": "记忆冲突", "error": "用了旧记忆"}
            ],
            "fixes": [],
            "new_cases": [],
            "removed_cases": [],
        }
        text = MarkdownReporter().generate(cases, agg, baseline_comparison=baseline)
        assert "退化" in text
        assert "C005" in text

    def test_baseline_section_shows_first_run_hint_when_none(self):
        """无基线时，报告仍含 Baseline 对比章节，并提示首次评测。"""
        cases = [_make_case("C001", True)]
        agg = ResultAggregator.aggregate(cases)
        text = MarkdownReporter().generate(cases, agg, baseline_comparison=None)
        assert "Baseline 对比" in text
        assert "首次评测" in text

    def test_conclusion_contains_baseline_summary(self):
        """结论里包含基线对比一句话摘要。"""
        cases = [_make_case("C001", True)]
        agg = ResultAggregator.aggregate(cases)
        baseline = {
            "baseline_version": "v0.2",
            "overall": {
                "baseline_pass_rate": 0.90,
                "current_pass_rate": 1.0,
                "change": 0.10,
                "direction": "improved",
            },
            "regressions": [],
            "fixes": [],
            "new_cases": [],
            "removed_cases": [],
        }
        text = MarkdownReporter().generate(cases, agg, baseline_comparison=baseline)
        assert "基线对比 (vs v0.2)" in text
        assert "90.0%" in text and "100.0%" in text

    def test_conclusion_first_run_summary(self):
        """无基线时结论里提示首次评测。"""
        cases = [_make_case("C001", True)]
        agg = ResultAggregator.aggregate(cases)
        text = MarkdownReporter().generate(cases, agg, baseline_comparison=None)
        assert "基线对比: 首次评测" in text

    def test_baseline_total_and_detail_sections(self):
        """完整 baseline 对比展示：总分比对 + 详细比对 + case 级变化。"""
        cases = [_make_case("C001", True)]
        agg = ResultAggregator.aggregate(cases)
        baseline = {
            "baseline_version": "v0.2",
            "overall": {
                "baseline_pass_rate": 0.90,
                "current_pass_rate": 1.0,
                "change": 0.10,
                "direction": "improved",
                "baseline_avg_score": 4.1,
                "current_avg_score": 4.6,
                "avg_score_change": 0.5,
                "avg_score_direction": "improved",
            },
            "by_priority": {
                "P0": {"baseline": 1.0, "current": 1.0, "change": 0.0, "direction": "unchanged"},
                "P1": {"baseline": 0.8, "current": 1.0, "change": 0.2, "direction": "improved"},
            },
            "by_dimension": {
                "D9.记忆召回": {"baseline": 0.9, "current": 1.0, "change": 0.1, "direction": "improved"},
            },
            "dimension_avg_score": {
                "D9.记忆召回": {"baseline": 4.0, "current": 4.5, "change": 0.5, "direction": "improved"},
            },
            "regressions": [],
            "fixes": [],
            "score_changes": [
                {"case_id": "C001", "title": "测试-C001", "baseline_score": 4.0,
                 "current_score": 5.0, "change": 1.0},
            ],
            "new_cases": [],
            "removed_cases": [],
        }
        text = MarkdownReporter().generate(cases, agg, baseline_comparison=baseline)
        assert "5.1 总分比对" in text
        assert "平均分" in text
        assert "5.2 详细比对" in text
        assert "按维度（通过率）" in text
        assert "按维度（平均分）" in text
        assert "5.3 case 级变化" in text
        assert "分数变化 case" in text
        assert "4.00" in text and "5.00" in text

    def test_snapshot_in_appendix(self):
        cases = [_make_case("C001", True)]
        agg = ResultAggregator.aggregate(cases)
        snapshot = {
            "agent": {"endpoint": "http://agent.example.com", "temperature": 0},
            "judge": {"model": "gpt-4o", "temperature": 0},
                        "case_count": 1,
        }
        text = MarkdownReporter().generate(cases, agg, snapshot=snapshot)
        assert "gpt-4o" in text
        assert "评测配置" in text
