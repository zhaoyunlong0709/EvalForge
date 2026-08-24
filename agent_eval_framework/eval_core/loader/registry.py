"""LoaderRegistry：根据 case_type 字段分发到对应 Loader。

对应《Agent评测框架设计方案》4.2 用例加载模块。
扩展新 Case 类型（文档 7.3）：实现 XXXCaseLoader 后调用 register() 注册，
不改任何现有代码。
"""
import json
from pathlib import Path
from typing import Callable, Protocol

from eval_core.models import EvaluableCase


class CaseLoaderProtocol(Protocol):
    """Loader 协议：实现 load(data) 即可注册。

    注意：data 是已解析的 JSON 字典（由 LoaderRegistry 传入，避免重复 IO）。
    """

    def load(self, data: dict) -> EvaluableCase: ...


class LoaderRegistry:
    """加载器注册表，按 case_type 字段分发。"""

    def __init__(self) -> None:
        self._loaders: dict[str, CaseLoaderProtocol] = {}

    def register(self, case_type: str, loader: CaseLoaderProtocol) -> None:
        """注册 Loader。重复注册同一 case_type 时后者覆盖前者。"""
        self._loaders[case_type] = loader

    def load(self, file_path: Path) -> EvaluableCase:
        """读取 JSON 的 case_type 字段，分发到对应 Loader 加载。

        Raises:
            json.JSONDecodeError: JSON 格式错误
            KeyError: case_type 缺失
            ValueError: 未知 case_type（无注册的 Loader）
        """
        data = json.loads(file_path.read_text(encoding="utf-8"))

        if "case_type" not in data:
            raise KeyError(f"用例缺少 case_type 字段: {file_path}")

        case_type = data["case_type"]
        loader = self._loaders.get(case_type)
        if loader is None:
            known = ", ".join(sorted(self._loaders.keys()))
            raise ValueError(
                f"未知的 case_type '{case_type}'（已注册: {known}）: {file_path}"
            )
        # 传已解析的 data 字典，避免 loader 重复 IO
        return loader.load(data)

    @property
    def registered_types(self) -> list[str]:
        return sorted(self._loaders.keys())
