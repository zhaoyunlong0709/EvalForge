"""规则操作符 + 字段提取。

对应《Agent评测框架设计方案》4.5 评测引擎模块 + 7.2 操作符扩展。

字段提取规则（文档 RuleAssertion docstring）：
- field 以 "$." 开头 -> jsonpath 提取（jsonpath-ng）
- 否则 -> 点号路径提取（如 "body.status"、"tool_calls[0].name"）

operator 只负责比较，不做字段提取（11 个纯比较操作符）。
新增操作符（文档 7.2）：需同时更新 RuleAssertion.operator 的 Literal 定义
和本模块 OPERATORS 注册表。
"""
import re
from typing import Any, Callable

from jsonpath_ng import parse as jsonpath_parse


# ============================================================
# 字段提取
# ============================================================

_ARRAY_INDEX_RE = re.compile(r"^(.+?)\[(\d+)\]$")


def parse_dot_path(path: str) -> list[Any]:
    """将点号路径解析为键/索引序列。

    Examples:
        "body.status"         -> ["body", "status"]
        "tool_calls[0].name"  -> ["tool_calls", 0, "name"]
        "a[2].b[10].c"        -> ["a", 2, "b", 10, "c"]
    """
    keys: list[Any] = []
    for part in path.split("."):
        if not part:
            continue
        # 处理形如 "name[0]" 的数组索引
        remaining = part
        while remaining:
            m = _ARRAY_INDEX_RE.match(remaining)
            if m and m.group(1):
                keys.append(m.group(1))
                keys.append(int(m.group(2)))
                remaining = ""
            elif remaining.endswith("]") and "[" in remaining:
                # 形如 "name[0]" 且 name 可能为空（如 "[0].a" 不合法，忽略）
                idx_start = remaining.index("[")
                keys.append(remaining[:idx_start])
                keys.append(int(remaining[idx_start + 1:-1]))
                remaining = ""
            else:
                keys.append(remaining)
                remaining = ""
    return keys


def extract_by_dot_path(data: Any, path: str) -> Any:
    """按点号路径从嵌套结构中提取值。找不到返回 None。"""
    current = data
    for key in parse_dot_path(path):
        if isinstance(key, int):
            if isinstance(current, list) and -len(current) <= key < len(current):
                current = current[key]
            else:
                return None
        else:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
    return current


def extract_by_jsonpath(data: Any, path: str) -> Any:
    """按 jsonpath 提取。无匹配返回 None，多匹配返回第一个。"""
    try:
        matches = jsonpath_parse(path).find(data)
        return matches[0].value if matches else None
    except Exception:  # noqa: BLE001 - 非法 jsonpath 语法视为提取失败
        return None


def extract_field(data: Any, field: str) -> Any:
    """字段提取入口：$. 前缀 -> jsonpath；否则 -> 点号路径。"""
    if field.startswith("$."):
        return extract_by_jsonpath(data, field)
    return extract_by_dot_path(data, field)


# ============================================================
# 比较操作符
# ============================================================

def _to_number(value: Any) -> float | None:
    """尝试转为数字，失败返回 None。"""
    if isinstance(value, bool):  # bool 是 int 子类，先排除
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _op_eq(actual: Any, expected: Any) -> bool:
    # 数字类型宽松比较（200 == 200.0），其余严格相等
    a_num, e_num = _to_number(actual), _to_number(expected)
    if a_num is not None and e_num is not None:
        return a_num == e_num
    return actual == expected


def _op_neq(actual: Any, expected: Any) -> bool:
    return not _op_eq(actual, expected)


def _compare_number(
    actual: Any, expected: Any, op: Callable[[float, float], bool]
) -> bool:
    """数值比较。任一方不可转数字则断言失败。"""
    a_num, e_num = _to_number(actual), _to_number(expected)
    if a_num is None or e_num is None:
        return False
    return op(a_num, e_num)


def _op_gt(actual: Any, expected: Any) -> bool:
    return _compare_number(actual, expected, lambda a, e: a > e)


def _op_gte(actual: Any, expected: Any) -> bool:
    return _compare_number(actual, expected, lambda a, e: a >= e)


def _op_lt(actual: Any, expected: Any) -> bool:
    return _compare_number(actual, expected, lambda a, e: a < e)


def _op_lte(actual: Any, expected: Any) -> bool:
    return _compare_number(actual, expected, lambda a, e: a <= e)


def _op_contains(actual: Any, expected: Any) -> bool:
    # 列表：成员包含；其余：字符串包含
    if isinstance(actual, (list, tuple)):
        return expected in actual
    return str(expected) in str(actual)


def _op_not_contains(actual: Any, expected: Any) -> bool:
    return not _op_contains(actual, expected)


def _op_regex(actual: Any, expected: Any) -> bool:
    try:
        return bool(re.search(str(expected), str(actual)))
    except re.error:
        return False


def _op_not_null(actual: Any, expected: Any) -> bool:
    return actual is not None


def _op_is_null(actual: Any, expected: Any) -> bool:
    return actual is None


# 操作符注册表（新增操作符在此注册，同时更新 RuleAssertion 的 Literal）
OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": _op_eq,
    "neq": _op_neq,
    "gt": _op_gt,
    "gte": _op_gte,
    "lt": _op_lt,
    "lte": _op_lte,
    "contains": _op_contains,
    "not_contains": _op_not_contains,
    "regex": _op_regex,
    "not_null": _op_not_null,
    "is_null": _op_is_null,
}


def evaluate_assertion(actual: Any, operator: str, expected: Any) -> bool:
    """执行单条断言比较。未知操作符返回 False。"""
    op_fn = OPERATORS.get(operator)
    if op_fn is None:
        return False
    try:
        return op_fn(actual, expected)
    except Exception:  # noqa: BLE001 - 比较异常视为断言失败
        return False
