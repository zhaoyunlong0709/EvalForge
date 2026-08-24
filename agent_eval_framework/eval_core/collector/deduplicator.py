"""CaseDeduplicator：与已有 case 比对，避免重复。

对应《Agent评测框架设计方案》4.3 用例搜集模块。

判重策略：
  1. 用户输入完全相同 + 场景相同 -> 重复
  2. 首条用户输入相似度 > 0.85 + 失败类型相同 -> 疑似重复

相似度算法：difflib.SequenceMatcher（标准库，零依赖）
"""
import difflib
from dataclasses import dataclass

from eval_core.utils import logger


@dataclass
class DedupResult:
    """去重检查结果"""

    is_duplicate: bool
    duplicate_of: str | None = None   # 与哪个已有 case 重复
    similarity: float = 0.0           # 相似度
    reason: str = ""                  # 判重原因


class CaseDeduplicator:
    """用例去重器"""

    SIMILARITY_THRESHOLD = 0.85

    def __init__(self, existing_cases: list[dict]):
        """Args: existing_cases: 已有 case 的 JSON dict 列表"""
        self.existing = existing_cases

    def check(self, new_case: dict) -> DedupResult:
        """检查新 case 是否与已有 case 重复。

        Args:
            new_case: 待检查的新 case JSON dict

        Returns:
            DedupResult 判重结果
        """
        new_first_user = self._get_first_user_input(new_case)
        new_scenario = new_case.get("scenario", "")
        new_failure = (
            new_case.get("golden", {}).get("failure", {}).get("category", "")
        )

        for existing in self.existing:
            existing_first_user = self._get_first_user_input(existing)
            existing_scenario = existing.get("scenario", "")
            existing_failure = (
                existing.get("golden", {}).get("failure", {}).get("category", "")
            )

            # 策略 1：完全相同输入 + 相同场景
            if (
                new_first_user == existing_first_user
                and new_scenario == existing_scenario
            ):
                return DedupResult(
                    is_duplicate=True,
                    duplicate_of=existing.get("case_id", ""),
                    similarity=1.0,
                    reason="用户输入和场景完全相同",
                )

            # 策略 2：高相似度 + 相同失败类型
            similarity = self._similarity(new_first_user, existing_first_user)
            if (
                similarity >= self.SIMILARITY_THRESHOLD
                and new_failure == existing_failure
                and new_failure  # 空字符串不参与此策略
            ):
                return DedupResult(
                    is_duplicate=True,
                    duplicate_of=existing.get("case_id", ""),
                    similarity=round(similarity, 3),
                    reason=f"相似度 {similarity:.2f} 且失败类型相同（{new_failure}）",
                )

        return DedupResult(is_duplicate=False)

    @staticmethod
    def _get_first_user_input(case: dict) -> str:
        """提取 case 的第一条用户输入。"""
        conversation = case.get("conversation", [])
        for turn in conversation:
            if turn.get("user"):
                return turn["user"]
        return ""

    @staticmethod
    def _similarity(text1: str, text2: str) -> float:
        """计算两段文本的相似度（0-1）。"""
        if not text1 or not text2:
            return 0.0
        return difflib.SequenceMatcher(None, text1, text2).ratio()
