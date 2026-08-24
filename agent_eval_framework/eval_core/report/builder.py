"""ReportBuilder：报告生成器，统一调度多种 Reporter。

对应《Agent评测框架设计方案》4.7 报告生成模块。
支持 Console + JSON + Markdown 三种格式。
"""
from datetime import datetime
from pathlib import Path

from eval_core.models import EvaluableCase

from .console import ConsoleReporter
from .json_reporter import JSONReporter
from .markdown_reporter import MarkdownReporter


class ReportBuilder:
    """报告生成器"""

    def __init__(self, report_dir: Path):
        self.report_dir = report_dir
        self.console_reporter = ConsoleReporter()
        self.json_reporter = JSONReporter()
        self.markdown_reporter = MarkdownReporter()

    def generate_all(
        self,
        cases: list[EvaluableCase],
        aggregated: dict,
        cost_summary: dict | None = None,
        snapshot: dict | None = None,
        baseline_comparison: dict | None = None,
    ) -> Path:
        """生成 Console + JSON + Markdown 报告。返回 JSON 报告文件路径。"""
        # Console 输出
        console_text = self.console_reporter.generate(
            cases, aggregated, cost_summary, snapshot
        )
        print(console_text)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # JSON 文件
        json_path = self.report_dir / f"report_{timestamp}.json"
        self.json_reporter.generate(
            cases, aggregated, cost_summary, snapshot, output_path=json_path
        )

        # Markdown 文件（给 PM 看，含 baseline 对比）
        md_path = self.report_dir / f"report_{timestamp}.md"
        self.markdown_reporter.generate(
            cases, aggregated,
            baseline_comparison=baseline_comparison,
            cost_summary=cost_summary,
            snapshot=snapshot,
            output_path=md_path,
        )

        return json_path
