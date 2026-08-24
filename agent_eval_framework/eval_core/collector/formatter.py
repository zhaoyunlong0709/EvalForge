"""CaseFormatter：将 RawCase + Classification 格式化为标准 AgentCase JSON。

对应《Agent评测框架设计方案》4.3 用例搜集模块。

职责：
  RawCase（原始对话） + CaseClassification（LLM 分类）
  -> 标准 AgentCase JSON（可直接写入 cases/ 目录）
"""
import json
from datetime import datetime
from pathlib import Path

from eval_core.utils import logger

from .classifier import CaseClassification
from .sources.base import RawCase


# 失败类型 -> 推荐评测模板映射
FAILURE_TEMPLATE_MAP = {
    "记忆召回失败": "记忆使用评估",
    "记忆写入失败": "记忆使用评估",
    "记忆冲突": "记忆使用评估",
    "工具参数错误": "工具使用评估",
    "工具调用失败": "工具使用评估",
    "工具结果未使用": "工具使用评估",
    "安全漏杀": "安全合规评估",
    "安全误杀": "安全合规评估",
    "语气不当": "共情评估",
    "角色漂移": "共情评估",
    "幻觉": "回答准确性评估",
}


class CaseFormatter:
    """将原始 case 格式化为标准 AgentCase JSON"""

    def __init__(self, output_dir: Path):
        """Args: output_dir: 生成的 case JSON 存放目录（如 cases/agent/auto_collected/）"""
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._counter = self._init_counter()

    def _init_counter(self) -> int:
        """根据已有文件数初始化计数器，避免 ID 冲突。"""
        existing = list(self.output_dir.glob("C_AUTO_*.json"))
        if not existing:
            return 1
        max_num = max(int(f.stem.split("_")[-1]) for f in existing)
        return max_num + 1

    def format(
        self,
        raw_case: RawCase,
        classification: CaseClassification,
    ) -> dict:
        """格式化为标准 AgentCase JSON。

        Args:
            raw_case: 原始 case
            classification: LLM 分类结果

        Returns:
            标准 AgentCase dict（可通过 AgentCase.model_validate 校验）
        """
        case_id = f"C_AUTO_{self._counter:04d}"
        self._counter += 1

        # 推荐评测模板
        template = FAILURE_TEMPLATE_MAP.get(
            classification.failure_category, "通用评估"
        )

        case = {
            "case_type": "agent",
            "case_id": case_id,
            "title": classification.suggested_title or f"自动搜集-{raw_case.source_type}",
            "version": "draft",
            "priority": classification.priority,
            "scenario": classification.scenario or "自动搜集",
            "dimensions": classification.dimensions or [],
            "tags": ["auto_collected", "pending_review", raw_case.source_type],

            # conversation 从原始对话提取
            "conversation": self._extract_conversation(raw_case),

            # evaluation 根据失败类型推荐模板
            "evaluation": {
                "method": "llm_judge",
                "template": template,
                "params": {},
                "threshold": 4,
            },

            # 自动标注 golden_truth
            "golden": {
                "annotator": "auto_collector",
                "annotated_at": datetime.now().isoformat(),
                "overall_score": 0,
                "failure": {
                    "severity": classification.priority,
                    "category": classification.failure_category,
                    "root_cause": classification.reason,
                },
            },

            # 生命周期标记
            "lifecycle": {
                "status": "draft",
                "created_at": datetime.now().isoformat(),
                "created_by": "auto_collector",
                "source": raw_case.source_type,
            },
        }

        logger.debug(f"格式化完成: {case_id} -> {case['title']}")
        return case

    def save(self, case: dict) -> Path:
        """将 case JSON 写入文件。"""
        file_path = self.output_dir / f"{case['case_id']}.json"
        file_path.write_text(
            json.dumps(case, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"用例已保存: {file_path}")
        return file_path

    @staticmethod
    def _extract_conversation(raw_case: RawCase) -> list[dict]:
        """从原始对话提取 AgentCase 的 conversation 格式。

        原始格式: [{"role": "user", "content": "..."}, ...]
        Case 格式: [{"turn": 1, "user": "...", "expect": ""}, ...]
        """
        conversation = []
        turn = 0
        for msg in raw_case.raw_conversation:
            if msg.get("role") == "user":
                turn += 1
                conversation.append({
                    "turn": turn,
                    "user": msg.get("content", ""),
                    "expect": "",  # 自动生成的 case 不填 expect，由人工审核时补充
                })
        return conversation
