"""共享 fixtures。"""
import json
import sys
from pathlib import Path

import pytest

# 确保能导入 eval_core（支持未 pip install -e 时直接跑 pytest）
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from eval_core.models import AgentCase, APICase  # noqa: E402


@pytest.fixture
def api_case_data() -> dict:
    """APICase 样例数据（对应文档附录 A.1）"""
    return {
        "case_type": "api",
        "case_id": "API_001",
        "title": "健康检查",
        "version": "v0.1",
        "priority": "P0",
        "scenario": "系统监控",
        "tags": ["smoke"],
        "request": {
            "method": "GET",
            "url": "https://api.example.com/health",
            "headers": {"Authorization": "Bearer {{API_TOKEN}}"},
            "timeout_ms": 5000,
        },
        "evaluation": {
            "method": "automated",
            "rules": [
                {"field": "status_code", "operator": "eq", "value": 200},
                {"field": "body.status", "operator": "eq", "value": "ok"},
                {"field": "latency_ms", "operator": "lt", "value": 1000},
            ],
        },
    }


@pytest.fixture
def agent_case_data() -> dict:
    """AgentCase 样例数据（对应文档附录 A.2，最小 smoke case）"""
    return {
        "case_type": "agent",
        "case_id": "C001",
        "title": "晚间情绪陪伴-共情回应",
        "version": "v0.1",
        "priority": "P1",
        "scenario": "晚间情绪陪伴",
        "dimensions": ["D2.情绪识别", "D8.共情适当率"],
        "tags": ["陪伴", "情绪", "smoke"],
        "profile": "test_profile",
        "conversation": [
            {"turn": 1, "user": "我今天又被老板否了方案。", "expect": "先共情，不急于给解决方案"},
        ],
        "evaluation": {
            "method": "llm_judge",
            "template": "共情评估",
            "params": {"user_emotion": "挫败/悲伤"},
            "threshold": 4,
        },
    }


@pytest.fixture
def profile_dir(tmp_path) -> Path:
    """临时 profiles 目录，含 test_profile"""
    profiles = tmp_path / "profiles" / "companion"
    profiles.mkdir(parents=True)
    (profiles / "test_profile.json").write_text(json.dumps({
        "profile_id": "test_profile",
        "description": "测试画像（元数据字段不应进入 setup）",
        "user_profile": "职场白领，最近睡眠质量差",
        "preloaded_memories": ["用户偏好晚上喝咖啡"],
    }), encoding="utf-8")
    return tmp_path / "profiles"


@pytest.fixture
def api_case(api_case_data) -> APICase:
    return APICase.model_validate(api_case_data)


@pytest.fixture
def agent_case(agent_case_data) -> AgentCase:
    return AgentCase.model_validate(agent_case_data)
