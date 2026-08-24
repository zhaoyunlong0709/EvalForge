"""数据源适配器基类 + RawCase 数据模型。

对应《Agent评测框架设计方案》4.3 用例搜集模块。

设计原则：
  - 只定义接口，不实现具体数据源
  - 未来接入真实数据源（日志/反馈/数据库/监控）时，实现 BaseCaseSource 即可
  - 搜集管道通过 BaseCaseSource 统一调用，不感知具体数据源类型
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RawCase:
    """从数据源拉取的原始 case（未格式化、未分类）"""

    source_type: str                    # 数据源类型标识（如 "chatlog" / "feedback"）
    source_id: str                      # 数据源内的唯一 ID（用于溯源）
    raw_conversation: list[dict]        # 原始对话 [{"role": "user", "content": "..."}, ...]
    failure_signal: str = ""            # 失败信号描述（如 "用户点踩" / "用户重新提问"）
    metadata: dict[str, Any] = field(default_factory=dict)  # 数据源附带的元信息

    @property
    def user_messages(self) -> list[str]:
        """提取所有用户消息（用于去重和分类）"""
        return [
            m["content"] for m in self.raw_conversation
            if m.get("role") == "user"
        ]


class BaseCaseSource(ABC):
    """数据源适配器接口。

    所有数据源（日志/反馈/数据库/监控）实现此接口后，
    注册到 CollectionPipeline 即可接入搜集流程。

    实现示例（未来接入真实数据源时）：
        class ChatLogSource(BaseCaseSource):
            async def fetch(self, since, limit):
                # 调用日志 API，解析为 RawCase 列表
                ...
    """

    @abstractmethod
    async def fetch(self, since: datetime, limit: int = 100) -> list[RawCase]:
        """从数据源拉取指定时间范围内的原始 case。

        Args:
            since: 拉取起始时间
            limit: 最多拉取条数

        Returns:
            RawCase 列表
        """
        ...

    @abstractmethod
    def source_type(self) -> str:
        """返回数据源类型标识（用于日志和溯源）"""
        ...


class ManualSource(BaseCaseSource):
    """手动导入源：用户粘贴对话内容。

    当前唯一可用的数据源。不依赖外部系统，
    用于测试工程师手动将线上 bad case 转化为标准用例。
    """

    def __init__(self):
        self._pending: list[RawCase] = []

    def add_conversation(
        self,
        conversation: list[dict],
        failure_signal: str = "",
        metadata: dict | None = None,
    ) -> RawCase:
        """手动添加一段对话。

        Args:
            conversation: 对话列表 [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
            failure_signal: 失败信号（如 "用户反馈推荐了不该推荐的东西"）
            metadata: 附加信息

        Returns:
            创建的 RawCase
        """
        raw = RawCase(
            source_type="manual",
            source_id=f"manual_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
            raw_conversation=conversation,
            failure_signal=failure_signal,
            metadata=metadata or {},
        )
        self._pending.append(raw)
        return raw

    async def fetch(self, since: datetime, limit: int = 100) -> list[RawCase]:
        """取出所有待处理的手动 case（一次性消费）。"""
        result = self._pending[:limit]
        self._pending = self._pending[limit:]
        return result

    def source_type(self) -> str:
        return "manual"
