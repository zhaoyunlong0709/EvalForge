"""执行引擎包：编排 + API 执行 + Agent 执行。

对应《Agent评测框架设计方案》4.4 执行引擎模块。
MockManager / ConcurrencyController 为 Phase 3，暂未实现。
"""
from .api_executor import APIExecutor, resolve_variables
from .agent_runner import AgentRunner
from .orchestrator import Orchestrator

__all__ = ["APIExecutor", "AgentRunner", "Orchestrator", "resolve_variables"]
