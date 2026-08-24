"""MockManager：Mock 拦截层，架在 Orchestrator 和 AgentRunner 之间。

对应《Agent评测框架设计方案》4.4 执行引擎模块 + Phase 3。

四种 Mock（框架设计方案 6.4）：
  1. 完全 Mock（agent 非空）：不调 Agent，直接返回预定义 reply + trace。
     用途：Judge 校准（固定 Agent 回复只测 judge 评分）、CI 冒烟（不依赖 Agent 服务）。
  2. 记忆 Mock（memory 非空）：覆盖 setup.preloaded_memories。
     用途：注入冲突记忆（"喜欢爬山" vs "膝盖受伤"）、清空记忆。
  3. 时间 Mock（time 非空）：覆盖时间感知，注入 context.mock_time。
     用途：测纪念日、过期记忆（需要精确控制时间差）。
  4. 工具 Mock（tools 非空）：拦截工具调用，注入 context.mock_tools。
     用途：工具返回错误/异常值（天气 999 度）/延迟，测 Agent 兜底策略。

契约：Agent 系统需支持 context.mock_time 和 context.mock_tools 字段才能生效；
完全 Mock 和记忆 Mock 不需要 Agent 配合，框架侧直接生效。
"""
from eval_core.models import AgentCase, AgentSetup
from eval_core.utils import logger


class MockManager:
    """Mock 拦截器：根据 case.mock 配置拦截或修改请求。"""

    def should_mock_agent(self, case: AgentCase) -> bool:
        """判断是否完全 Mock Agent（跳过 HTTP 调用）。"""
        return case.mock is not None and case.mock.agent is not None

    def get_mock_response(self, case: AgentCase, turn_index: int) -> dict:
        """完全 Mock 时返回预定义响应。

        Args:
            case: 带 mock.agent 配置的用例
            turn_index: 当前轮次（从 0 开始）

        Returns:
            Agent API 契约格式的响应 {"reply", "trace", "usage"}

        Raises:
            IndexError: mock.replies 数量不足以覆盖当前轮次
        """
        mock = case.mock.agent
        if turn_index >= len(mock.replies):
            raise IndexError(
                f"mock.replies 只有 {len(mock.replies)} 条，"
                f"但 case 有 {len(case.conversation)} 轮对话，第 {turn_index + 1} 轮无预定义回复"
            )
        logger.debug(f"case {case.case_id} 第 {turn_index + 1} 轮使用完全 Mock")
        return {
            "reply": mock.replies[turn_index],
            "trace": dict(mock.trace),
            "usage": {},
        }

    def apply_context_mock(self, case: AgentCase) -> AgentCase:
        """应用记忆/时间/工具 Mock 到 case（在 AgentRunner 发请求前调用）。

        - memory 非空 -> 覆盖 setup.preloaded_memories
        - time 非空 / tools 非空 -> 通过 context 传递（Agent 系统需支持）
        """
        if case.mock is None:
            return case

        # 记忆 Mock：覆盖 preloaded_memories
        if case.mock.memory:
            if case.setup is None:
                case.setup = AgentSetup()
            case.setup.preloaded_memories = list(case.mock.memory)
            logger.debug(
                f"case {case.case_id} 记忆 Mock: 注入 {len(case.mock.memory)} 条"
            )

        return case

    def build_mock_context(self, case: AgentCase) -> dict:
        """构建需要传给 Agent 的 mock context 扩展字段。

        Agent 系统需支持 context.mock_time 和 context.mock_tools 才能生效。
        """
        if case.mock is None:
            return {}

        context_ext: dict = {}

        if case.mock.time:
            context_ext["mock_time"] = case.mock.time

        if case.mock.tools:
            context_ext["mock_tools"] = [
                t.model_dump() for t in case.mock.tools
            ]

        return context_ext
