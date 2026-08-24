"""日志配置，基于 loguru。

对应《Agent评测框架设计方案》技术栈表"日志"层。
"""
import sys

from loguru import logger


def setup_logger(level: str = "INFO") -> None:
    """初始化框架日志。CLI 启动时调用一次。

    Args:
        level: 日志级别，默认 INFO。调试时可传 DEBUG。
    """
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <7}</level> | "
            "{message}"
        ),
    )
