"""CostTracker：统计每次 API 调用的 token 消耗和成本（仪表盘）。

对应《Agent评测框架设计方案》4.7 报告生成模块。
定价从 config/global.yaml 的 pricing 段读取，不硬编码。
Phase 1 只做仪表盘（统计+报告展示）；预算预估 Phase 2-3；--max-cost 刹车 Phase 3。
"""


class CostTracker:
    """成本追踪器（仪表盘）"""

    def __init__(self, pricing: dict[str, dict[str, float]]):
        """pricing: $/1M tokens，如 {"gpt-4o": {"input": 2.5, "output": 10.0}}"""
        self.pricing = pricing
        self.calls: list[dict] = []

    def track(
        self,
        caller: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """记录一次 API 调用，返回该次成本（$）。

        Args:
            caller: 调用方，"judge" 或 "agent"
            model: 模型名
            input_tokens / output_tokens: token 消耗
        """
        price = self.pricing.get(model, {"input": 0.0, "output": 0.0})
        cost = (
            input_tokens * price["input"] + output_tokens * price["output"]
        ) / 1_000_000

        self.calls.append({
            "caller": caller,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
        })
        return cost

    @property
    def total_cost(self) -> float:
        return sum(c["cost"] for c in self.calls)

    @property
    def judge_cost(self) -> float:
        return sum(c["cost"] for c in self.calls if c["caller"] == "judge")

    @property
    def agent_cost(self) -> float:
        return sum(c["cost"] for c in self.calls if c["caller"] == "agent")

    def summary(self) -> dict:
        """成本汇总（供报告使用）"""
        return {
            "total_cost_usd": round(self.total_cost, 6),
            "judge_cost_usd": round(self.judge_cost, 6),
            "agent_cost_usd": round(self.agent_cost, 6),
            "total_calls": len(self.calls),
            "unknown_pricing_models": sorted({
                c["model"] for c in self.calls
                if c["model"] not in self.pricing
            }),
        }

    def summary_text(self) -> str:
        """成本汇总文本（Console 报告使用）"""
        s = self.summary()
        note = ""
        if s["unknown_pricing_models"]:
            note = f"（未配置定价的模型按 0 计: {', '.join(s['unknown_pricing_models'])}）"
        return (
            f"成本: ${s['total_cost_usd']:.6f} "
            f"(Judge: ${s['judge_cost_usd']:.6f}, "
            f"Agent: ${s['agent_cost_usd']:.6f}, "
            f"调用 {s['total_calls']} 次){note}"
        )
