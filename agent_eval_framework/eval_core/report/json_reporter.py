"""JSONReporter：结构化 JSON 输出。

对应《Agent评测框架设计方案》4.7 报告生成模块。
输出完整评测结果（含每个 case 的详细数据）到 reports/ 目录。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from eval_core.models import EvaluableCase


class JSONReporter:
    """JSON 报告输出"""

    def generate(
        self,
        cases: list[EvaluableCase],
        aggregated: dict,
        cost_summary: dict | None = None,
        snapshot: dict | None = None,
        output_path: Path | None = None,
    ) -> str:
        """生成完整 JSON 报告。output_path 为空时只返回字符串。"""
        report = {
            "report_type": "eval_forge_report",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": aggregated,
            "cost": cost_summary,
            "reproducibility_snapshot": snapshot,
            "cases": [self._case_detail(c) for c in cases],
        }
        text = json.dumps(report, ensure_ascii=False, indent=2, default=str)

        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(text, encoding="utf-8")

        return text

    @staticmethod
    def _case_detail(case: EvaluableCase) -> dict:
        """单个 case 的完整明细。"""
        detail = {
            "case_id": case.case_id,
            "case_type": case.case_type,
            "title": case.title,
            "version": case.version,
            "priority": case.priority.value,
            "scenario": case.scenario,
            "tags": case.tags,
        }
        if hasattr(case, "dimensions"):
            detail["dimensions"] = case.dimensions
        detail["result"] = case.result.model_dump()
        return detail
