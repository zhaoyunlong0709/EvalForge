"""TraceEvaluator：Trace 链路校验（Phase 1 基础版：断言求值 + 盲区检测）。

对应《LLM-as-Judge评测执行流程》第 6 步 + 框架设计文档 4.5。

路由角色（文档 5.3）：附加评测器。trace_errors / trace_gaps 仅记录，
不影响 result.passed。

"""
import re

from eval_core.models import AgentCase, EvaluableCase


def evaluate_trace_assertion(actual, expected) -> bool:
    """Trace 断言求值（对应 LLM-as-Judge 文档第 6 步 6.2）。

    expected 支持：
    - bool            -> actual == expected
    - ">=N" / "<=N"   -> 数值比较
    - "contains:X"    -> X 包含于 str(actual)
    - "not_null"      -> actual is not None
    - 其他            -> actual == expected
    """
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, str):
        if expected == "not_null":
            return actual is not None
        if expected.startswith(">="):
            return _try_compare(actual, expected[2:], lambda a, e: a >= e)
        if expected.startswith("<="):
            return _try_compare(actual, expected[2:], lambda a, e: a <= e)
        if expected.startswith("contains:"):
            return expected[len("contains:"):] in str(actual)
    return actual == expected


def _try_compare(actual, expected_str, op) -> bool:
    """数值比较，任一方不可转数字则断言失败。"""
    try:
        return op(float(actual), float(expected_str))
    except (TypeError, ValueError):
        return False


def get_nested_value(data, field: str):
    """按点号路径从 actual_trace 提取值。找不到返回 None。"""
    from eval_core.operators import extract_by_dot_path
    return extract_by_dot_path(data, field)


class TraceEvaluator:
    """Trace 链路校验评测器"""

    async def evaluate(self, case: EvaluableCase) -> EvaluableCase:
        """校验 trace 断言 + 盲区检测。结果只记录，不影响 passed。"""
        if not isinstance(case, AgentCase):
            return case
        if case.trace is None:
            return case

        trace_data = case.result.actual_trace or {}

        # 1. 断言求值（对应流程 6.1）
        for assertion in case.trace.assertions:
            actual = get_nested_value(trace_data, assertion.field)
            if not evaluate_trace_assertion(actual, assertion.expected):
                case.result.trace_errors.append({
                    "field": assertion.field,
                    "expected": assertion.expected,
                    "actual": actual,
                })

        # 2. 盲区检测（对应流程 6.3）：期望缺失的字段在 trace 中确实为空
        for field in case.trace.missing_fields:
            actual = get_nested_value(trace_data, field)
            if actual is None:
                case.result.trace_gaps.append(field)

        # 注意：trace_errors / trace_gaps 不影响 result.passed（文档 5.3）
        return case
