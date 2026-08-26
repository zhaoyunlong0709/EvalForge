"""报告生成测试：ConsoleReporter、JSONReporter、ReportBuilder。"""
import json

from eval_core.models import APICase
from eval_core.report import ConsoleReporter, JSONReporter, ReportBuilder
from eval_core.result import ResultAggregator


def _sample_cases():
    c1 = APICase(
        case_id="A1", title="健康检查", version="v0.1", priority="P0", scenario="s",
        tags=["smoke"],
        request={"method": "GET", "url": "x"},
        evaluation={"method": "automated", "rules": []},
    )
    c1.result.passed = True

    c2 = APICase(
        case_id="A2", title="订单查询", version="v0.1", priority="P1", scenario="s",
        request={"method": "GET", "url": "y"},
        evaluation={"method": "automated", "rules": []},
    )
    c2.result.passed = False
    c2.result.error_analysis = "断言失败: status_code eq 200, 实际: 500"
    return [c1, c2]


class TestConsoleReporter:
    def test_generate(self):
        cases = _sample_cases()
        agg = ResultAggregator.aggregate(cases)
        text = ConsoleReporter().generate(cases, agg, cost_summary=None, snapshot=None)
        assert "EvalForge 评测报告" in text
        assert "2 个用例" in text
        assert "1 通过" in text
        assert "失败用例" in text
        assert "A2" in text

    def test_with_cost_and_snapshot(self):
        cases = _sample_cases()
        agg = ResultAggregator.aggregate(cases)
        text = ConsoleReporter().generate(
            cases, agg,
            cost_summary={"total_cost_usd": 0.001, "judge_cost_usd": 0.001,
                          "agent_cost_usd": 0.0, "total_calls": 2},
            snapshot={"agent": {"endpoint": "https://a"}, "judge": {"model": "m"}},
        )
        assert "成本" in text
        assert "可复现性快照" in text


class TestJSONReporter:
    def test_generate_to_file(self, tmp_path):
        cases = _sample_cases()
        agg = ResultAggregator.aggregate(cases)
        out = tmp_path / "sub" / "report.json"
        JSONReporter().generate(cases, agg, output_path=out)
        report = json.loads(out.read_text())
        assert report["report_type"] == "eval_forge_report"
        assert len(report["cases"]) == 2
        assert report["cases"][0]["case_id"] == "A1"
        assert report["cases"][0]["result"]["passed"] is True
        assert report["summary"]["total"]["count"] == 2

    def test_generate_returns_string(self):
        cases = _sample_cases()
        agg = ResultAggregator.aggregate(cases)
        text = JSONReporter().generate(cases, agg)
        assert json.loads(text)["report_type"] == "eval_forge_report"


class TestReportBuilder:
    def test_generate_all(self, tmp_path, capsys):
        cases = _sample_cases()
        agg = ResultAggregator.aggregate(cases)
        builder = ReportBuilder(tmp_path)
        json_path = builder.generate_all(cases, agg)
        assert json_path.exists()
        assert json_path.suffix == ".json"
        # Console 输出到 stdout
        captured = capsys.readouterr()
        assert "EvalForge 评测报告" in captured.out
