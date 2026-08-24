"""CollectionPipeline：搜集管道编排，串联分类 -> 格式化 -> 去重 -> 审核。

对应《Agent评测框架设计方案》4.3 + 6.2 用例搜集流程。

流程：
  RawCase 列表
    -> CaseClassifier（LLM 自动分类）
    -> CaseFormatter（格式化为标准 JSON）
    -> CaseDeduplicator（与已有 case 去重）
    -> ReviewQueue（入队，高置信度可自动通过）
"""
from dataclasses import dataclass, field
from pathlib import Path

from eval_core.utils import logger

from .classifier import CaseClassifier
from .deduplicator import CaseDeduplicator
from .formatter import CaseFormatter
from .review_queue import ReviewQueue
from .sources.base import BaseCaseSource, RawCase


@dataclass
class CollectResult:
    """单次搜集的结果汇总"""

    total: int = 0                      # 拉取的原始 case 数
    classified: int = 0                 # 分类成功数
    duplicates: int = 0                 # 去重跳过数
    enqueued: int = 0                   # 入队数
    auto_approved: int = 0              # 自动通过数
    errors: list[str] = field(default_factory=list)


class CollectionPipeline:
    """搜集管道"""

    def __init__(
        self,
        classifier: CaseClassifier,
        formatter: CaseFormatter,
        deduplicator: CaseDeduplicator,
        review_queue: ReviewQueue,
    ):
        self.classifier = classifier
        self.formatter = formatter
        self.deduplicator = deduplicator
        self.review_queue = review_queue

    async def collect(
        self,
        source: BaseCaseSource,
        auto_approve_threshold: float = 0.8,
    ) -> CollectResult:
        """执行完整搜集流程。

        Args:
            source: 数据源适配器
            auto_approve_threshold: 自动通过的置信度阈值

        Returns:
            CollectResult 结果汇总
        """
        result = CollectResult()

        # 1. 拉取原始 case
        from datetime import datetime

        raw_cases = await source.fetch(since=datetime.min)
        result.total = len(raw_cases)
        logger.info(f"从 {source.source_type()} 拉取 {len(raw_cases)} 条原始 case")

        for raw in raw_cases:
            try:
                # 2. LLM 分类
                classification = await self.classifier.classify(raw)
                result.classified += 1

                # 3. 格式化为标准 case
                case = self.formatter.format(raw, classification)

                # 4. 去重
                dedup = self.deduplicator.check(case)
                if dedup.is_duplicate:
                    result.duplicates += 1
                    logger.info(
                        f"跳过重复: {case['case_id']} "
                        f"(与 {dedup.duplicate_of} 重复, {dedup.reason})"
                    )
                    continue

                # 5. 入队
                self.review_queue.enqueue(
                    case=case,
                    classification={
                        "scenario": classification.scenario,
                        "dimensions": classification.dimensions,
                        "failure_category": classification.failure_category,
                        "priority": classification.priority,
                        "confidence": classification.confidence,
                        "reason": classification.reason,
                    },
                    source_type=raw.source_type,
                    source_id=raw.source_id,
                )
                result.enqueued += 1

                # 6. 高置信度自动通过
                if classification.confidence >= auto_approve_threshold:
                    self.review_queue.approve(case["case_id"], reviewer="auto")
                    result.auto_approved += 1
                    logger.info(
                        f"自动通过: {case['case_id']} "
                        f"(置信度 {classification.confidence:.2f})"
                    )

            except Exception as e:  # noqa: BLE001 - 单条失败不中断
                result.errors.append(f"{raw.source_id}: {e}")
                logger.warning(f"case 处理失败: {raw.source_id} - {e}")

        logger.info(
            f"搜集完成: {result.total} 拉取, {result.classified} 分类, "
            f"{result.duplicates} 重复跳过, {result.enqueued} 入队, "
            f"{result.auto_approved} 自动通过"
        )
        return result

    def export_approved(self, output_dir: Path) -> list[Path]:
        """将审核通过的 case 导出到指定目录（写入 JSON 文件）。"""
        import json

        approved = self.review_queue.list(status="approved")
        exported = []

        for item in approved:
            case = self.review_queue.get_case(item["case_id"])
            if case is None:
                continue
            file_path = output_dir / f"{case['case_id']}.json"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                json.dumps(case, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            exported.append(file_path)
            logger.info(f"导出: {file_path}")

        return exported
