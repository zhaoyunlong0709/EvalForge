"""Judge 子系统：模板引擎 + 客户端 + 解析器 + 评测器。

对应《Agent评测框架设计方案》目录结构 evaluators/judge/。
calibration.py 为 Phase 2，暂未实现。
"""
import json
import os
from pathlib import Path

from .template_engine import TemplateEngine, TemplateError
from .response_parser import JudgeResponseError, parse_judge_response
from .judge_client import JudgeClient, JudgeResult
from .calibration import CalibrationTracker, CalibrationResult
from .evaluator import JudgeEvaluator
from eval_core.utils import logger


def create_judge_evaluator(
    prompt_templates_path: Path,
    judge_config: dict,
    cost_tracker=None,
) -> JudgeEvaluator:
    """工厂：从 prompt_templates.json 和 global.yaml judge 段构建 JudgeEvaluator。

    Args:
        prompt_templates_path: config/prompt_templates.json 路径
        judge_config: global.yaml 的 judge 段。支持任意 OpenAI 兼容端点：
            - base_url: 端点地址（如火山方舟 https://ark.cn-beijing.volces.com/api/v3），
              留空则用 OpenAI 官方
            - api_key_env: API Key 的环境变量名（如 ARK_API_KEY），
              避免密钥明文入 git；留空则由 openai SDK 读 OPENAI_API_KEY
        cost_tracker: CostTracker，可选，用于成本统计
    """
    templates_data = json.loads(prompt_templates_path.read_text(encoding="utf-8"))
    templates = templates_data.get("prompt_templates", templates_data)

    # API Key 从环境变量读取（api_key_env 指定变量名）
    api_key_env = judge_config.get("api_key_env")
    api_key = None
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            logger.warning(
                f"环境变量 {api_key_env} 未设置，Judge 调用将失败"
                "（纯规则类评测不受影响）"
            )

    base_url = judge_config.get("base_url")
    if base_url:
        logger.info(f"Judge 端点: {base_url} (model: {judge_config['model']})")

    # Judge 速率限制（可选，rate_limit_rpm 如 60 = 每分钟最多 60 个请求）
    from eval_core.utils import RateLimiter
    judge_rpm = judge_config.get("rate_limit_rpm")
    judge_rate_limiter = RateLimiter.from_rpm(judge_rpm) if judge_rpm else None

    engine = TemplateEngine(templates)
    client = JudgeClient(
        model=judge_config["model"],
        temperature=judge_config.get("temperature", 0),
        max_tokens=judge_config.get("max_tokens", 500),
        retry_count=judge_config.get("retry_count", 3),
        retry_delay_ms=judge_config.get("retry_delay_ms", 1000),
        api_key=api_key,
        base_url=base_url,
        cost_tracker=cost_tracker,
        rate_limiter=judge_rate_limiter,
    )
    return JudgeEvaluator(engine, client)


__all__ = [
    "TemplateEngine",
    "TemplateError",
    "JudgeResponseError",
    "parse_judge_response",
    "JudgeClient",
    "CalibrationTracker",
    "CalibrationResult",
    "JudgeResult",
    "JudgeEvaluator",
    "create_judge_evaluator",
]
