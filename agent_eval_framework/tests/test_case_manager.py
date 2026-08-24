"""CaseRepository 测试：同步、查询、逻辑删除、统计。"""
import json
from pathlib import Path

import pytest

from eval_core.case_manager import CaseRepository
from eval_core.models import AgentCase, APICase


@pytest.fixture
def agent_case(tmp_path):
    """创建一个 AgentCase 及其对应文件。"""
    file_path = tmp_path / "C001.json"
    file_path.write_text(json.dumps({"case_id": "C001"}), encoding="utf-8")
    case = AgentCase.model_validate({
        "case_id": "C001",
        "title": "记忆写入测试",
        "version": "v0.1",
        "priority": "P1",
        "scenario": "长期偏好记忆",
        "dimensions": ["D9.记忆召回", "D8.回答准确性"],
        "tags": ["记忆", "regression"],
        "conversation": [{"turn": 1, "user": "你记一下"}],
        "evaluation": {"method": "llm_judge", "template": "test"},
    })
    return case, file_path


@pytest.fixture
def api_case(tmp_path):
    """创建一个 APICase 及其对应文件。"""
    file_path = tmp_path / "API_001.json"
    file_path.write_text(json.dumps({"case_id": "API_001"}), encoding="utf-8")
    case = APICase.model_validate({
        "case_id": "API_001",
        "case_type": "api",
        "title": "健康检查",
        "version": "v0.1",
        "priority": "P0",
        "scenario": "系统监控",
        "tags": ["smoke"],
        "request": {"method": "GET", "url": "https://api.example.com/health"},
        "evaluation": {"method": "automated", "rules": []},
    })
    return case, file_path


@pytest.fixture
def repo(tmp_path):
    """每个测试独立的临时数据库。"""
    return CaseRepository(tmp_path / "test.db")


class TestSync:
    def test_sync_insert(self, repo, agent_case, api_case):
        """首次同步：插入 2 条记录。"""
        result = repo.sync([agent_case, api_case])
        assert result["synced"] == 2
        assert result["deprecated"] == 0

    def test_sync_upsert(self, repo, agent_case):
        """二次同步：更新而不报错。"""
        repo.sync([agent_case])
        # 修改 title 后再同步
        case, fp = agent_case
        case.title = "修改后的标题"
        result = repo.sync([(case, fp)])
        assert result["synced"] == 1
        assert repo.get("C001")["title"] == "修改后的标题"

    def test_sync_marks_deleted_files(self, repo, agent_case):
        """文件删除后同步：标记 deprecated。"""
        case, fp = agent_case
        repo.sync([agent_case])

        # 删除文件
        fp.unlink()
        result = repo.sync([])

        assert result["deprecated"] == 1
        row = repo.get("C001")
        assert row["case_status"] == "deprecated"
        assert row["deprecated_reason"] == "file_deleted"

    def test_sync_restores_when_file_back(self, repo, agent_case):
        """文件恢复后同步：从 deprecated 恢复 active。"""
        case, fp = agent_case
        repo.sync([agent_case])

        # 删除文件 -> 同步 -> deprecated
        fp.unlink()
        repo.sync([])

        # 文件恢复 -> 同步 -> active
        fp.write_text(json.dumps({"case_id": "C001"}), encoding="utf-8")
        result = repo.sync([agent_case])

        row = repo.get("C001")
        assert row["case_status"] == "active"
        assert row["deprecated_reason"] is None


class TestSearch:
    def test_search_by_priority(self, repo, agent_case, api_case):
        repo.sync([agent_case, api_case])
        results = repo.search(priority="P0")
        assert len(results) == 1
        assert results[0]["case_id"] == "API_001"

    def test_search_by_case_type(self, repo, agent_case, api_case):
        repo.sync([agent_case, api_case])
        results = repo.search(case_type="agent")
        assert len(results) == 1
        assert results[0]["case_id"] == "C001"

    def test_search_by_dimension(self, repo, agent_case):
        repo.sync([agent_case])
        results = repo.search(dimension="D9")
        assert len(results) == 1

    def test_search_by_tag(self, repo, agent_case):
        repo.sync([agent_case])
        results = repo.search(tag="regression")
        assert len(results) == 1

    def test_search_by_version(self, repo, agent_case):
        repo.sync([agent_case])
        results = repo.search(version="v0.1")
        assert len(results) == 1
        results = repo.search(version="v0.99")
        assert len(results) == 0

    def test_search_by_keyword(self, repo, agent_case):
        repo.sync([agent_case])
        results = repo.search(keyword="记忆")
        assert len(results) == 1

    def test_search_excludes_deprecated(self, repo, agent_case):
        case, fp = agent_case
        repo.sync([agent_case])
        repo.update_status("C001", "deprecated", "手动作废")

        # 默认只查 active
        assert len(repo.search()) == 0
        # 显式查 deprecated
        assert len(repo.search(case_status="deprecated")) == 1
        # 查全部
        assert len(repo.search(case_status="all")) == 1

    def test_search_combined(self, repo, agent_case, api_case):
        repo.sync([agent_case, api_case])
        results = repo.search(case_type="agent", priority="P1", dimension="D9")
        assert len(results) == 1


class TestLifecycle:
    def test_deprecate_and_restore(self, repo, agent_case):
        repo.sync([agent_case])

        # 作废
        assert repo.update_status("C001", "deprecated", "需求变更") is True
        row = repo.get("C001")
        assert row["case_status"] == "deprecated"
        assert row["deprecated_reason"] == "需求变更"

        # 恢复
        assert repo.update_status("C001", "active") is True
        row = repo.get("C001")
        assert row["case_status"] == "active"
        assert row["deprecated_reason"] is None

    def test_update_nonexistent(self, repo):
        assert repo.update_status("C999", "deprecated") is False


class TestStatistics:
    def test_statistics(self, repo, agent_case, api_case):
        repo.sync([agent_case, api_case])

        stats = repo.get_statistics()
        assert stats["total"] == 2
        assert stats["by_priority"] == {"P0": 1, "P1": 1}
        assert stats["by_case_type"] == {"agent": 1, "api": 1}
        assert stats["by_status"] == {"active": 2}
        assert "D9" in stats["by_dimension"]
        assert "D8" in stats["by_dimension"]

    def test_statistics_excludes_deprecated(self, repo, agent_case):
        repo.sync([agent_case])
        repo.update_status("C001", "deprecated")

        stats = repo.get_statistics()
        assert stats["by_status"] == {"deprecated": 1}
        assert stats["by_priority"] == {}  # active 的按优先级统计为空

    def test_statistics_dimension_grouped_by_first_level(self, repo, agent_case):
        """D9.记忆召回 和 D9.记忆保持 都归到 D9。"""
        repo.sync([agent_case])
        stats = repo.get_statistics()
        # C001 的 dimensions 是 ["D9.记忆召回", "D8.回答准确性"]
        assert stats["by_dimension"]["D9"] == 1
        assert stats["by_dimension"]["D8"] == 1


class TestGetFilePath:
    def test_get_file_path(self, repo, agent_case):
        case, fp = agent_case
        repo.sync([agent_case])
        assert repo.get_file_path("C001") == str(fp)

    def test_get_nonexistent(self, repo):
        assert repo.get_file_path("C999") is None
        assert repo.get("C999") is None
