"""CaseClassifier：用 LLM 给原始 case 自动分类。

对应《Agent评测框架设计方案》4.3 用例搜集模块。

职责：
  输入 RawCase -> 输出 CaseClassification（场景/维度/失败类型/优先级/建议标题/置信度）

置信度规则：
  - confidence >= 0.8 -> 审核队列可自动通过
  - confidence < 0.8  -> 需要人工审核

依赖 JudgeClient（复用已有的 LLM 调用能力，含重试和成本追踪）。
"""
from dataclasses import dataclass, field
from typing import Any

from eval_core.utils import logger

from .sources.base import RawCase


@dataclass
class CaseClassification:
    """LLM 分类结果"""

    scenario: str = ""                  # 场景分类（如 "长期偏好记忆"）
    dimensions: list[str] = field(default_factory=list)  # 涉及的评测维度
    failure_category: str = ""          # 失败类型（如 "记忆召回失败" / "工具参数错误"）
    priority: str = "P2"                # 建议优先级 P0-P3
    suggested_title: str = ""           # 建议标题
    confidence: float = 0.0             # 分类置信度 0-1
    reason: str = ""                    # 分类理由


# 分类 prompt 模板（复用 prompt_templates.json 机制，此处硬编码默认版）
CLASSIFY_PROMPT = """你是一个 Agent 评测专家。请分析以下对话，输出分类结果。

【对话内容】
{conversation}

【失败信号】
{failure_signal}

请严格按以下 JSON 格式输出：
{{
  "scenario": "<场景分类，如：长期偏好记忆/晚间情绪陪伴/工具调用/安全合规>",
  "dimensions": ["<涉及的评测维度，如：D9.记忆召回, D8.回答准确性>"],
  "failure_category": "<失败类型，如：记忆召回失败/工具参数错误/安全漏杀/语气不当/幻觉>",
  "priority": "<建议优先级 P0-P3>",
  "suggested_title": "<一句话标题>",
  "confidence": <0-1 的数字，分类置信度>,
  "reason": "<分类理由，一句话>"
}}
"""


class CaseClassifier:
    """LLM 自动分类器"""

    def __init__(self, judge_client=None):
        """Args: judge_client: JudgeClient 实例（复用 LLM 调用 + 重试 + 成本追踪）"""
        self.judge_client = judge_client

    async def classify(self, raw_case: RawCase) -> CaseClassification:
        """对原始 case 进行 LLM 分类。

        Args:
            raw_case: 从数据源拉取的原始 case

        Returns:
            CaseClassification 分类结果。LLM 不可用时返回默认分类（需人工审核）。
        """
        if self.judge_client is None:
            logger.warning("JudgeClient 未配置，返回默认分类（需人工审核）")
            return CaseClassification(
                confidence=0.0,
                reason="Judge 未配置，跳过自动分类",
            )

        conversation_text = self._format_conversation(raw_case)
        prompt = CLASSIFY_PROMPT.format(
            conversation=conversation_text,
            failure_signal=raw_case.failure_signal or "无",
        )

        result = await self.judge_client.score(prompt)
        if result.error:
            logger.warning(f"分类失败: {result.error}，返回默认分类")
            return CaseClassification(
                confidence=0.0,
                reason=f"LLM 分类失败: {result.error}",
            )

        return self._parse_classification(result.reason)

    @staticmethod
    def _format_conversation(raw_case: RawCase) -> str:
        """将 RawCase 的对话格式化为 prompt 可用的文本。"""
        lines = []
        for msg in raw_case.raw_conversation:
            role = "用户" if msg.get("role") == "user" else "Agent"
            lines.append(f"{role}: {msg.get('content', '')}")
        return "\n".join(lines)

    @staticmethod
    def _parse_classification(raw_output: str) -> CaseClassification:
        """解析 LLM 输出为 CaseClassification。解析失败返回默认值。"""
        import json

        try:
            data = json.loads(raw_output)
            return CaseClassification(
                scenario=data.get("scenario", ""),
                dimensions=data.get("dimensions", []),
                failure_category=data.get("failure_category", ""),
                priority=data.get("priority", "P2"),
                suggested_title=data.get("suggested_title", ""),
                confidence=float(data.get("confidence", 0)),
                reason=data.get("reason", ""),
            )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"分类结果解析失败: {e}")
            return CaseClassification(
                confidence=0.0,
                reason=f"解析失败: {e}",
            )
