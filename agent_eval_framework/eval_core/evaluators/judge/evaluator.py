"""JudgeEvaluator：LLM-judge 评测器（Phase 1 基础版）。

对应《LLM-as-Judge评测执行流程》第 3-5 步 + 框架设计文档 4.5。
路由角色（文档 5.3）：method="llm_judge" 时是主评测器，决定 result.passed。

基础版范围（Phase 1）：单模板 + 单 Judge 模型 + 阈值判定 + 重试。
不做：校准、多 Judge 模型（Phase 2/6）。
"""
from eval_core.models import AgentCase, EvaluableCase
from eval_core.utils import logger

from .judge_client import JudgeClient
from .template_engine import TemplateEngine, TemplateError


class JudgeEvaluator:
    """LLM-judge 评测器"""

    def __init__(self, template_engine: TemplateEngine, judge_client: JudgeClient):
        self.template_engine = template_engine
        self.judge_client = judge_client

    async def evaluate(self, case: EvaluableCase) -> EvaluableCase:
        """对 AgentCase 执行 LLM-judge 评分。

        非 AgentCase、非 llm_judge 方法、或执行阶段已失败的 case 直接跳过。
        """
        if not isinstance(case, AgentCase):
            return case
        if case.evaluation.method != "llm_judge":
            return case
        # 执行阶段已失败（无回复可评），跳过 Judge
        if case.result.error_analysis and not case.result.actual_reply:
            return case

        # 第 1 步：组装 prompt
        try:
            prompt = self.template_engine.build_prompt(case)
        except TemplateError as e:
            case.result.passed = False
            case.result.error_analysis = f"Judge 模板错误: {e}"
            return case

        # 第 2 步：调用 Judge（含重试）
        judge_result = await self.judge_client.score(prompt, case.dimensions)

        # 第 3 步：判定通过/失败（对应流程第 5 步）
        if judge_result.error:
            case.result.passed = False
            case.result.error_analysis = judge_result.error
            return case

        case.result.score = judge_result.score
        case.result.judge_reason = judge_result.reason
        case.result.dimension_scores.update(judge_result.dimension_scores)

        threshold = case.evaluation.threshold
        if judge_result.score >= threshold:
            case.result.passed = True
        else:
            case.result.passed = False
            case.result.error_analysis = (
                f"Judge 评分 {judge_result.score} < 阈值 {threshold}。"
                f"原因：{judge_result.reason}"
            )

        return case
