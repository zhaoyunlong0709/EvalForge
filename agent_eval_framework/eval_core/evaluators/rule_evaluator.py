"""RuleEvaluator：规则断言评测器，两种 case 完全复用。

对应《Agent评测框架设计方案》4.5 + 5.3 路由规则。

角色（文档 5.3）：
- method="automated" 时是主评测器，决定 result.passed
- method="llm_judge" 且 rules 非空时是附加评测器，断言失败也拉低 passed

断言数据源（文档 4.5）：合并视图
- result.actual_reply 若可解析为 JSON dict -> 作为基础视图
- result.actual_trace 的字段覆盖补充（AgentCase 的 tool_calls 等来自这里）
"""
import json

from eval_core.models import EvaluableCase
from eval_core.operators import evaluate_assertion, extract_field


class RuleEvaluator:
    """规则断言评测器"""

    async def evaluate(self, case: EvaluableCase) -> EvaluableCase:
        """执行规则断言并更新 result。

        - 有断言失败：passed=False，失败详情追加到 error_analysis
        - 全部通过且是主评测器（method=automated）：passed=True
        - 全部通过且是附加评测器（method=llm_judge）：不改变 passed（Judge 判定生效）
        """
        rules = case.evaluation.rules
        if not rules:
            return case

        context = self._build_context(case)
        is_main = case.evaluation.method == "automated"

        failures: list[str] = []
        for rule in rules:
            actual = extract_field(context, rule.field)
            if not evaluate_assertion(actual, rule.operator, rule.value):
                failures.append(
                    f"断言失败: {rule.field} {rule.operator} {rule.value!r}, 实际: {actual!r}"
                )

        if failures:
            case.result.passed = False
            detail = "; ".join(failures)
            case.result.error_analysis = (
                f"{case.result.error_analysis}\n{detail}"
                if case.result.error_analysis
                else detail
            )
        elif is_main:
            # 主评测器：全部通过 -> 通过
            case.result.passed = True
        # 附加评测器全部通过：不改变 passed（Judge 的判定生效）

        return case

    @staticmethod
    def _build_context(case: EvaluableCase) -> dict:
        """构建断言数据源合并视图：actual_reply（JSON）+ actual_trace。"""
        context: dict = {}

        reply = case.result.actual_reply
        if reply:
            if isinstance(reply, str):
                try:
                    parsed = json.loads(reply)
                    if isinstance(parsed, dict):
                        context = parsed
                except json.JSONDecodeError:
                    # 非结构化回复（Agent 文本），提供 text 键供 contains/regex 断言
                    context = {"text": reply}
            elif isinstance(reply, dict):
                context = dict(reply)

        # trace 字段覆盖补充（AgentCase 的 tool_calls / memory_* 等来自这里）
        if case.result.actual_trace:
            context = {**context, **case.result.actual_trace}

        return context
