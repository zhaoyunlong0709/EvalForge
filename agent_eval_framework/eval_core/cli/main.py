"""CLI 入口：eval-forge run ...

对应《Agent评测框架设计方案》第 3 节 CLI 入口层。
用法：
  eval-forge run                            # 全量运行 cases/ 下所有用例
  eval-forge run --case-dir cases/agent     # 运行指定目录
  eval-forge run --case cases/api/API_001.json   # 运行指定文件
  eval-forge run --tags smoke               # 按 tags 筛选（L1 冒烟）
  eval-forge run --priority P0 --priority P1     # 按优先级筛选（L2 回归）
  eval-forge run --dimension "D9.上下文记忆"     # 按维度筛选

退出码：0=全部通过；1=有失败用例（供 CI 阻塞判断，文档第 10 节）。
"""
import asyncio
import sys
from pathlib import Path

import click
import yaml

from eval_core.utils import setup_logger


@click.group()
def cli() -> None:
    """EvalForge - Agent 评测框架（支持 Agent 评测与原生 API 测试）"""


@cli.command()
@click.option(
    "--case", "case_files", multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="指定用例文件路径（可多次）",
)
@click.option(
    "--case-dir", "case_dirs", multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="指定用例目录（可多次）",
)
@click.option("--tags", multiple=True, help="按 tags 筛选（OR 语义）")
@click.option(
    "--priority", "priorities", multiple=True,
    type=click.Choice(["P0", "P1", "P2", "P3"]),
    help="按优先级筛选（OR 语义，可多次）",
)
@click.option("--dimension", "dimensions", multiple=True, help="按维度筛选（OR 语义）")
@click.option(
    "--config", "config_path", default="config/global.yaml",
    type=click.Path(exists=True, path_type=Path),
    help="全局配置文件路径（默认 config/global.yaml）",
)
@click.option(
    "--cases-dir", default="cases", type=click.Path(file_okay=False, path_type=Path),
    help="用例根目录（默认 cases/）",
)
@click.option(
    "--profiles-dir", default="profiles", type=click.Path(file_okay=False, path_type=Path),
    help="profile 根目录（默认 profiles/）",
)
@click.option(
    "--report-dir", default="reports", type=click.Path(file_okay=False, path_type=Path),
    help="报告输出目录（默认 reports/）",
)
@click.option(
    "--pass-k", "pass_k", default=1, type=int,
    help="PassK 模式：每个 case 执行 K 次取多数（默认 1=普通模式）。用于诊断 flaky case",
)
@click.option(
    "--pass-threshold", "pass_threshold", default=1, type=int,
    help="PassK 通过阈值：K 次中至少过 N 次算通过（默认 1，仅在 --pass-k > 1 时生效）",
)
@click.option(
    "--concurrency", default=1, type=int,
    help="并发执行数（默认 1=串行）。配合 API 速率限制使用，如 --concurrency 5",
)
@click.option(
    "--save-baseline", "save_baseline", default=None, metavar="VERSION",
    help="发版后保存本次结果为 baseline（如 --save-baseline v0.3）",
)
@click.option(
    "--force", "force_save", is_flag=True,
    help="保存 baseline 时忽略退化防护（明知更差仍强制保存）",
)
@click.option(
    "--baselines-dir", default="baselines", type=click.Path(file_okay=False, path_type=Path),
    help="baseline 存储目录（默认 baselines/）",
)
@click.option(
    "--dry-run", "dry_run", is_flag=True,
    help="只校验用例格式，不执行评测。用于用例开发阶段快速检查",
)
@click.option("--log-level", default="INFO", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]))
@click.option(
    "--mock", "mock_mode", is_flag=True,
    help="Mock 模式：不调真实 Agent 和 Judge API，使用 golden 标注作为评分依据",
)
def run(
    case_files, case_dirs, tags, priorities, dimensions,
    config_path, cases_dir, profiles_dir, report_dir,
    pass_k, pass_threshold, concurrency, save_baseline, force_save, baselines_dir,
    dry_run, log_level,
    mock_mode,
) -> None:
    """运行评测：加载用例 -> 执行 -> 评测 -> 报告"""
    setup_logger(log_level)
    try:
        exit_code = asyncio.run(
            _run(
                case_files=list(case_files) or None,
                case_dirs=list(case_dirs) or None,
                tags=list(tags) or None,
                priorities=list(priorities) or None,
                dimensions=list(dimensions) or None,
                config_path=config_path,
                cases_dir=cases_dir,
                profiles_dir=profiles_dir,
                report_dir=report_dir,
                pass_k=pass_k,
                pass_threshold=pass_threshold,
                concurrency=concurrency,
                save_baseline=save_baseline,
                force_save=force_save,
                baselines_dir=baselines_dir,
                dry_run=dry_run,
                mock_mode=mock_mode,
            )
        )
    except Exception as e:  # noqa: BLE001 - CLI 兜底，任何异常返回退出码 1
        from eval_core.utils import logger
        logger.error(f"评测执行异常: {e}")
        exit_code = 1
    sys.exit(exit_code)


async def _run(
    case_files, case_dirs, tags, priorities, dimensions,
    config_path: Path, cases_dir: Path, profiles_dir: Path, report_dir: Path,
    pass_k: int = 1, pass_threshold: int = 1, concurrency: int = 1,
    save_baseline: str | None = None, force_save: bool = False,
    baselines_dir: Path | None = None,
    dry_run: bool = False, mock_mode: bool = False,
) -> int:
    """执行完整评测流程，返回退出码（0=全部通过，1=有失败）。"""
    from eval_core.evaluators import EvaluatorPipeline, JudgeEvaluator, TemplateEngine, create_judge_evaluator
    from eval_core.loader import CaseDiscoverer, CaseFilter, create_loader_registry
    from eval_core.report import ReportBuilder
    from eval_core.result import (
        CostTracker, DependencySnapshotRecorder, ResultAggregator,
    )
    from eval_core.runner import APIExecutor, AgentRunner, Orchestrator
    from eval_core.utils import logger

    # ---- 1. 加载配置 ----
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error(f"配置文件不存在: {config_path}")
        return 1

    agent_config = config.get("agent", {})
    judge_config = config.get("judge", {})
    pricing = config.get("pricing", {})
    api_retry = config.get("api_retry", {})

    # ---- 2. 加载用例 ----
    registry = create_loader_registry(profiles_dir)
    discoverer = CaseDiscoverer(registry)
    cases, load_errors = discoverer.load_all(cases_dir, case_files, case_dirs)
    if load_errors:
        for e in load_errors:
            logger.warning(f"跳过: {e['file']}: {e['error'][:80]}")

    # ---- 3. 筛选 ----
    cases = CaseFilter.filter(
        cases, tags=tags, priorities=priorities, dimensions=dimensions
    )
    if not cases:
        logger.warning("没有匹配的用例（检查 --tags/--priority/--dimension 筛选条件）")
        return 0

    logger.info(f"待评测用例: {len(cases)} 个"
                + (f"（另有 {len(load_errors)} 个加载失败已跳过）" if load_errors else ""))

    # ---- 3.5 Dry-Run：只校验不执行 ----
    if dry_run:
        from eval_core.utils import logger as _logger
        _logger.info("🔍 Dry-Run 模式：只校验用例格式，不执行评测")
        print()
        print("=" * 60)
        print("Dry-Run 校验结果")
        print("=" * 60)

        # 校验通过的
        for case in cases:
            print(f"  ✅ {case.case_id}: {case.title}")

        # 校验失败的
        if load_errors:
            print()
            for e in load_errors:
                print(f"  ❌ {e['file']}: {e['error'][:80]}")

        # 检查可选 section
        print()
        print(f"总计: {len(cases)} 个通过校验, {len(load_errors)} 个失败")
        from eval_core.models import AgentCase
        has_judge = sum(1 for c in cases
                        if getattr(c, 'evaluation', None)
                        and c.evaluation.method == 'llm_judge')
        has_trace = sum(1 for c in cases
                        if isinstance(c, AgentCase) and c.trace is not None)
        has_golden = sum(1 for c in cases
                         if isinstance(c, AgentCase) and c.golden is not None)
        has_mock = sum(1 for c in cases
                       if isinstance(c, AgentCase) and c.mock is not None)
        print(f"  - llm_judge 用例: {has_judge} 个（需要 Judge 配置）")
        print(f"  - 含 trace 断言: {has_trace} 个")
        print(f"  - 含 golden 标注: {has_golden} 个")
        print(f"  - 含 mock 配置: {has_mock} 个")

        return 1 if load_errors else 0

    # ---- 4. 组装组件 ----
    cost_tracker = CostTracker(pricing)

    api_executor = APIExecutor(
        max_attempts=api_retry.get("max_attempts", 3),
        wait_min_ms=api_retry.get("wait_min_ms", 1000),
        wait_max_ms=api_retry.get("wait_max_ms", 10000),
    )
    # Agent 速率限制（可选）
    from eval_core.utils import RateLimiter
    agent_rate = agent_config.get("rate_limit_rps")
    agent_rate_limiter = RateLimiter(rate=agent_rate) if agent_rate else None

    agent_runner = AgentRunner(
        endpoint=agent_config.get("endpoint", ""),
        timeout_ms=agent_config.get("timeout_ms", 30000),
        temperature=agent_config.get("temperature", 0),
        max_attempts=api_retry.get("max_attempts", 3),
        wait_min_ms=api_retry.get("wait_min_ms", 1000),
        wait_max_ms=api_retry.get("wait_max_ms", 10000),
        cost_tracker=cost_tracker,
        rate_limiter=agent_rate_limiter,
    )
    judge_evaluator = create_judge_evaluator(
        prompt_templates_path=config_path.parent / "prompt_templates.json",
        judge_config=judge_config,
        cost_tracker=cost_tracker,
    )

    # ---- 4.5 Mock 模式：替换 Agent 和 Judge 为 Mock 实现 ----
    if mock_mode:
        import httpx
        from eval_core.evaluators.judge.judge_client import JudgeClient, JudgeResult

        logger.info("🔧 Mock 模式：跳过真实 Agent 和 Judge API")

        # Mock Agent：返回通用回复
        agent_runner = AgentRunner(
            endpoint="https://mock-agent.local",
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json={
                    "reply": "Mock Agent 回复",
                    "trace": {"session_id": "mock"},
                    "usage": {},
                })
            ),
            cost_tracker=cost_tracker,
        )

        # Mock Judge：根据 golden 标注返回分数
        class MockJudgeClientCLI(JudgeClient):
            def __init__(self, cases):
                super().__init__(model="mock-judge", api_key="mock")
                self._idx = 0
                self._scores = self._prepare_scores(cases)

            @staticmethod
            def _prepare_scores(cases):
                scores = []
                for c in cases:
                    if hasattr(c, "golden") and c.golden and c.golden.overall_score > 0:
                        s = float(c.golden.overall_score)
                        dims = c.golden.dimension_scores
                        reason = "Mock: golden 标注"
                    else:
                        s = 0.0
                        dims = {}
                        reason = "Mock: 无 golden 标注或标注为 0"
                    scores.append((s, dims, reason))
                return scores

            async def score(self, prompt, expected_dimensions=None):
                if self._idx >= len(self._scores):
                    return JudgeResult(error="Mock 评分已耗尽", model=self.model)
                s, dims, reason = self._scores[self._idx]
                self._idx += 1
                return JudgeResult(score=s, reason=reason, dimension_scores=dims, model=self.model)

        with open(config_path.parent / "prompt_templates.json") as f:
            import json as _json
            templates = _json.load(f)["prompt_templates"]
        mock_judge_evaluator = JudgeEvaluator(
            TemplateEngine(templates), MockJudgeClientCLI(cases)
        )
        judge_evaluator = mock_judge_evaluator

    pipeline = EvaluatorPipeline(judge_evaluator=judge_evaluator)
    orchestrator = Orchestrator(
        api_executor, agent_runner, pipeline,
        pass_k=pass_k, pass_threshold=pass_threshold,
        concurrency=concurrency,
    )
    if pass_k > 1:
        logger.info(f"PassK 模式启用: 每个用例执行 {pass_k} 次，"
                    f"过 {pass_threshold} 次算通过")

    # ---- 5. 执行 ----
    results = await orchestrator.run(cases)

    # ---- 6. 聚合 + 快照 + 报告 ----
    aggregated = ResultAggregator.aggregate(results, load_errors)
    snapshot = DependencySnapshotRecorder.record(
        agent_config=agent_config,
        judge_config=judge_config,
        case_count=len(results),
    )
    # ---- 6.5 Baseline 对比 ----
    baseline_comparison = None
    if baselines_dir:
        from eval_core.result import BaselineManager
        bm = BaselineManager(baselines_dir)
        baseline_comparison = bm.compare(aggregated, results)
        if baseline_comparison:
            logger.info(
                f"Baseline 对比 (vs {baseline_comparison['baseline_version']}): "
                f"通过率 {baseline_comparison['overall']['baseline_pass_rate']:.1%} -> "
                f"{baseline_comparison['overall']['current_pass_rate']:.1%} "
                f"({baseline_comparison['overall']['direction']})"
            )
            if baseline_comparison["regressions"]:
                for r in baseline_comparison["regressions"]:
                    logger.warning(f"  退化: {r['case_id']} - {r['error']}")

    # ---- 6.6 保存 Baseline（发版时，手动触发 + 退化防护） ----
    if save_baseline and baselines_dir:
        from eval_core.result import BaselineManager
        bm = BaselineManager(baselines_dir)
        block_reason = _check_baseline_save(baseline_comparison, aggregated)
        if block_reason and not force_save:
            logger.error(f"拒绝保存 baseline ({save_baseline}): {block_reason}")
            logger.error("如需强制保存，请加 --force")
        else:
            if block_reason:
                logger.warning(f"⚠️ 检测到阻塞原因但 --force 生效，强制保存: {block_reason}")
            bm.save(save_baseline, aggregated, results)

    builder = ReportBuilder(report_dir)
    json_path = builder.generate_all(
        results, aggregated,
        cost_summary=cost_tracker.summary(),
        snapshot=snapshot,
        baseline_comparison=baseline_comparison,
    )
    logger.info(f"JSON 报告已输出: {json_path}")

    # ---- 7. 退出码 ----
    return 1 if aggregated["total"]["failed"] > 0 else 0


def _check_baseline_save(
    baseline_comparison: dict | None = None,
    aggregated: dict | None = None,
) -> str | None:
    """保存 baseline 前的防护检查。

    两层检查：
      1. 绝对红线：当前运行存在 P0/P1 失败（与 baseline 无关）
         P0=安全红线，P1=基本功能，两者都是阻塞发版点
      2. 相对退化：整体通过率下降 / 非 P0/P1 case 退化（需 baseline 对比数据）

    Returns:
        阻塞原因（None = 允许保存）。
    """
    reasons: list[str] = []

    # 1. 绝对红线：当前 P0/P1 失败（永远禁止存为 baseline，与 baseline 无关）
    if aggregated:
        failures = aggregated.get("failures", [])
        p0_failures = [f for f in failures if f.get("priority") == "P0"]
        p1_failures = [f for f in failures if f.get("priority") == "P1"]
        if p0_failures:
            ids = ", ".join(f["case_id"] for f in p0_failures)
            reasons.append(f"P0 用例失败（安全红线，阻塞发版）: {ids}")
        if p1_failures:
            ids = ", ".join(f["case_id"] for f in p1_failures)
            reasons.append(f"P1 用例失败（基本功能，阻塞发版）: {ids}")

    # 2. 相对退化（需 baseline 对比数据）
    if baseline_comparison:
        overall = baseline_comparison.get("overall", {})
        if overall.get("direction") == "degraded":
            reasons.append(
                f"整体通过率下降 "
                f"{overall.get('baseline_pass_rate', 0):.1%} -> "
                f"{overall.get('current_pass_rate', 0):.1%}"
            )

        regressions = baseline_comparison.get("regressions", [])
        non_critical_regressions = [
            r for r in regressions if r.get("priority") not in ("P0", "P1")
        ]
        if non_critical_regressions:
            ids = ", ".join(r["case_id"] for r in non_critical_regressions)
            reasons.append(f"非 P0/P1 用例退化: {ids}")

    if not reasons:
        return None
    return "；".join(reasons)




# ==================== collect 子命令组 ====================

@cli.group()
def collect() -> None:
    """用例搜集：粘贴对话 -> 自动分类 -> 审核 -> 写入用例集"""


@collect.command()
@click.option(
    "--review-db", default="review_queue.db",
    type=click.Path(path_type=Path),
    help="审核队列数据库路径（默认 review_queue.db）",
)
@click.option(
    "--output-dir", default="cases/agent/auto_collected",
    type=click.Path(file_okay=False, path_type=Path),
    help="审核通过的 case 存放目录",
)
def interactive(review_db, output_dir) -> None:
    """交互式粘贴对话，自动生成 case 草稿"""
    setup_logger("INFO")
    click.echo("=== 用例搜集（交互模式）===")
    click.echo("粘贴对话内容，每行一条，格式：user: xxx 或 assistant: xxx")
    click.echo("输入空行结束对话，输入 q 结束搜集\n")

    from eval_core.collector import ManualSource
    from eval_core.collector import CaseClassifier, CaseFormatter, CaseDeduplicator, ReviewQueue

    source = ManualSource()

    while True:
        conversation = []
        click.echo("--- 新对话（直接回车结束）---")
        while True:
            try:
                line = input().strip()
            except EOFError:
                line = "q"
            if not line:
                break
            if line == "q":
                if conversation:
                    source.add_conversation(conversation, failure_signal="")
                    click.echo("✅ 对话已添加\n")
                _run_collect_pipeline(source, review_db, output_dir)
                return
            if line.startswith("user:"):
                conversation.append({"role": "user", "content": line[5:].strip()})
            elif line.startswith("assistant:"):
                conversation.append({"role": "assistant", "content": line[10:].strip()})
            else:
                click.echo(f"  ⚠️ 忽略无法识别的行: {line[:30]}")

        if conversation:
            signal = input("失败信号（如：用户反馈推荐了不该推荐的东西，直接回车跳过）: ").strip()
            source.add_conversation(conversation, failure_signal=signal)
            click.echo("✅ 对话已添加\n")
        else:
            click.echo("（空对话，跳过）\n")


def _run_collect_pipeline(source, review_db: Path, output_dir: Path) -> None:
    """执行搜集管道。"""
    import asyncio

    from eval_core.collector import (
        CaseClassifier, CaseFormatter, CaseDeduplicator, ReviewQueue, CollectionPipeline,
    )
    from eval_core.utils import logger

    # 加载已有 case（用于去重）
    import json
    existing = []
    for f in output_dir.parent.rglob("*.json"):
        try:
            existing.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass

    classifier = CaseClassifier()  # 无 JudgeClient，返回默认分类
    formatter = CaseFormatter(output_dir)
    deduplicator = CaseDeduplicator(existing)
    review_queue = ReviewQueue(review_db)
    pipeline = CollectionPipeline(classifier, formatter, deduplicator, review_queue)

    result = asyncio.run(pipeline.collect(source))
    click.echo(f"\n搜集结果: {result.total} 拉取, {result.enqueued} 入队, "
               f"{result.duplicates} 重复跳过")
    if result.errors:
        click.echo(f"错误: {result.errors}")


@collect.command()
@click.option("--status", default="pending", type=click.Choice(["pending", "approved", "rejected"]))
@click.option("--review-db", default="review_queue.db", type=click.Path(path_type=Path))
def review(status, review_db) -> None:
    """查看审核队列"""
    from eval_core.collector import ReviewQueue

    queue = ReviewQueue(review_db)
    items = queue.list(status=status)

    if not items:
        click.echo(f"队列中无 {status} 状态的 case")
        return

    click.echo(f"{'case_id':<15} {'来源':<10} {'置信度':<8} {'创建时间':<20}")
    click.echo("-" * 60)
    for item in items:
        click.echo(
            f"{item['case_id']:<15} {item['source_type']:<10} "
            f"{item['confidence']:<8.2f} {item['created_at'][:19]:<20}"
        )
    click.echo(f"\n共 {len(items)} 条")


@collect.command()
@click.argument("case_id")
@click.option("--review-db", default="review_queue.db", type=click.Path(path_type=Path))
@click.option("--export-dir", default="cases/agent/auto_collected", type=click.Path(path_type=Path))
def approve(case_id, review_db, export_dir) -> None:
    """审核通过，写入用例目录"""
    from eval_core.collector import ReviewQueue
    from eval_core.utils import logger

    queue = ReviewQueue(review_db)
    if queue.approve(case_id, reviewer="cli"):
        case = queue.get_case(case_id)
        if case:
            export_dir.mkdir(parents=True, exist_ok=True)
            import json
            file_path = export_dir / f"{case_id}.json"
            file_path.write_text(
                json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            click.echo(f"✅ 已通过并导出: {file_path}")
        else:
            click.echo(f"✅ 已通过（case 数据缺失，未导出）")
    else:
        click.echo(f"❌ 未找到 pending 状态的 {case_id}")


@collect.command()
@click.argument("case_id")
@click.option("--reason", default="", help="驳回原因")
@click.option("--review-db", default="review_queue.db", type=click.Path(path_type=Path))
def reject(case_id, reason, review_db) -> None:
    """驳回"""
    from eval_core.collector import ReviewQueue

    queue = ReviewQueue(review_db)
    if queue.reject(case_id, reviewer="cli", reason=reason):
        click.echo(f"✅ 已驳回: {case_id}")
    else:
        click.echo(f"❌ 未找到 pending 状态的 {case_id}")


# ==================== case 子命令组 ====================

@cli.group()
def case() -> None:
    """用例管理：搜索 / 统计 / 作废"""


@case.command()
@click.option("--priority", help="按优先级筛选（P0/P1/P2/P3）")
@click.option("--dimension", help="按维度筛选（模糊匹配，如 D9）")
@click.option("--scenario", help="按场景筛选")
@click.option("--keyword", help="关键词搜索（标题/场景/case_id）")
@click.option("--status", default="active", help="case 状态（active/deprecated/all）")
@click.option("--db", default="cases.db", type=click.Path(path_type=Path), help="SQLite 路径")
def search(priority, dimension, scenario, keyword, status, db) -> None:
    """搜索用例"""
    from eval_core.case_manager import CaseRepository

    if not db.exists():
        click.echo(f"数据库不存在: {db}（先运行评测或手动 sync）")
        return

    repo = CaseRepository(db)
    results = repo.search(
        priority=priority, dimension=dimension, scenario=scenario,
        keyword=keyword, case_status=status,
    )

    if not results:
        click.echo("无匹配结果")
        return

    click.echo(f"{'case_id':<15} {'类型':<8} {'优先级':<6} {'场景':<20} {'标题':<30}")
    click.echo("-" * 85)
    for r in results:
        click.echo(
            f"{r['case_id']:<15} {r['case_type']:<8} {r['priority']:<6} "
            f"{r['scenario'][:18]:<20} {r['title'][:28]:<30}"
        )
    click.echo(f"\n共 {len(results)} 条")


@case.command()
@click.option("--db", default="cases.db", type=click.Path(path_type=Path))
def stats(db) -> None:
    """用例统计（按维度/优先级/场景）"""
    from eval_core.case_manager import CaseRepository

    if not db.exists():
        click.echo(f"数据库不存在: {db}")
        return

    repo = CaseRepository(db)
    stats = repo.get_statistics()

    click.echo("=" * 50)
    click.echo("用例统计")
    click.echo("=" * 50)
    click.echo(f"总数: {stats['total']}")

    if stats.get("by_status"):
        click.echo("\n按状态:")
        for s, cnt in stats["by_status"].items():
            click.echo(f"  {s}: {cnt}")

    if stats.get("by_priority"):
        click.echo("\n按优先级:")
        for p, cnt in stats["by_priority"].items():
            click.echo(f"  {p}: {cnt}")

    if stats.get("by_dimension"):
        click.echo("\n按维度:")
        for d, cnt in stats["by_dimension"].items():
            click.echo(f"  {d}: {cnt}")


@case.command()
@click.argument("case_id")
@click.option("--reason", default="", help="作废原因")
@click.option("--db", default="cases.db", type=click.Path(path_type=Path))
def deprecate(case_id, reason, db) -> None:
    """作废用例（逻辑删除）"""
    from eval_core.case_manager import CaseRepository

    if not db.exists():
        click.echo(f"数据库不存在: {db}")
        return

    repo = CaseRepository(db)
    if repo.update_status(case_id, "deprecated", reason or "手动作废"):
        click.echo(f"✅ 已作废: {case_id}")
    else:
        click.echo(f"❌ 未找到: {case_id}")


# ==================== baseline 子命令组 ====================

@cli.group()
def baseline() -> None:
    """基线管理：查看历史版本"""


@baseline.command("list")
@click.option("--dir", "baselines_dir", default="baselines",
              type=click.Path(file_okay=False, path_type=Path))
def baseline_list(baselines_dir) -> None:
    """列出所有已保存的基线"""
    from eval_core.result import BaselineManager

    if not baselines_dir.exists():
        click.echo("无基线（目录不存在）")
        return

    bm = BaselineManager(baselines_dir)
    versions = bm.list_versions()

    if not versions:
        click.echo("无基线")
        return

    click.echo(f"{'版本':<10} {'保存时间':<20}")
    click.echo("-" * 35)
    for v in versions:
        data = bm.load(v)
        saved_at = data.get("saved_at", "")[:19] if data else ""
        click.echo(f"{v:<10} {saved_at:<20}")


@baseline.command("show")
@click.argument("version")
@click.option("--dir", "baselines_dir", default="baselines",
              type=click.Path(file_okay=False, path_type=Path))
def baseline_show(version, baselines_dir) -> None:
    """查看某个版本的基线详情"""
    from eval_core.result import BaselineManager

    if not baselines_dir.exists():
        click.echo("无基线（目录不存在）")
        return

    bm = BaselineManager(baselines_dir)
    data = bm.load(version)

    if not data:
        click.echo(f"基线不存在: {version}")
        return

    summary = data.get("summary", {})
    total = summary.get("total", {})

    click.echo(f"版本: {data.get('version', '')}")
    click.echo(f"保存时间: {data.get('saved_at', '')[:19]}")
    click.echo(f"用例数: {total.get('count', 0)} | 通过: {total.get('passed', 0)} "
               f"| 失败: {total.get('failed', 0)} | 通过率: {total.get('pass_rate', 0):.1%}")

    by_pri = summary.get("by_priority", {})
    if by_pri:
        click.echo("\n按优先级:")
        for p, s in by_pri.items():
            click.echo(f"  {p}: {s.get('passed', 0)}/{s.get('count', 0)} "
                       f"({s.get('pass_rate', 0):.1%})")

    by_dim = summary.get("by_dimension", {})
    if by_dim:
        click.echo("\n按维度 (前 10):")
        for d, s in sorted(by_dim.items())[:10]:
            click.echo(f"  {d}: {s.get('passed', 0)}/{s.get('count', 0)} "
                       f"({s.get('pass_rate', 0):.1%})")



# ==================== calibrate 子命令 ====================

@cli.command()
@click.option(
    "--config", "config_path", default="config/global.yaml",
    type=click.Path(exists=True, path_type=Path),
    help="全局配置文件路径",
)
@click.option(
    "--cases-dir", default="cases", type=click.Path(file_okay=False, path_type=Path),
    help="用例根目录",
)
@click.option(
    "--profiles-dir", default="profiles", type=click.Path(file_okay=False, path_type=Path),
    help="profile 根目录",
)
@click.option(
    "--storage-dir", default=".",
    type=click.Path(file_okay=False, path_type=Path),
    help="校准元数据存储目录（calibration_meta.json）",
)
def calibrate(config_path, cases_dir, profiles_dir, storage_dir) -> None:
    """Judge 校准：对比人工分 vs judge 分，量化 judge 可信度"""
    import asyncio

    import yaml

    setup_logger("INFO")

    async def _calibrate():
        from eval_core.evaluators import create_judge_evaluator
        from eval_core.evaluators.judge import CalibrationTracker
        from eval_core.loader import CaseDiscoverer, create_loader_registry
        from eval_core.result import CostTracker
        from eval_core.utils import logger

        # 加载配置
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        judge_config = config.get("judge", {})
        pricing = config.get("pricing", {})

        # 加载用例（只取有 golden 的）
        registry = create_loader_registry(profiles_dir)
        discoverer = CaseDiscoverer(registry)
        cases, errors = discoverer.load_all(cases_dir)
        golden_cases = [c for c in cases if getattr(c, "golden", None) is not None]

        if not golden_cases:
            click.echo("❌ 无含 golden 标注的 case（校准需要人工打分）")
            return

        click.echo(f"含 golden 标注的 case: {len(golden_cases)} 个")

        # 构建 judge evaluator
        cost_tracker = CostTracker(pricing)
        judge_evaluator = create_judge_evaluator(
            prompt_templates_path=config_path.parent / "prompt_templates.json",
            judge_config=judge_config,
            cost_tracker=cost_tracker,
        )

        # 执行校准
        tracker = CalibrationTracker(storage_dir)
        result = await tracker.calibrate(golden_cases, judge_evaluator)

        click.echo(f"\n{'=' * 60}")
        click.echo("Judge 校准结果")
        click.echo(f"{'=' * 60}")
        click.echo(f"样本数: {result.sample_size}")
        click.echo(f"Judge 模型: {result.judge_model}")
        click.echo(f"\n指标:")
        click.echo(f"  Spearman 秩相关: {result.spearman:.3f} (阈值 > 0.8)")
        click.echo(f"  MAE 平均误差:    {result.mae:.3f} (阈值 < 0.5)")
        click.echo(f"  Exact Match:     {result.exact_match_rate:.1%} (阈值 > 30%)")
        click.echo(f"  ±1 容错率:       {result.within_one_rate:.1%} (阈值 > 85%)")
        click.echo(f"  QWK Kappa:       {result.qwk:.3f} (阈值 > 0.6)")

        verdict_str = {
            "trusted": "✅ 可信，可用于评测",
            "acceptable": "⚠️ 基本可信，建议人工抽检",
            "untrusted": "❌ 不可信，需调整 prompt 或更换 judge 模型",
        }
        click.echo(f"\n判定: {verdict_str.get(result.verdict, result.verdict)}")

    asyncio.run(_calibrate())


@cli.command()
@click.option(
    "--storage-dir", default=".",
    type=click.Path(file_okay=False, path_type=Path),
    help="校准元数据存储目录",
)
@click.option("--judge-model", default="", help="当前使用的 judge 模型名（用于检查是否变更）")
def calibration_status(storage_dir, judge_model) -> None:
    """查看校准状态（是否过期、最近一次校准结果）"""
    from eval_core.evaluators.judge import CalibrationTracker

    tracker = CalibrationTracker(storage_dir)
    status = tracker.check_expired(current_model=judge_model)

    click.echo(f"{'=' * 50}")
    click.echo("Judge 校准状态")
    click.echo(f"{'=' * 50}")

    if status["expired"]:
        click.echo(f"❌ 已过期: {status['reason']}")
    else:
        click.echo("✅ 校准有效")

    meta = status.get("last_calibration")
    if meta:
        click.echo(f"\n最近校准:")
        click.echo(f"  时间: {meta.get('calibrated_at', '')[:19]}")
        click.echo(f"  模型: {meta.get('judge_model', '')}")
        click.echo(f"  样本: {meta.get('sample_size', 0)}")
        metrics = meta.get("metrics", {})
        if metrics:
            click.echo(f"  Spearman: {metrics.get('spearman', 0):.3f}")
            click.echo(f"  MAE: {metrics.get('mae', 0):.3f}")
            click.echo(f"  QWK: {metrics.get('qwk', 0):.3f}")
    else:
        click.echo("\n无校准记录（运行 eval-forge calibrate 进行校准）")

if __name__ == "__main__":
    cli()
