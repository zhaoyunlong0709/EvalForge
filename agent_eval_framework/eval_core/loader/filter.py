"""CaseFilter：按 tags/priority/dimensions 筛选。

对应《Agent评测框架设计方案》4.2 用例加载模块。
筛选语义：
- 同一筛选类型内部为 OR（任一匹配即保留）：
    tags=["smoke", "regression"] -> tags 含 smoke 或 regression 的 case
    priorities=["P0", "P1"]      -> priority 为 P0 或 P1 的 case
- 不同筛选类型之间为 AND（同时满足）：
    指定 tags 和 priorities 时，case 必须同时满足两者
- 筛选条件为空/None 时不做该维度筛选
"""
from eval_core.models import AgentCase, APICase, EvaluableCase, Priority


class CaseFilter:
    """用例筛选器"""

    @staticmethod
    def filter(
        cases: list[EvaluableCase],
        tags: list[str] | None = None,
        priorities: list[Priority | str] | None = None,
        dimensions: list[str] | None = None,
    ) -> list[EvaluableCase]:
        """筛选用例列表。

        Args:
            cases: 待筛选的用例列表
            tags: 标签筛选（OR 语义，None 不过滤）
            priorities: 优先级筛选（OR 语义，None 不过滤）
            dimensions: 维度筛选（OR 语义，仅对 AgentCase 生效，None 不过滤）

        Returns:
            符合条件的用例列表
        """
        result = cases
        if tags:
            tag_set = set(tags)
            result = [c for c in result if tag_set & set(c.tags)]
        if priorities:
            pri_set = {Priority(p) if isinstance(p, str) else p for p in priorities}
            result = [c for c in result if c.priority in pri_set]
        if dimensions:
            dim_set = set(dimensions)
            result = [
                c for c in result
                if isinstance(c, AgentCase) and dim_set & set(c.dimensions)
            ]
        return result
