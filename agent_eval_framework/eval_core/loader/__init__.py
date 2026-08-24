"""用例加载包：发现 -> 分发 -> 加载 -> 筛选。

对应《Agent评测框架设计方案》4.2 用例加载模块。
"""
from pathlib import Path

from .api_loader import APICaseLoader
from .agent_loader import AgentCaseLoader, ProfileResolver
from .registry import LoaderRegistry
from .discoverer import CaseDiscoverer
from .filter import CaseFilter


def create_loader_registry(profiles_dir: Path) -> LoaderRegistry:
    """创建默认注册表：注册 api 和 agent 两种 Loader。

    profiles_dir: profiles/ 根目录，AgentCaseLoader 的 ProfileResolver 使用。
    """
    registry = LoaderRegistry()
    registry.register("api", APICaseLoader())
    registry.register("agent", AgentCaseLoader(profiles_dir))
    return registry


__all__ = [
    "APICaseLoader",
    "AgentCaseLoader",
    "ProfileResolver",
    "LoaderRegistry",
    "CaseDiscoverer",
    "CaseFilter",
    "create_loader_registry",
]
