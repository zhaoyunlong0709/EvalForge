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

    支持单模型和多模型（judge.models 数组）配置，多模型时评分取加权平均。

    Args:
        prompt_templates_path: config/prompt_templates.json 路径
        judge_config: global.yaml 的 judge 段
        cost_tracker: CostTracker，可选
    """
    templates_data = json.loads(prompt_templates_path.read_text(encoding="utf-8"))
    templates = templates_data.get("prompt_templates", templates_data)
    engine = TemplateEngine(templates)

    # 多模型配置
    models = judge_config.get("models")
    if models:
        clients = [_build_judge_client(m, cost_tracker) for m in models]
        logger.info(f"Judge 多模型模式: {len(clients)} 个模型")
        return JudgeEvaluator(engine, clients)

    # 单模型配置（兼容旧版）
    return JudgeEvaluator(engine, [_build_judge_client(judge_config, cost_tracker)])


def _build_judge_client(judge_config: dict, cost_tracker=None) -> JudgeClient:
    """构建单个 JudgeClient。"""
    api_key_env = judge_config.get("api_key_env")
    api_key = None
    if api_key_env:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            logger.warning(
                f"环境变量 {api_key_env} 未设置，Judge 调用将失败"
            )

    base_url = judge_config.get("base_url")
    if base_url:
        logger.info(f"Judge 端点: {base_url} (model: {judge_config['model']})")

    from eval_core.utils import RateLimiter
    judge_rpm = judge_config.get("rate_limit_rpm")
    judge_rate_limiter = RateLimiter.from_rpm(judge_rpm) if judge_rpm else None

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
    client.weight = judge_config.get("weight", 1.0)  # 权重
    return client


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
