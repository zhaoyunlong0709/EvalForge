"""collector 模块测试：ManualSource / Classifier / Formatter / Deduplicator / ReviewQueue / Pipeline。"""
import json
from datetime import datetime

import pytest

from eval_core.collector import (
    ManualSource, RawCase, CaseClassifier, CaseClassification,
    CaseFormatter, CaseDeduplicator, ReviewQueue, CollectionPipeline,
)


# ==================== ManualSource ====================

class TestManualSource:
    def test_add_conversation(self):
        src = ManualSource()
        raw = src.add_conversation([
            {"role": "user", "content": "你记一下我不喝咖啡"},
            {"role": "assistant", "content": "好的"},
        ], failure_signal="用户反馈推荐了咖啡")
        assert raw.source_type == "manual"
        assert raw.failure_signal == "用户反馈推荐了咖啡"
        assert len(raw.raw_conversation) == 2

    async def test_fetch_consumes_all(self):
        src = ManualSource()
        src.add_conversation([{"role": "user", "content": "a"}])
        src.add_conversation([{"role": "user", "content": "b"}])
        result = await src.fetch(since=datetime.min)
        assert len(result) == 2
        # 消费后队列为空
        assert len(await src.fetch(since=datetime.min)) == 0

    def test_user_messages(self):
        raw = RawCase(
            source_type="manual", source_id="1",
            raw_conversation=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "world"},
            ],
        )
        assert raw.user_messages == ["hello", "world"]

    def test_source_type(self):
        assert ManualSource().source_type() == "manual"


# ==================== CaseClassifier ====================

class TestCaseClassifier:
    async def test_no_judge_client_returns_default(self):
        classifier = CaseClassifier(judge_client=None)
        raw = RawCase("manual", "1", [{"role": "user", "content": "test"}])
        result = await classifier.classify(raw)
        assert result.confidence == 0.0
        assert "跳过" in result.reason or "未配置" in result.reason

    def test_format_conversation(self):
        raw = RawCase("manual", "1", [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
        text = CaseClassifier._format_conversation(raw)
        assert "用户: hello" in text
        assert "Agent: hi" in text

    def test_parse_classification_valid(self):
        result = CaseClassifier._parse_classification(json.dumps({
            "scenario": "测试场景",
            "dimensions": ["D9.记忆召回"],
            "failure_category": "记忆召回失败",
            "priority": "P1",
            "suggested_title": "测试标题",
            "confidence": 0.9,
            "reason": "理由",
        }))
        assert result.scenario == "测试场景"
        assert result.confidence == 0.9
        assert result.priority == "P1"

    def test_parse_classification_invalid_json(self):
        result = CaseClassifier._parse_classification("not json")
        assert result.confidence == 0.0
        assert "解析失败" in result.reason


# ==================== CaseFormatter ====================

class TestCaseFormatter:
    @pytest.fixture
    def formatter(self, tmp_path):
        return CaseFormatter(tmp_path / "auto_collected")

    def test_format(self, formatter):
        raw = RawCase("manual", "1", [
            {"role": "user", "content": "你记一下我不喝咖啡"},
            {"role": "assistant", "content": "好的"},
        ])
        classification = CaseClassification(
            scenario="长期偏好记忆",
            dimensions=["D9.记忆召回"],
            failure_category="记忆召回失败",
            priority="P1",
            suggested_title="测试标题",
            confidence=0.9,
        )
        case = formatter.format(raw, classification)
        assert case["case_id"].startswith("C_AUTO_")
        assert case["priority"] == "P1"
        assert case["tags"] == ["auto_collected", "pending_review", "manual"]
        assert len(case["conversation"]) == 1  # 只提取 user 消息
        assert case["golden"]["failure"]["category"] == "记忆召回失败"
        assert case["lifecycle"]["status"] == "draft"

    def test_format_assigns_template(self, formatter):
        raw = RawCase("manual", "1", [{"role": "user", "content": "test"}])
        classification = CaseClassification(failure_category="工具参数错误")
        case = formatter.format(raw, classification)
        assert case["evaluation"]["template"] == "工具使用评估"

    def test_format_unknown_failure_defaults(self, formatter):
        raw = RawCase("manual", "1", [{"role": "user", "content": "test"}])
        classification = CaseClassification(failure_category="未知类型")
        case = formatter.format(raw, classification)
        assert case["evaluation"]["template"] == "通用评估"

    def test_save(self, formatter, tmp_path):
        raw = RawCase("manual", "1", [{"role": "user", "content": "test"}])
        classification = CaseClassification()
        case = formatter.format(raw, classification)
        path = formatter.save(case)
        assert path.exists()
        assert path.name.endswith(".json")


# ==================== CaseDeduplicator ====================

class TestCaseDeduplicator:
    def test_same_input_and_scenario_duplicate(self):
        existing = [{
            "case_id": "C001",
            "scenario": "长期偏好记忆",
            "conversation": [{"turn": 1, "user": "你记一下我不喝咖啡"}],
            "golden": {"failure": {"category": "记忆召回失败"}},
        }]
        new = {
            "case_id": "C_AUTO_001",
            "scenario": "长期偏好记忆",
            "conversation": [{"turn": 1, "user": "你记一下我不喝咖啡"}],
        }
        result = CaseDeduplicator(existing).check(new)
        assert result.is_duplicate
        assert result.similarity == 1.0

    def test_similar_failure_duplicate(self):
        existing = [{
            "case_id": "C001",
            "scenario": "长期偏好记忆",
            "conversation": [{"turn": 1, "user": "你记一下我不喝咖啡和水"}],
            "golden": {"failure": {"category": "记忆召回失败"}},
        }]
        new = {
            "case_id": "C_AUTO_001",
            "scenario": "长期偏好记忆",
            "conversation": [{"turn": 1, "user": "你记一下我不喝咖啡和茶"}],
            "golden": {"failure": {"category": "记忆召回失败"}},
        }
        result = CaseDeduplicator(existing).check(new)
        assert result.is_duplicate

    def test_different_input_not_duplicate(self):
        existing = [{
            "case_id": "C001",
            "scenario": "长期偏好记忆",
            "conversation": [{"turn": 1, "user": "你记一下我不喝咖啡"}],
        }]
        new = {
            "case_id": "C_AUTO_001",
            "scenario": "长期偏好记忆",
            "conversation": [{"turn": 1, "user": "今天天气怎么样"}],
        }
        result = CaseDeduplicator(existing).check(new)
        assert not result.is_duplicate


# ==================== ReviewQueue ====================

class TestReviewQueue:
    @pytest.fixture
    def queue(self, tmp_path):
        return ReviewQueue(tmp_path / "test_review.db")

    def test_enqueue_and_list(self, queue):
        case = {"case_id": "C_AUTO_001", "title": "测试"}
        classification = {"confidence": 0.9}
        queue.enqueue(case, classification, "manual", "sid_1")
        items = queue.list("pending")
        assert len(items) == 1
        assert items[0]["case_id"] == "C_AUTO_001"

    def test_approve(self, queue):
        case = {"case_id": "C_AUTO_001", "title": "测试"}
        queue.enqueue(case, {"confidence": 0.9}, "manual", "sid_1")
        assert queue.approve("C_AUTO_001", reviewer="tester")
        assert len(queue.list("pending")) == 0
        assert len(queue.list("approved")) == 1

    def test_reject(self, queue):
        case = {"case_id": "C_AUTO_001", "title": "测试"}
        queue.enqueue(case, {"confidence": 0.5}, "manual", "sid_1")
        assert queue.reject("C_AUTO_001", reviewer="tester", reason="重复")
        assert len(queue.list("rejected")) == 1

    def test_should_auto_approve(self, queue):
        case = {"case_id": "C_AUTO_001", "title": "测试"}
        queue.enqueue(case, {"confidence": 0.9}, "manual", "sid_1")
        assert queue.should_auto_approve("C_AUTO_001", threshold=0.8)

    def test_should_not_auto_approve_low_confidence(self, queue):
        case = {"case_id": "C_AUTO_001", "title": "测试"}
        queue.enqueue(case, {"confidence": 0.5}, "manual", "sid_1")
        assert not queue.should_auto_approve("C_AUTO_001", threshold=0.8)

    def test_stats(self, queue):
        case = {"case_id": "C_AUTO_001", "title": "测试"}
        queue.enqueue(case, {"confidence": 0.9}, "manual", "sid_1")
        queue.enqueue({"case_id": "C_AUTO_002", "title": "测试2"}, {"confidence": 0.9}, "manual", "sid_2")
        queue.approve("C_AUTO_001", "tester")
        stats = queue.stats()
        assert stats["pending"] == 1
        assert stats["approved"] == 1

    def test_get_case(self, queue):
        case = {"case_id": "C_AUTO_001", "title": "测试", "data": [1, 2, 3]}
        queue.enqueue(case, {"confidence": 0.9}, "manual", "sid_1")
        retrieved = queue.get_case("C_AUTO_001")
        assert retrieved["case_id"] == "C_AUTO_001"
        assert retrieved["data"] == [1, 2, 3]

    def test_approve_nonexistent(self, queue):
        assert not queue.approve("C_NOT_EXIST")

    def test_reject_nonexistent(self, queue):
        assert not queue.reject("C_NOT_EXIST")
