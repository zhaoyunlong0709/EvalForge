"""用例搜集模块：数据源 -> 分类 -> 格式化 -> 去重 -> 审核。"""
from .sources.base import BaseCaseSource, ManualSource, RawCase
from .classifier import CaseClassifier, CaseClassification
from .formatter import CaseFormatter
from .deduplicator import CaseDeduplicator, DedupResult
from .review_queue import ReviewQueue
from .pipeline import CollectionPipeline, CollectResult

__all__ = [
    "BaseCaseSource",
    "ManualSource",
    "RawCase",
    "CaseClassifier",
    "CaseClassification",
    "CaseFormatter",
    "CaseDeduplicator",
    "DedupResult",
    "ReviewQueue",
    "CollectionPipeline",
    "CollectResult",
]
