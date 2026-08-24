"""Judge 响应解析：JSON 解析 -> score 范围校验 -> 维度完整性检查。

对应《LLM-as-Judge评测执行流程》第 4 步（4.2-4.4 校验逻辑）。
"""
import json
import re


class JudgeResponseError(Exception):
    """Judge 返回内容无法通过校验"""


def parse_judge_response(raw: str, expected_dimensions: list[str] | None = None) -> dict:
    """解析并校验 Judge 返回内容。

    Returns:
        {"score": float, "reason": str, "dimension_scores": dict}

    Raises:
        JudgeResponseError: 非 JSON / score 缺失或越界 / dimension_scores 缺失
    """
    parsed = _extract_json(raw)

    # 校验 score（对应流程 4.3）
    score = parsed.get("score")
    if score is None or not isinstance(score, (int, float)) or not (0 <= score <= 5):
        raise JudgeResponseError(f"score 值异常: {score!r}")

    # 校验 dimension_scores（对应流程 4.4）
    dimension_scores = parsed.get("dimension_scores")
    if dimension_scores is None or not isinstance(dimension_scores, dict):
        raise JudgeResponseError(
            f"dimension_scores 缺失或类型异常: {dimension_scores!r}"
        )

    # 期望维度缺失检查：作为警告记录在返回值中（不阻塞重试。
    # 说明：LLM-as-Judge 流程文档 4.4 原为重试条件，但模板输出维度由模板
    # 自身定义，与 case.dimensions 可能不完全一致（如 C001 含 D2.情绪识别
    # 但共情评估模板只输出 D8.共情适当率），严格校验会误杀合法用例，
    # 故降级为警告。）
    missing_dims = []
    if expected_dimensions:
        missing_dims = [d for d in expected_dimensions if d not in dimension_scores]

    return {
        "score": float(score),
        "reason": str(parsed.get("reason", "")),
        "dimension_scores": {k: float(v) for k, v in dimension_scores.items()
                             if isinstance(v, (int, float))},
        "missing_dimensions": missing_dims,  # 警告信息，由调用方记录日志
    }


def _extract_json(raw: str) -> dict:
    """从 LLM 返回文本中提取 JSON。

    依次尝试：直接解析 -> 剥离 markdown 代码块 -> 提取首个 {..} 平衡块。
    """
    text = raw.strip()

    # 1. 直接解析
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 2. 剥离 ```json ... ``` 或 ``` ... ``` 代码块
    code_block = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if code_block:
        try:
            result = json.loads(code_block.group(1))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # 3. 提取首个平衡的花括号块
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        result = json.loads(text[start:i + 1])
                        if isinstance(result, dict):
                            return result
                    except json.JSONDecodeError:
                        break
                    break
        start = text.find("{", start + 1)

    raise JudgeResponseError(f"Judge 返回非 JSON 格式: {text[:100]}...")
