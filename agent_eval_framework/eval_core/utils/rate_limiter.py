"""RateLimiter：令牌桶算法，控制 API 请求速率。

对应《Agent评测框架设计方案》4.4 执行引擎 - 速率限制。

原理：
  桶里最多 burst 个令牌，每秒补充 rate 个。
  每次请求前从桶里取 1 个令牌，桶空则等待。

效果：允许短时突发（burst），但平均速率不超过 rate。
适用：Agent API / Judge API 有 RPM/RPS 限制时防止被限流。
"""
import asyncio
import time

from eval_core.utils import logger


class RateLimiter:
    """异步令牌桶限流器"""

    def __init__(self, rate: float, burst: int | None = None):
        """初始化令牌桶。

        Args:
            rate: 每秒补充的令牌数（如 10 = 10 req/s；0.5 = 30 req/min）
            burst: 桶容量（允许的突发量），默认等于 rate 取整（至少 1）
        """
        if rate <= 0:
            raise ValueError(f"rate 必须大于 0，当前: {rate}")
        self.rate = float(rate)
        self.burst = burst if burst is not None else max(1, int(rate))
        self.tokens = float(self.burst)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """获取一个令牌。桶空时等待（不抛异常）。"""
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
                self.last_refill = now

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return

                # 令牌不足，计算等待时间
                wait_time = (1.0 - self.tokens) / self.rate
                logger.debug(f"RateLimiter 等待 {wait_time:.3f}s（rate={self.rate}/s）")
                await asyncio.sleep(wait_time)

    @staticmethod
    def from_rpm(rpm: int, burst: int | None = None) -> "RateLimiter":
        """从每分钟请求数创建（Judge API 常用 RPM 而非 RPS）。

        Args:
            rpm: 每分钟允许的请求数（如 60 = 60 req/min = 1 req/s）
            burst: 桶容量
        """
        return RateLimiter(rate=rpm / 60.0, burst=burst)
