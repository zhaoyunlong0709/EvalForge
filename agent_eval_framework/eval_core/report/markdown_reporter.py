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

        # P0（安全红线）与 P1（基本功能）都是阻塞发版点
        p0 = by_pri.get("P0", {})
        p1 = by_pri.get("P1", {})
        p0_all_pass = bool(p0) and p0["passed"] == p0["count"]
        p1_all_pass = bool(p1) and p1["passed"] == p1["count"]
        critical_failed = (
            (bool(p0) and p0["passed"] < p0["count"])
            or (bool(p1) and p1["passed"] < p1["count"])
        )
        has_failures = t["failed"] > 0

        if not has_failures:
            verdict = "✅ **全部通过，建议发版**"
        elif critical_failed:
            verdict = "❌ **P0/P1 存在失败，阻塞发版**"
        else:
            verdict = f"⚠️ **P0/P1 全过，{t['failed']} 个非关键失败，建议发版（记录已知问题）**"

        w("## 一、结论")
        w("")
        w(f"{verdict}")
        w("")

        # P0 / P1 阻塞指标
        if p0:
            mark = "✅" if p0_all_pass else "❌"
            w(f"- P0 阻塞发版指标: {mark} {p0['passed']}/{p0['count']}")
        if p1:
            mark = "✅" if p1_all_pass else "❌"
            w(f"- P1 阻塞发版指标: {mark} {p1['passed']}/{p1['count']}")

        # 基线对比摘要（每次报告必含）
        w(self._baseline_summary_line(baseline))

        skipped = aggregated.get("skipped", [])
        if skipped:
            w(f"- 加载失败跳过: {len(skipped)} 个")
        w("")

    def _baseline_summary_line(self, baseline: dict | None) -> str:
        """结论里的一句话基线对比摘要。无基线时提示首次评测。"""
        if not baseline:
            return "- 基线对比: 首次评测，无历史基线可比"
        overall = baseline.get("overall", {})
        version = baseline.get("baseline_version", "?")
        b_rate = overall.get("baseline_pass_rate", 0)
        c_rate = overall.get("current_pass_rate", 0)
        change = overall.get("change", 0)
        regressions = baseline.get("regressions", [])

        if change > 0:
            mark = "✅"
            extra = f"，但有 {len(regressions)} 个 case 退化" if regressions else ""
            return (f"- 基线对比 (vs {version}): {mark} 通过率 "
                    f"当前 {c_rate:.1%} vs baseline {b_rate:.1%}（{change:+.1%}）{extra}")
        if change < 0:
            mark = "⚠️"
            return (f"- 基线对比 (vs {version}): {mark} 通过率 "
                    f"当前 {c_rate:.1%} vs baseline {b_rate:.1%}（{change:+.1%}），"
                    f"{len(regressions)} 个退化")
        mark = "➡️"
        extra = f"，但有 {len(regressions)} 个 case 退化" if regressions else ""
        return (f"- 基线对比 (vs {version}): {mark} 通过率 "
                f"当前 {c_rate:.1%} vs baseline {b_rate:.1%}，与基线持平{extra}")

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
        w("## 五、Baseline 对比")
        w("")
        if not baseline:
            w("首次评测，无历史基线可比。")
            w("")
            w("发版后执行 `--save-baseline VERSION` 保存首个基线，"
              "之后每次评测将自动与本基线对比。")
            w("")
            return
        w(f"当前版本 -> Baseline **{baseline.get('baseline_version', '?')}**")
        w("")

        self._baseline_overall(w, baseline.get("overall", {}))
        self._baseline_sections(w, baseline)
        self._baseline_cases(w, baseline)
        w("")

    # ==================== Baseline 各子节 ====================

    @staticmethod
    def _change_mark(change: float | None) -> str:
        """变化方向标记。None 表示不可比。"""
        if change is None:
            return ""
        if change > 0:
            return " ✅"
        if change < 0:
            return " ⚠️"
        return " ➡️"

    @staticmethod
    def _fmt_pct(v: float | None) -> str:
        return f"{v:.1%}" if v is not None else "-"

    @staticmethod
    def _fmt_score(v: float | None) -> str:
        return f"{v:.2f}" if v is not None else "-"

    def _fmt_pct_change(self, change: float | None) -> str:
        if change is None:
            return "-"
        return f"{change:+.1%}{self._change_mark(change)}"

    def _fmt_score_change(self, change: float | None) -> str:
        if change is None:
            return "-"
        return f"{change:+.2f}{self._change_mark(change)}"

    def _baseline_overall(self, w, overall: dict) -> None:
        """5.1 总分比对：通过率 + 平均分。"""
        if not overall:
            return
        w("### 5.1 总分比对")
        w("")
        w("| 指标 | 当前 | baseline | 变化 |")
        w("|------|------|----------|------|")

        b_rate = overall.get("baseline_pass_rate", 0)
        c_rate = overall.get("current_pass_rate", 0)
        change = overall.get("change", 0)
        w(f"| 通过率 | {self._fmt_pct(c_rate)} | {self._fmt_pct(b_rate)} "
          f"| {self._fmt_pct_change(change)} |")

        b_avg = overall.get("baseline_avg_score")
        c_avg = overall.get("current_avg_score")
        avg_change = overall.get("avg_score_change")
        if b_avg is not None:
            w(f"| 平均分 | {self._fmt_score(c_avg)} | {self._fmt_score(b_avg)} "
              f"| {self._fmt_score_change(avg_change)} |")
        else:
            w(f"| 平均分 | {self._fmt_score(c_avg)} | - | （旧 baseline 无此数据） |")
        w("")

    def _baseline_sections(self, w, baseline: dict) -> None:
        """5.2 详细比对：按优先级 + 按维度（通过率 + 平均分）。"""
        w("### 5.2 详细比对")
        w("")

        by_priority = baseline.get("by_priority", {})
        if by_priority:
            w("**按优先级（通过率）:**")
            w("")
            w("| 优先级 | 当前 | baseline | 变化 |")
            w("|--------|------|----------|------|")
            for p, s in by_priority.items():
                w(f"| {p} | {self._fmt_pct(s.get('current'))} "
                  f"| {self._fmt_pct(s.get('baseline'))} "
                  f"| {self._fmt_pct_change(s.get('change'))} |")
            w("")

        by_dimension = baseline.get("by_dimension", {})
        if by_dimension:
            w("**按维度（通过率）:**")
            w("")
            w("| 维度 | 当前 | baseline | 变化 |")
            w("|------|------|----------|------|")
            for d, s in by_dimension.items():
                w(f"| {d} | {self._fmt_pct(s.get('current'))} "
                  f"| {self._fmt_pct(s.get('baseline'))} "
                  f"| {self._fmt_pct_change(s.get('change'))} |")
            w("")

        dim_avg = baseline.get("dimension_avg_score", {})
        if dim_avg:
            w("**按维度（平均分）:**")
            w("")
            w("| 维度 | 当前 | baseline | 变化 |")
            w("|------|------|----------|------|")
            for d, s in dim_avg.items():
                w(f"| {d} | {self._fmt_score(s.get('current'))} "
                  f"| {self._fmt_score(s.get('baseline'))} "
                  f"| {self._fmt_score_change(s.get('change'))} |")
            w("")

    def _baseline_cases(self, w, baseline: dict) -> None:
        """5.3 case 级变化：退化 / 修复 / 分数变化 / 新增 / 移除。"""
        regressions = baseline.get("regressions", [])
        fixes = baseline.get("fixes", [])
        score_changes = baseline.get("score_changes", [])
        new_cases = baseline.get("new_cases", [])
        removed = baseline.get("removed_cases", [])

        has_change = any([regressions, fixes, score_changes, new_cases, removed])
        if not has_change:
            w("### 5.3 case 级变化")
            w("")
            w("- 无 case 级变化")
            return

        w("### 5.3 case 级变化")
        w("")

        if regressions:
            w(f"- ⚠️ **退化 case ({len(regressions)} 个)**（baseline 通过 → 当前失败）:")
            w("")
            w("| case_id | 标题 | baseline | 当前 | 失败原因 |")
            w("|---------|------|----------|------|----------|")
            for r in regressions:
                w(f"| {r['case_id']} | {r['title']} | ✅ 通过 | ❌ 失败 "
                  f"| {r.get('error', '')[:60]} |")
            w("")

        if fixes:
            w(f"- ✅ **修复 case ({len(fixes)} 个)**（baseline 失败 → 当前通过）:")
            w("")
            w("| case_id | 标题 | baseline | 当前 |")
            w("|---------|------|----------|------|")
            for f in fixes:
                w(f"| {f['case_id']} | {f['title']} | ❌ 失败 | ✅ 通过 |")
            w("")

        if score_changes:
            w(f"- 📈 **分数变化 case ({len(score_changes)} 个)**（通过状态不变，仅分数变化）:")
            w("")
            w("| case_id | 标题 | 当前分 | baseline 分 | 变化 |")
            w("|---------|------|--------|------------|------|")
            for s in score_changes:
                w(f"| {s['case_id']} | {s['title']} "
                  f"| {self._fmt_score(s.get('current_score'))} "
                  f"| {self._fmt_score(s.get('baseline_score'))} "
                  f"| {self._fmt_score_change(s.get('change'))} |")
            w("")

        if new_cases:
            w(f"- ➕ **新增 case ({len(new_cases)} 个)**（当前新增，baseline 中不存在）:")
            w("")
            w("| case_id | 标题 | 优先级 | 当前状态 |")
            w("|---------|------|--------|----------|")
            for n in new_cases:
                passed = n.get("current_passed")
                mark = "✅ 通过" if passed else "❌ 失败"
                warn = " ⚠️" if (n.get("priority") == "P0" and not passed) else ""
                w(f"| {n['case_id']} | {n.get('title', '-')} "
                  f"| {n.get('priority', '-')} | {mark}{warn} |")
            w("")

        if removed:
            w(f"- ➖ **移除 case ({len(removed)} 个)**（baseline 中存在，当前已移除）:")
            w("")
            w("| case_id | 标题 | 优先级 | baseline 状态 |")
            w("|---------|------|--------|--------------|")
            for rm in removed:
                passed = rm.get("baseline_passed")
                mark = "✅ 通过" if passed else "❌ 失败"
                warn = " ⚠️" if (rm.get("priority") == "P0" or not passed) else ""
                w(f"| {rm['case_id']} | {rm.get('title', '-')} "
                  f"| {rm.get('priority', '-')} | {mark}{warn} |")
            w("")

    def _appendix(self, w, cost_summary: dict | None,
                  snapshot: dict | None) -> None:
        w("## 附录：评测配置")
        w("")

        if snapshot:
            agent = snapshot.get("agent", {})
            judge = snapshot.get("judge", {})
            w("| 配置项 | 值 |")
            w("|--------|-----|")
            w(f"| Agent 端点 | {agent.get('endpoint', '')} |")
            w(f"| Agent 温度 | {agent.get('temperature', '')} |")
            w(f"| Judge 模型 | {judge.get('model', '')} |")
            w(f"| Judge 温度 | {judge.get('temperature', '')} |")
            w(f"| 用例数 | {snapshot.get('case_count', '')} |")
            w("")

        if cost_summary:
            w("### 成本")
            w("")
            w("| 调用方 | 请求数 | 输入 token | 输出 token |")
            w("|--------|--------|-----------|-----------|")
            by_caller = cost_summary.get("by_caller", {})
            if by_caller:
                for caller, s in by_caller.items():
                    w(f"| {caller} | {s.get('requests', 0)} | "
                      f"{s.get('input_tokens', 0)} | {s.get('output_tokens', 0)} |")
            else:
                w(f"| 全部 | {cost_summary.get('total_calls', 0)} | - | - |")
                w(f"| 总成本 | ${cost_summary.get('total_cost_usd', 0):.6f} | | |")
            w("")

        w("---")
        w("")
        w("> 本报告由 EvalForge 自动生成。结果在相同配置下可复现。")
