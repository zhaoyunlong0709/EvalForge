"""DependencySnapshotRecorder：每次评测记录依赖快照。

对应《Agent评测框架设计方案》4.6 可复现性模块。
记录 Agent 模型版本、Judge 模型版本、温度等配置，
用于评测结果的可复现性声明。

可复现性由 temperature=0 保证，不需要 seed 参数。
"""
from datetime import datetime, timezone


class DependencySnapshotRecorder:
    """依赖快照记录器"""

    @staticmethod
    def record(
        agent_config: dict,
        judge_config: dict,
        case_count: int,
        framework_version: str = "",
    ) -> dict:
        """生成依赖快照（随报告输出）。

        Args:
            agent_config: global.yaml 的 agent 段
            judge_config: global.yaml 的 judge 段
            case_count: 本次评测的用例数
        """
        return {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "framework_version": framework_version,
            "agent": {
                "endpoint": agent_config.get("endpoint", ""),
                "temperature": agent_config.get("temperature"),
            },
            "judge": {
                "model": judge_config.get("model", ""),
                "temperature": judge_config.get("temperature"),
            },
            "case_count": case_count,
        }
