"""用例加载测试：LoaderRegistry、ProfileResolver、CaseDiscoverer、CaseFilter。"""
import json
from pathlib import Path

import pytest

from eval_core.loader import (
    APICaseLoader, AgentCaseLoader, CaseDiscoverer, CaseFilter,
    LoaderRegistry, ProfileResolver, create_loader_registry,
)
from eval_core.models import AgentCase, APICase


class TestAPICaseLoader:
    def test_load(self, api_case_data):
        case = APICaseLoader.load(dict(api_case_data))
        assert isinstance(case, APICase)
        assert case.case_id == "API_001"

    def test_validation_error(self, api_case_data):
        del api_case_data["request"]
        with pytest.raises(Exception):
            APICaseLoader.load(api_case_data)


class TestProfileResolver:
    def test_resolve_populates_setup(self, profile_dir, agent_case_data):
        resolver = ProfileResolver(profile_dir)
        data = resolver.resolve(dict(agent_case_data))
        assert data["setup"]["user_profile"] == "职场白领，最近睡眠质量差"
        assert data["setup"]["preloaded_memories"] == ["用户偏好晚上喝咖啡"]
        # 元数据字段不应进入 setup
        assert "profile_id" not in data["setup"]
        assert "description" not in data["setup"]

    def test_profile_not_found(self, profile_dir, agent_case_data):
        agent_case_data["profile"] = "nonexistent"
        resolver = ProfileResolver(profile_dir)
        with pytest.raises(FileNotFoundError):
            resolver.resolve(agent_case_data)

    def test_name_conflict(self, profile_dir):
        # 在另一个子目录创建同名 profile
        other = profile_dir / "other_type"
        other.mkdir()
        (other / "test_profile.json").write_text("{}", encoding="utf-8")
        resolver = ProfileResolver(profile_dir)
        with pytest.raises(ValueError, match="冲突"):
            resolver.resolve({"profile": "test_profile"})

    def test_empty_profile_skipped(self, profile_dir):
        resolver = ProfileResolver(profile_dir)
        data = {"profile": ""}
        assert resolver.resolve(data) is data


class TestLoaderRegistry:
    def test_dispatch(self, profile_dir, tmp_path, api_case_data, agent_case_data):
        registry = create_loader_registry(profile_dir)
        api_file = tmp_path / "api.json"
        api_file.write_text(json.dumps(api_case_data), encoding="utf-8")
        agent_file = tmp_path / "agent.json"
        agent_file.write_text(json.dumps(agent_case_data), encoding="utf-8")

        assert isinstance(registry.load(api_file), APICase)
        assert isinstance(registry.load(agent_file), AgentCase)

    def test_unknown_case_type(self, profile_dir, tmp_path):
        registry = create_loader_registry(profile_dir)
        f = tmp_path / "x.json"
        f.write_text('{"case_type": "webhook"}', encoding="utf-8")
        with pytest.raises(ValueError, match="未知的 case_type"):
            registry.load(f)

    def test_missing_case_type(self, profile_dir, tmp_path):
        registry = create_loader_registry(profile_dir)
        f = tmp_path / "x.json"
        f.write_text('{"case_id": "X"}', encoding="utf-8")
        with pytest.raises(KeyError):
            registry.load(f)

    def test_invalid_json(self, profile_dir, tmp_path):
        registry = create_loader_registry(profile_dir)
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            registry.load(f)


class TestCaseDiscoverer:
    def _make_case_file(self, dir_: Path, name: str, data: dict):
        f = dir_ / f"{name}.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        return f

    def test_load_all(self, profile_dir, tmp_path, api_case_data, agent_case_data):
        cases_dir = tmp_path / "cases"
        (cases_dir / "api").mkdir(parents=True)
        (cases_dir / "agent").mkdir()
        self._make_case_file(cases_dir / "api", "a1", api_case_data)
        self._make_case_file(cases_dir / "agent", "c1", agent_case_data)

        registry = create_loader_registry(profile_dir)
        d = CaseDiscoverer(registry)
        cases, errors = d.load_all(cases_dir)
        assert len(cases) == 2
        assert errors == []

    def test_load_specific_file(self, profile_dir, tmp_path, api_case_data, agent_case_data):
        cases_dir = tmp_path / "cases"
        (cases_dir / "api").mkdir(parents=True)
        f1 = self._make_case_file(cases_dir / "api", "a1", api_case_data)
        agent_case_data["profile"] = ""  # 无 profile 引用
        self._make_case_file(cases_dir / "api", "c1", agent_case_data)

        d = CaseDiscoverer(create_loader_registry(profile_dir))
        cases, _ = d.load_all(cases_dir, case_files=[f1])
        assert len(cases) == 1
        assert cases[0].case_id == "API_001"

    def test_load_specific_dir(self, profile_dir, tmp_path, api_case_data, agent_case_data):
        cases_dir = tmp_path / "cases"
        sub = cases_dir / "smoke"
        sub.mkdir(parents=True)
        self._make_case_file(sub, "a1", api_case_data)

        d = CaseDiscoverer(create_loader_registry(profile_dir))
        cases, _ = d.load_all(cases_dir, case_dirs=[sub])
        assert len(cases) == 1

    def test_nonexistent_path(self, profile_dir, tmp_path):
        d = CaseDiscoverer(create_loader_registry(profile_dir))
        with pytest.raises(FileNotFoundError):
            d.load_all(tmp_path, case_files=[tmp_path / "nope.json"])

    def test_bad_file_skipped_not_fatal(self, profile_dir, tmp_path, api_case_data):
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        self._make_case_file(cases_dir, "good", api_case_data)
        (cases_dir / "bad.json").write_text('{"case_type": "api"}', encoding="utf-8")

        d = CaseDiscoverer(create_loader_registry(profile_dir))
        cases, errors = d.load_all(cases_dir)
        assert len(cases) == 1
        assert len(errors) == 1
        assert "bad.json" in errors[0]["file"]


class TestCaseFilter:
    def _cases(self, api_case, agent_case):
        return [api_case, agent_case]

    def test_filter_by_tags(self, api_case, agent_case):
        result = CaseFilter.filter(self._cases(api_case, agent_case), tags=["smoke"])
        assert {c.case_id for c in result} == {"API_001", "C001"}

    def test_filter_by_priority(self, api_case, agent_case):
        result = CaseFilter.filter(self._cases(api_case, agent_case), priorities=["P0"])
        assert {c.case_id for c in result} == {"API_001"}

    def test_filter_by_dimension(self, api_case, agent_case):
        result = CaseFilter.filter(
            self._cases(api_case, agent_case), dimensions=["D8.共情适当率"]
        )
        assert {c.case_id for c in result} == {"C001"}

    def test_combined_and_semantics(self, api_case, agent_case):
        """不同筛选类型之间为 AND。"""
        result = CaseFilter.filter(
            self._cases(api_case, agent_case), tags=["smoke"], priorities=["P1"]
        )
        assert {c.case_id for c in result} == {"C001"}

    def test_no_match(self, api_case, agent_case):
        result = CaseFilter.filter(self._cases(api_case, agent_case), tags=["xxx"])
        assert result == []

    def test_no_filter_returns_all(self, api_case, agent_case):
        result = CaseFilter.filter(self._cases(api_case, agent_case))
        assert len(result) == 2
