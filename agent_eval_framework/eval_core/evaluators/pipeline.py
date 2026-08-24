"""EvaluatorPipeline：评测器路由 + 调度 + 结果聚合。

对应《Agent评测框架设计方案》4.5 + 5.3 路由规则。

路由规则（文档 5.3）：
- evaluation.method 决定主评测器（决定 result.passed）
- 可选 section 决定附加评测器
- SafetyEvaluator 对所有 AgentCase 始终执行（安全是红线，零成本正则检测）

| method="automated"                        | RuleEvaluator（主） |
| method="llm_judge"                        | JudgeEvaluator（主） |
| method="llm_judge" 且 rules 非空           | + RuleEvaluator（附加，失败计入 passed） |
| trace section 存在                         | + TraceEvaluator（附加，不影响 passed） |
| AgentCase（始终）                          | + SafetyEvaluator（附加，有害/PII 拉低 passed） |
| method="manual"                           | 不自动判断 |
"""
from datetime import datetime, timezone

from eval_core.models import AgentCase, EvaluableCase
from eval_core.utils import logger

from .judge import JudgeEvaluator
from .rule_evaluator import RuleEvaluator
from .safety_evaluator import SafetyEvaluator
from .trace_evaluator import TraceEvaluator


class EvaluatorPipeline:
    """评测器管线"""

    def __init__(
        self,
        rule_evaluator: RuleEvaluator | None = None,
        judge_evaluator: JudgeEvaluator | None = None,
        trace_evaluator: TraceEvaluator | None = None,
        safety_evaluator: SafetyEvaluator | None = None,
    ):
        self.rule_evaluator = rule_evaluator or RuleEvaluator()
        self.judge_evaluator = judge_evaluator  # None 时 llm_judge case 无法评测
        self.trace_evaluator = trace_evaluator or TraceEvaluator()
        self.safety_evaluator = safety_evaluator or SafetyEvaluator()

    async def evaluate(self, case: EvaluableCase) -> EvaluableCase:
        """按路由规则调度评测器。

        执行阶段已失败的 case（error_analysis 非空且无回复）跳过所有评测器。
        """
        # 0. 执行失败 -> 跳过评测（LLM-as-Judge 文档第 2 步：
        #    "如果某一轮 Agent 请求失败，该 case 直接标记失败，不再执行 judge 评估"）
        if case.result.error_analysis and not case.result.actual_reply:
            case.result.passed = False
            logger.warning(
                f"case {case.case_id} 执行阶段已失败，跳过评测: "
                f"{case.result.error_analysis[:80]}"
            )
            self._stamp_evaluated_at(case)
            return case

        method = case.evaluation.method

        # 主评测器
        if method == "automated":
            case = await self.rule_evaluator.evaluate(case)

        elif method == "llm_judge":
            if self.judge_evaluator is None:
                case.result.passed = False
                case.result.error_analysis = (
                    "Judge 未配置（检查 config/global.yaml judge 段），无法执行 llm_judge 评测"
                )
            else:
                case = await self.judge_evaluator.evaluate(case)
                # 附加：rules 非空时 RuleEvaluator（断言失败也拉低 passed）
                if case.evaluation.rules:
                    case = await self.rule_evaluator.evaluate(case)

        # manual：不自动判断（人工评测）

        # 附加：TraceEvaluator（不影响 passed）
        if isinstance(case, AgentCase) and case.trace is not None:
            case = await self.trace_evaluator.evaluate(case)

        # 附加：SafetyEvaluator（对所有 AgentCase 始终执行，有害/PII 拉低 passed）
        if isinstance(case, AgentCase):
            case = await self.safety_evaluator.evaluate(case)

        self._stamp_evaluated_at(case)
        return case

    @staticmethod
    def _stamp_evaluated_at(case: EvaluableCase) -> None:
        case.result.evaluated_at = datetime.now(timezone.utc).isoformat()
