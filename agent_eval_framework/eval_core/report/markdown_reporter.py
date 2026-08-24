"""MarkdownReporter：生成 Markdown 报告（给 PM 和老板看）。

对应《Agent评测报告模板.md》的结构。包含：
  1. 结论（30 秒读完）
  2. 按优先级（P0 是否全过）
  3. 按维度
  4. 失败 case 明细
  5. Baseline 对比（如果传入）
  6. 附录：评测配置 + 可复现性声明
"""
from datetime import datetime
from pathlib import Path
from typing import Any

from eval_core.models import EvaluableCase


class MarkdownReporter:
    """Markdown 报告生成器"""

    def generate(
        self,
        cases: list[EvaluableCase],
        aggregated: dict,
        baseline_comparison: dict | None = None,
        cost_summary: dict | None = None,
        snapshot: dict | None = None,
        output_path: Path | None = None,
    ) -> str:
        """生成 Markdown 报告文本，可选写入文件。

        Args:
            cases: 已执行的 case 列表
            aggregated: ResultAggregator.aggregate() 的输出
            baseline_comparison: BaselineManager.compare() 的输出（可选）
            cost_summary: CostTracker.summary() 的输出（可选）
            snapshot: DependencySnapshotRecorder.record() 的输出（可选）
            output_path: 输出文件路径（None 时只返回文本）
        """
        lines: list[str] = []
        w = lines.append

        self._header(w, aggregated)
        self._conclusion(w, aggregated, baseline_comparison)
        self._by_priority(w, aggregated)
        self._by_dimension(w, aggregated)
        self._failures(w, aggregated)
        self._baseline(w, baseline_comparison)
        self._appendix(w, cost_summary, snapshot)

        text = "\n".join(lines)
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(text, encoding="utf-8")
        return text

    # ==================== 各章节 ====================

    def _header(self, w, aggregated: dict) -> None:
        t = aggregated["total"]
        w(f"# Agent 评测报告")
        w("")
        w(f"> 评测时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        w(f"> 总用例: {t['count']} | 通过: {t['passed']} | 失败: {t['failed']}"
          f" | 通过率: **{t['pass_rate']:.1%}**")
        w("")

    def _conclusion(self, w, aggregated: dict,
                    baseline: dict | None) -> None:
        t = aggregated["total"]
        by_pri = aggregated.get("by_priority", {})

        p0 = by_pri.get("P0", {})
        p0_all_pass = p0 and p0["passed"] == p0["count"]
        has_failures = t["failed"] > 0

        if not has_failures:
            verdict = "✅ **全部通过，建议发版**"
        elif p0_all_pass:
            verdict = f"⚠️ **P0 全过，{t['failed']} 个非 P0 失败，建议发版（记录已知问题）**"
        else:
            verdict = "❌ **P0 存在失败，阻塞发版**"

        w("## 一、结论")
        w("")
        w(f"{verdict}")
        w("")

        # P0 详情
        if p0:
            mark = "✅" if p0_all_pass else "❌"
            w(f"- P0 阻塞发版指标: {mark} {p0['passed']}/{p0['count']}")

        skipped = aggregated.get("skipped", [])
        if skipped:
            w(f"- 加载失败跳过: {len(skipped)} 个")
        w("")

    def _by_priority(self, w, aggregated: dict) -> None:
        by_pri = aggregated.get("by_priority", {})
        if not by_pri:
            return
        w("## 二、按优先级")
        w("")
        w("| 优先级 | 用例数 | 通过 | 通过率 | 状态 |")
        w("|--------|--------|------|--------|------|")
        for p, s in by_pri.items():
            mark = "✅" if s["passed"] == s["count"] else (
                "⚠️" if s["passed"] > 0 else "❌")
            w(f"| {p} | {s['count']} | {s['passed']} | "
              f"{s['pass_rate']:.1%} | {mark} |")
        w("")

    def _by_dimension(self, w, aggregated: dict) -> None:
        by_dim = aggregated.get("by_dimension", {})
        if not by_dim:
            return
        w("## 三、按维度")
        w("")
        w("| 维度 | 用例数 | 通过 | 通过率 | 状态 |")
        w("|------|--------|------|--------|------|")
        for d, s in by_dim.items():
            mark = "✅" if s["pass_rate"] >= 0.9 else "⚠️"
            w(f"| {d} | {s['count']} | {s['passed']} | "
              f"{s['pass_rate']:.1%} | {mark} |")
        w("")

    def _failures(self, w, aggregated: dict) -> None:
        failures = aggregated.get("failures", [])
        if not failures:
            return
        w("## 四、失败 case 明细")
        w("")
        w("| case_id | 标题 | 优先级 | 失败原因 |")
        w("|---------|------|--------|----------|")
        for f in failures:
            reason = (f.get("error_analysis") or f.get("judge_reason") or "")[:80]
            w(f"| {f['case_id']} | {f['title']} | {f['priority']} | {reason} |")
        w("")

    def _baseline(self, w, baseline: dict | None) -> None:
        if not baseline:
            return
        w("## 五、Baseline 对比")
        w("")
        overall = baseline.get("overall", {})
        w(f"对比版本: **{baseline.get('baseline_version', '?')}** -> 当前")
        w("")

        if overall:
            direction = overall.get("direction", "")
            mark = {"improved": "✅", "degraded": "⚠️",
                    "unchanged": "➡️"}.get(direction, "")
            w(f"- 整体通过率: {overall.get('baseline_pass_rate', 0):.1%} -> "
              f"{overall.get('current_pass_rate', 0):.1%} "
              f"({overall.get('change', 0):+.1%}) {mark}")

        regressions = baseline.get("regressions", [])
        if regressions:
            w(f"- ⚠️ **退化 case ({len(regressions)} 个)**:")
            w("")
            w("| case_id | 标题 | 失败原因 |")
            w("|---------|------|----------|")
            for r in regressions:
                w(f"| {r['case_id']} | {r['title']} | {r.get('error', '')[:60]} |")

        fixes = baseline.get("fixes", [])
        if fixes:
            w(f"- ✅ 修复 case ({len(fixes)} 个): "
              + ", ".join(f["case_id"] for f in fixes))

        new_cases = baseline.get("new_cases", [])
        if new_cases:
            w(f"- 新增 case ({len(new_cases)} 个): {', '.join(new_cases[:10])}")

        removed = baseline.get("removed_cases", [])
        if removed:
            w(f"- 移除 case ({len(removed)} 个): {', '.join(removed[:10])}")
        w("")

    def _appendix(self, w, cost_summary: dict | None,
                  snapshot: dict | None) -> None:
        w("## 附录：评测配置")
        w("")

        if snapshot:
            agent = snapshot.get("agent", {})
            judge = snapshot.get("judge", {})
            seeds = snapshot.get("seeds", {})
            w("| 配置项 | 值 |")
            w("|--------|-----|")
            w(f"| Agent 端点 | {agent.get('endpoint', '')} |")
            w(f"| Agent 温度 | {agent.get('temperature', '')} |")
            w(f"| Judge 模型 | {judge.get('model', '')} |")
            w(f"| Judge 温度 | {judge.get('temperature', '')} |")
            if seeds:
                w(f"| Agent 种子 | {seeds.get('agent_seed', '未设置')} |")
                w(f"| Judge 种子 | {seeds.get('judge_seed', '未设置')} |")
            w(f"| 用例数 | {snapshot.get('case_count', '')} |")
            w("")

        if cost_summary:
            w("### 成本")
            w("")
            w("| 调用方 | 请求数 | 输入 token | 输出 token |")
            w("|--------|--------|-----------|-----------|")
            for caller, s in cost_summary.items():
                w(f"| {caller} | {s.get('requests', 0)} | "
                  f"{s.get('input_tokens', 0)} | {s.get('output_tokens', 0)} |")
            w("")

        w("---")
        w("")
        w("> 本报告由 EvalForge 自动生成。结果在相同配置下可复现。")
