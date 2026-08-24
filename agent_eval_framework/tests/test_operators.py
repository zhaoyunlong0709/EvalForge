"""操作符 + 字段提取测试。"""
from eval_core.operators import (
    evaluate_assertion, extract_by_dot_path, extract_by_jsonpath,
    extract_field, parse_dot_path,
)


class TestParseDotPath:
    def test_simple(self):
        assert parse_dot_path("a.b.c") == ["a", "b", "c"]

    def test_array_index(self):
        assert parse_dot_path("tool_calls[0].name") == ["tool_calls", 0, "name"]

    def test_nested_array(self):
        assert parse_dot_path("a[2].b[10].c") == ["a", 2, "b", 10, "c"]


class TestExtractByDotPath:
    DATA = {
        "status_code": 200,
        "body": {"status": "ok", "items": [1, 2, 3]},
        "tool_calls": [{"name": "create_reminder", "arguments": {"time": "08:00"}}],
    }

    def test_top_level(self):
        assert extract_by_dot_path(self.DATA, "status_code") == 200

    def test_nested(self):
        assert extract_by_dot_path(self.DATA, "body.status") == "ok"

    def test_array(self):
        assert extract_by_dot_path(self.DATA, "body.items[2]") == 3

    def test_array_nested_object(self):
        assert extract_by_dot_path(self.DATA, "tool_calls[0].arguments.time") == "08:00"

    def test_missing(self):
        assert extract_by_dot_path(self.DATA, "not.exist") is None
        assert extract_by_dot_path(self.DATA, "body.not.exist") is None

    def test_out_of_range(self):
        assert extract_by_dot_path(self.DATA, "body.items[99]") is None


class TestExtractByJsonPath:
    DATA = {"body": {"items": [{"id": 1}, {"id": 2}]}}

    def test_simple(self):
        assert extract_by_jsonpath(self.DATA, "$.body.items[0].id") == 1

    def test_wildcard(self):
        assert extract_by_jsonpath(self.DATA, "$.body.items[*].id") == 1

    def test_no_match(self):
        assert extract_by_jsonpath(self.DATA, "$.not.exist") is None

    def test_invalid_syntax(self):
        assert extract_by_jsonpath(self.DATA, "$$$$[") is None


class TestExtractField:
    def test_dollar_prefix_routes_to_jsonpath(self):
        assert extract_field({"a": {"b": 1}}, "$.a.b") == 1

    def test_plain_routes_to_dot_path(self):
        assert extract_field({"a": {"b": 1}}, "a.b") == 1


class TestOperators:
    def test_eq_number_loose(self):
        """数字宽松比较（200 == 200.0）。"""
        assert evaluate_assertion(200, "eq", 200.0)
        assert evaluate_assertion("200", "eq", 200)

    def test_eq_string_strict(self):
        assert evaluate_assertion("ok", "eq", "ok")
        assert not evaluate_assertion("ok", "eq", "OK")

    def test_eq_bool(self):
        """bool 遵循 Python 相等语义（True == 1 为 True，与 Python 原生一致）。"""
        assert evaluate_assertion(True, "eq", True)
        assert evaluate_assertion(True, "eq", 1)   # Python: True == 1
        assert not evaluate_assertion(True, "eq", 2)

    def test_neq(self):
        assert evaluate_assertion(200, "neq", 404)
        assert not evaluate_assertion(200, "neq", 200)

    def test_gt_gte_lt_lte(self):
        assert evaluate_assertion(150, "lt", 1000)
        assert evaluate_assertion(1000, "lte", 1000)
        assert not evaluate_assertion(150, "gt", 1000)
        assert evaluate_assertion(1000, "gte", 1000)

    def test_numeric_compare_non_number_fails(self):
        assert not evaluate_assertion("abc", "lt", 100)

    def test_contains_string(self):
        assert evaluate_assertion("hello world", "contains", "world")
        assert not evaluate_assertion("hello", "contains", "world")

    def test_contains_list(self):
        assert evaluate_assertion(["a", "b"], "contains", "a")
        assert not evaluate_assertion(["a", "b"], "contains", "c")

    def test_not_contains(self):
        assert evaluate_assertion("hello", "not_contains", "world")

    def test_regex(self):
        assert evaluate_assertion("abc123", "regex", r"^abc\d+$")
        assert not evaluate_assertion("xyz", "regex", r"^abc")

    def test_regex_invalid_pattern(self):
        assert not evaluate_assertion("x", "regex", "([invalid")

    def test_not_null(self):
        assert evaluate_assertion("x", "not_null", None)
        assert evaluate_assertion(0, "not_null", None)
        assert not evaluate_assertion(None, "not_null", None)

    def test_is_null(self):
        assert evaluate_assertion(None, "is_null", None)
        assert not evaluate_assertion("x", "is_null", None)

    def test_unknown_operator(self):
        assert not evaluate_assertion(1, "unknown_op", 1)
