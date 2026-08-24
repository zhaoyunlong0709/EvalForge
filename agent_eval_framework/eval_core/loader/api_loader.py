"""APICaseLoader：Pydantic 校验 -> 返回 APICase。

对应《Agent评测框架设计方案》4.2 用例加载模块。
注意：data 由 LoaderRegistry 传入（已解析的 JSON 字典，避免重复 IO）。
"""
from eval_core.models import APICase


class APICaseLoader:
    """API 用例加载器"""

    @staticmethod
    def load(data: dict) -> APICase:
        """加载并校验单个 API 用例。

        Args:
            data: 已解析的 JSON 字典（由 LoaderRegistry 传入）

        Raises:
            pydantic.ValidationError: 字段校验失败（必填缺失 / 未知字段 / 类型错误）
        """
        return APICase.model_validate(data)
