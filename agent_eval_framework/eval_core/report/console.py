"""ConsoleReporter：终端实时输出。

对应《Agent评测框架设计方案》4.7 报告生成模块。
样本量规则（对应《Agent评测集格式规范》第五节）：
  < 5 条逐条列出；6~29 条报 X/Y；>= 30 条报百分比。
"""
from eval_core.models import EvaluableCase


def _fmt_rate(passed: int, count: int) -> str:
    """按样本量规则格式化通过率。"""
    if count == 0:
        return "0/0"
    if count < 5:
        return f"{passed}/{count}"
    if count < 30:
        return f"{passed}/{count}"
    return f"{passed / count:.1%} ({passed}/{count})"


class ConsoleReporter:
    """终端报告输出"""

    def generate(self, cases: list[EvaluableCase], aggregated: dict,
                 cost_summary: dict | None = None, snapshot: dict | None = None) -> str:
        """生成终端报告文本。"""
        lines: list[str] = []
        w = lines.append

        w("=" * 64)
        w("EvalForge 评测报告")
        w("=" * 64)

        # 总览
        t = aggregated["total"]
        w(f"总计: {t['count']} 个用例, {t['passed']} 通过, {t['failed']} 失败 "
          f"(通过率 {t['pass_rate']:.1%})")

        skipped = aggregated.get("skipped", [])
        if skipped:
            w(f"跳过: {len(skipped)} 个（加载失败）")

        # 按优先级
        by_pri = aggregated.get("by_priority", {})
        if by_pri:
            w("")
            w("按优先级:")
            for p, s in by_pri.items():
                mark = "✅" if s["passed"] == s["count"] else (
                    "⚠️" if s["passed"] > 0 else "❌")
                w(f"  {mark} {p}: {_fmt_rate(s['passed'], s['count'])}")

        # 按维度
        by_dim = aggregated.get("by_dimension", {})
        if by_dim:
            w("")
            w("按维度:")
            for d, s in by_dim.items():
                mark = "✅" if s["pass_rate"] >= 0.9 else "⚠️"
                w(f"  {mark} {d}: {_fmt_rate(s['passed'], s['count'])}")

        # 失败明细
        failures = aggregated.get("failures", [])
        if failures:
            w("")
            w("失败用例:")
            for f in failures:
                w(f"  ❌ {f['case_id']}: {f['title']} [{f['priority']}]")
                if f.get("error_analysis"):
                    for line in str(f["error_analysis"]).splitlines():
                        w(f"     {line}")
                if f.get("trace_errors"):
                    w(f"     trace_errors: {len(f['trace_errors'])} 条")
                if f.get("trace_gaps"):
                    w(f"     trace_gaps: {', '.join(f['trace_gaps'])}")

        # 跳过明细
        if skipped:
            w("")
            w("跳过用例（加载失败）:")
            for s in skipped:
                w(f"  ⏭️ {s['file']}: {s['error'][:60]}")

        # 成本 + 快照
        if cost_summary:
            w("")
            total = cost_summary.get("total_cost_usd", 0)
            judge = cost_summary.get("judge_cost_usd", 0)
            agent = cost_summary.get("agent_cost_usd", 0)
            calls = cost_summary.get("total_calls", 0)
            w(f"成本: ${total:.6f} (Judge: ${judge:.6f}, Agent: ${agent:.6f}, "
              f"调用 {calls} 次)")

        if snapshot:
            w("")
            w("可复现性快照:")
            w(f"  Agent: {snapshot.get('agent', {}).get('endpoint', '')}")
            w(f"  Judge: {snapshot.get('judge', {}).get('model', '')}")
            seeds = snapshot.get("seeds", {})
            w(f"  种子: agent={seeds.get('agent_seed')}, judge={seeds.get('judge_seed')}")

        w("=" * 64)
        return "\n".join(lines)
