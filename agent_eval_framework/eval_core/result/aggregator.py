"""ResultAggregator：按维度/优先级/标签聚合评测结果。

对应《Agent评测框架设计方案》4.7 报告生成模块。
聚合输出供 ConsoleReporter / JSONReporter 使用。
样本量规则（对应《Agent评测集格式规范》第五节）由报告层处理，本层提供原始计数。
"""
from eval_core.models import AgentCase, EvaluableCase


class ResultAggregator:
    """结果聚合器"""

    @staticmethod
    def aggregate(cases: list[EvaluableCase], load_errors: list[dict] | None = None) -> dict:
        """聚合所有 case 的评测结果。

        Args:
            cases: 已执行评测的 case 列表
            load_errors: 加载阶段失败的记录（{"file", "error"}），计入 skipped
        """
        total = len(cases)
        passed = sum(1 for c in cases if c.result.passed)
        failed = total - passed

        result = {
            "total": {
                "count": total,
                "passed": passed,
                "failed": failed,
                "pass_rate": round(passed / total, 4) if total else 0.0,
            },
            "by_priority": ResultAggregator._by_priority(cases),
            "by_dimension": ResultAggregator._by_dimension(cases),
            "by_tag": ResultAggregator._by_tag(cases),
            "failures": ResultAggregator._failures(cases),
            "skipped": [
                {"file": e.get("file", ""), "error": e.get("error", "")}
                for e in (load_errors or [])
            ],
        }
        return result

    @staticmethod
    def _by_priority(cases: list[EvaluableCase]) -> dict:
        stats: dict[str, dict] = {}
        for c in cases:
            p = c.priority.value
            stats.setdefault(p, {"count": 0, "passed": 0})
            stats[p]["count"] += 1
            if c.result.passed:
                stats[p]["passed"] += 1
        for p, s in stats.items():
            s["pass_rate"] = round(s["passed"] / s["count"], 4) if s["count"] else 0.0
        return dict(sorted(stats.items()))

    @staticmethod
    def _by_dimension(cases: list[EvaluableCase]) -> dict:
        """按维度聚合（仅 AgentCase 有 dimensions）。

        维度判定：case.passed 为 True 则其所有维度均计通过；
        passed 为 False 时该维度计失败。附加 trace_errors 不影响 passed，
        但维度通过率只反映主判定结果。
        """
        stats: dict[str, dict] = {}
        for c in cases:
            if not isinstance(c, AgentCase):
                continue
            for dim in c.dimensions:
                stats.setdefault(dim, {"count": 0, "passed": 0})
                stats[dim]["count"] += 1
                if c.result.passed:
                    stats[dim]["passed"] += 1
        for d, s in stats.items():
            s["pass_rate"] = round(s["passed"] / s["count"], 4) if s["count"] else 0.0
        return dict(sorted(stats.items()))

    @staticmethod
    def _by_tag(cases: list[EvaluableCase]) -> dict:
        stats: dict[str, dict] = {}
        for c in cases:
            for tag in c.tags:
                stats.setdefault(tag, {"count": 0, "passed": 0})
                stats[tag]["count"] += 1
                if c.result.passed:
                    stats[tag]["passed"] += 1
        for t, s in stats.items():
            s["pass_rate"] = round(s["passed"] / s["count"], 4) if s["count"] else 0.0
        return dict(sorted(stats.items()))

    @staticmethod
    def _failures(cases: list[EvaluableCase]) -> list[dict]:
        """失败 case 明细（含归因信息）"""
        failures = []
        for c in cases:
            if c.result.passed:
                continue
            entry = {
                "case_id": c.case_id,
                "title": c.title,
                "priority": c.priority.value,
                "error_analysis": c.result.error_analysis,
                "score": c.result.score,
                "judge_reason": c.result.judge_reason,
            }
            if isinstance(c, AgentCase) and c.result.trace_errors:
                entry["trace_errors"] = c.result.trace_errors
            if isinstance(c, AgentCase) and c.result.trace_gaps:
                entry["trace_gaps"] = c.result.trace_gaps
            failures.append(entry)
        return failures
