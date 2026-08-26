"""Orchestrator：编排整个评测流程，根据 case_type 分发到执行器。

对应《Agent评测框架设计方案》4.4 + 6.1 评测执行流程。

流程（文档 6.1）：
  对每个 case：
    1. 执行（APICase -> APIExecutor；AgentCase -> AgentRunner）
    2. 评测（EvaluatorPipeline 路由主/附加评测器）

并发模式（--concurrency N，N > 1）：
  asyncio.gather + Semaphore 并发执行多个 case。
  结果顺序与输入一致（gather 保序），RateLimiter 控制 API 请求速率。
  N = 1 时走原有串行逻辑，行为不变。

PassK 模式（--pass-k K）：
  对每个 case 循环执行 K 次（执行+评测），过 pass_threshold 次算通过。
  用于诊断 flaky case（时好时坏的 case）和 P0 case 发版前加保险。
  普通模式（pass_k=1）走原有逻辑，不引入任何额外开销。
"""
import asyncio

from eval_core.models import AgentCase, APICase, CaseResult, EvaluableCase
from eval_core.utils import logger

from ..evaluators.pipeline import EvaluatorPipeline
from .agent_runner import AgentRunner
from .api_executor import APIExecutor


class Orchestrator:
    """评测编排器（支持串行/并发）"""

    def __init__(
        self,
        api_executor: APIExecutor,
        agent_runner: AgentRunner,
        evaluator_pipeline: EvaluatorPipeline,
        pass_k: int = 1,
        pass_threshold: int = 1,
        concurrency: int = 1,
    ):
        self.api_executor = api_executor
        self.agent_runner = agent_runner
        self.evaluator_pipeline = evaluator_pipeline
        self.pass_k = max(1, pass_k)
        self.pass_threshold = max(1, min(pass_threshold, self.pass_k))
        self.concurrency = max(1, concurrency)
        self._semaphore: asyncio.Semaphore | None = None
        if self.concurrency > 1:
            self._semaphore = asyncio.Semaphore(self.concurrency)

    async def run(self, cases: list[EvaluableCase]) -> list[EvaluableCase]:
        """执行所有 case。concurrency=1 串行；>1 并发（gather 保序）。
        有 depends_on 的 case 自动排序，被依赖的 case 先执行。"""
        cases = self._resolve_dependencies(cases)
        if self.concurrency > 1:
            return await self._run_concurrent(cases)
        return await self._run_serial(cases)

    def _resolve_dependencies(self, cases: list[EvaluableCase]) -> list[EvaluableCase]:
        """按 depends_on 字段排序：被依赖的 case 先执行。"""
        # 构建 case_id -> case 的映射
        case_map = {c.case_id: c for c in cases}
        # 拓扑排序
        visited: set[str] = set()
        ordered: list[EvaluableCase] = []

        def visit(case: EvaluableCase):
            dep_id = getattr(case, "depends_on", "") or ""
            if dep_id and dep_id in case_map and dep_id not in visited:
                visit(case_map[dep_id])
            if case.case_id not in visited:
                visited.add(case.case_id)
                ordered.append(case)

        for c in cases:
            if c.case_id not in visited:
                visit(c)

        return ordered

    async def _run_serial(self, cases: list[EvaluableCase]) -> list[EvaluableCase]:
        """串行执行（原有逻辑，默认模式）。"""
        results = []
        for i, case in enumerate(cases, 1):
            logger.info(f"[{i}/{len(cases)}] 执行 {case.case_id} ({case.title})")
            case = await self._process_case(case)
            status = "✅ 通过" if case.result.passed else "❌ 失败"
            logger.info(f"[{i}/{len(cases)}] {case.case_id} {status}")
            results.append(case)
        return results

    async def _run_concurrent(self, cases: list[EvaluableCase]) -> list[EvaluableCase]:
        """并发执行：Semaphore 限流，gather 保序。"""
        total = len(cases)
        completed = 0

        async def _process_with_semaphore(case: EvaluableCase) -> EvaluableCase:
            nonlocal completed
            async with self._semaphore:  # type: ignore[union-attr]
                case = await self._process_case(case)
                completed += 1
                status = "✅ 通过" if case.result.passed else "❌ 失败"
                logger.info(f"[{completed}/{total}] {case.case_id} {status}")
                return case

        logger.info(f"并发模式启动: {self.concurrency} 个 case 同时执行")
        results = await asyncio.gather(*[_process_with_semaphore(c) for c in cases])
        return list(results)

    async def _process_case(self, case: EvaluableCase) -> EvaluableCase:
        """单个 case 的完整流程：执行 -> 评测（含 PassK 分支和异常兜底）。"""
        try:
            if self.pass_k > 1:
                case = await self._run_with_pass_k(case)
            else:
                case = await self._execute(case)
                case = await self.evaluator_pipeline.evaluate(case)
        except Exception as e:  # noqa: BLE001 - 单个 case 异常不中断整体
            case.result.passed = False
            case.result.error_analysis = f"框架内部错误: {e}"
            logger.error(f"case {case.case_id} 处理异常: {e}")
        return case

    async def _run_with_pass_k(self, case: EvaluableCase) -> EvaluableCase:
        """PassK 模式：循环执行+评测 k 次，多数表决。"""
        individual_results: list[bool] = []
        last_case = case

        for run_idx in range(1, self.pass_k + 1):
            # 每次运行前重置 result（复用同一个 case 对象）
            case.result = CaseResult()
            try:
                last_case = await self._execute(case)
                last_case = await self.evaluator_pipeline.evaluate(last_case)
            except Exception as e:  # noqa: BLE001
                last_case.result.passed = False
                last_case.result.error_analysis = f"PassK 第 {run_idx} 轮执行异常: {e}"

            individual_results.append(last_case.result.passed)
            logger.debug(
                f"case {case.case_id} PassK {run_idx}/{self.pass_k}: "
                f"{'✅' if last_case.result.passed else '❌'}"
            )

        pass_count = sum(individual_results)
        final_passed = pass_count >= self.pass_threshold

        # 填充 pass_k_detail 和最终结果
        last_case.result.passed = final_passed
        last_case.result.pass_k_detail = {
            "k": self.pass_k,
            "pass_count": pass_count,
            "pass_threshold": self.pass_threshold,
            "individual_results": individual_results,
        }
        if not final_passed and not last_case.result.error_analysis:
            last_case.result.error_analysis = (
                f"PassK: {pass_count}/{self.pass_k} 未达阈值 {self.pass_threshold}"
            )

        logger.info(
            f"case {case.case_id} PassK 结果: {pass_count}/{self.pass_k} "
            f"(阈值 {self.pass_threshold}) -> {'✅' if final_passed else '❌'}"
        )
        return last_case

    async def _execute(self, case: EvaluableCase) -> EvaluableCase:
        """根据 case_type 分发到对应执行器（文档 4.4 match 分发）。"""
        if isinstance(case, APICase):
            return await self.api_executor.execute(case)
        if isinstance(case, AgentCase):
            return await self.agent_runner.execute(case)
        raise ValueError(f"未知的 case 类型: {type(case).__name__}")
