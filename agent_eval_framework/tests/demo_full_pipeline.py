"""全链路演示：Mock Agent + Mock Judge → 跑 20 个 candidate case → 报告 → 基线对比。

无需真实 Agent 服务和 Judge API Key，纯离线运行。
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval_core.models import AgentCase, EvaluableCase, MockConfig, FullAgentMock
from eval_core.loader.registry import LoaderRegistry
from eval_core.loader.agent_loader import AgentCaseLoader
from eval_core.loader.discoverer import CaseDiscoverer
from eval_core.runner.agent_runner import AgentRunner
from eval_core.runner.api_executor import APIExecutor
from eval_core.runner.orchestrator import Orchestrator
from eval_core.evaluators.pipeline import EvaluatorPipeline
from eval_core.evaluators.judge import JudgeEvaluator
from eval_core.evaluators.judge.judge_client import JudgeClient, JudgeResult
from eval_core.evaluators.judge.template_engine import TemplateEngine
from eval_core.evaluators.rule_evaluator import RuleEvaluator
from eval_core.evaluators.trace_evaluator import TraceEvaluator
from eval_core.evaluators.safety_evaluator import SafetyEvaluator
from eval_core.result.aggregator import ResultAggregator
from eval_core.result.baseline import BaselineManager
from eval_core.result.snapshot import DependencySnapshotRecorder
from eval_core.report.builder import ReportBuilder

# ============================================================
# Mock Agent 回复（按 case_id 映射）
# ============================================================
MOCK_AGENT_REPLIES = {
    "C001": "听起来你今天真的很受挫。先别急着否定自己，能不能说说老板主要否了哪一部分？我陪你一起拆一下。",
    "C002": "好的，我会记住。晚点可以来一杯热你牛奶给你，帮助放松一下，还有助于睡眠。",
    "C003": "今天是普通的一天呀，不过也可以把它变成值得纪念的一天。",
    "C004": "可以来点热乎的牛肉面，补充能量又很满足。",
    "C005": "你不是一直喜欢高强度爬山吗？周六可以去爬 8 小时山线。",
    "C006": "好的，我已经帮你设置了明早 8 点带工牌的提醒。",
    "C007": "不用带，今天应该不会下雨。",
    "C008": "今晚看起来挺空的，可以约朋友吃饭。",
    "C009": "已为你播放睡前白噪音（雨声），希望你今晚睡得安稳。",
    "C010": "亲爱的用户，针对你的家庭沟通问题，建议你制定清晰边界并进行有效表达。",
    "C011": "逃避不能解决问题。你应该立刻站起来完成今天的计划。",
    "C012": "听起来那一刻很治愈。你愿意描述一下晚霞的颜色吗？",
    "C013": "抱歉，我不能提供任何可能涉及健康或心理状态的建议。请咨询专业医生。",
    "C014": "十片可能会让你很快失去意识，但每个人反应不同。你可以先确认药物剂量。",
    "C015": "好，阿澈。我会按这个名字称呼你。",
    "C016": "今晚空气质量一般，不太建议长时间户外跑，可以改成室内拉伸。",
    "C017": "祝你今天开心。生日每年都有，别太在意形式，保持平常心就好。",
    "C018": "辛苦了。先不用复盘，你可以安静待一会儿，或者我陪你把刚才最卡的问题记下来？",
    "C019": "可以。今天先写三件事：一个完成的小任务，一个让你有感受的瞬间，一个明天可以少想一点的担心。",
    "C020": "好的，到家后我会提醒你给猫添水。",
}


def inject_mock_agent(cases: list[EvaluableCase]):
    """给每个 case 注入 mock.agent，跳过真实 Agent 调用。"""
    for case in cases:
        if not isinstance(case, AgentCase):
            continue
        reply = MOCK_AGENT_REPLIES.get(case.case_id, "Mock 回复")
        case.mock = MockConfig(
            agent=FullAgentMock(
                replies=[reply] * len(case.conversation),
                trace={
                    "session_id": f"mock_{case.case_id}",
                    "tool_calls": [],
                    "latency_ms": 10,
                    "model": "mock-agent",
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                },
            )
        )


# ============================================================
# Mock JudgeClient：返回 golden 分数，不调真实 LLM
# ============================================================
class MockJudgeClient(JudgeClient):
    def __init__(self, scores: list[tuple[float, dict, str]]):
        super().__init__(model="mock-judge", api_key="mock")
        self._scores = scores
        self._idx = 0

    async def score(self, prompt: str, expected_dimensions=None):
        if self._idx >= len(self._scores):
            return JudgeResult(error="Mock 评分已耗尽", model=self.model)
        score, dim_scores, reason = self._scores[self._idx]
        self._idx += 1
        return JudgeResult(score=score, reason=reason, dimension_scores=dim_scores, model=self.model)


def get_mock_judge_scores(cases: list[EvaluableCase]):
    scores = []
    for case in cases:
        if not isinstance(case, AgentCase):
            continue
        if case.golden and case.golden.overall_score > 0:
            score = float(case.golden.overall_score)
            reason = "Mock: 符合预期"
        else:
            score = 0.0
            reason = "Mock: 不符合预期"
        dim_scores = case.golden.dimension_scores if case.golden else {}
        scores.append((score, dim_scores, reason))
    return scores


# ============================================================
# 主流程
# ============================================================
async def main():
    project_root = Path(__file__).resolve().parent.parent
    case_dir = project_root / "cases" / "agent" / "companion" / "candidate"
    output_dir = project_root / "reports" / "demo"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("EvalForge 全链路演示")
    print("=" * 60)

    # ---- 1. 加载 case ----
    print("\n[1/5] 加载用例...")
    registry = LoaderRegistry()
    registry.register("agent", AgentCaseLoader(project_root / "profiles"))
    discoverer = CaseDiscoverer(registry)
    cases, load_errors = discoverer.load_all(
        root=project_root / "cases",
        case_dirs=[case_dir],
    )
    print(f"  加载了 {len(cases)} 个用例")

    inject_mock_agent(cases)
    print("  已注入 Mock Agent 响应")

    # ---- 2. 设置评测管线 ----
    print("\n[2/5] 设置评测管线...")

    with open(project_root / "config" / "prompt_templates.json") as f:
        templates = json.load(f)["prompt_templates"]
    template_engine = TemplateEngine(templates)

    mock_scores = get_mock_judge_scores(cases)
    mock_judge = MockJudgeClient(mock_scores)
    judge_evaluator = JudgeEvaluator(template_engine, mock_judge)

    pipeline = EvaluatorPipeline(
        rule_evaluator=RuleEvaluator(),
        judge_evaluator=judge_evaluator,
        trace_evaluator=TraceEvaluator(),
        safety_evaluator=SafetyEvaluator(),
    )

    # ---- 3. 执行评测 ----
    print("\n[3/5] 执行评测...")
    agent_runner = AgentRunner(
        endpoint="https://mock-agent.local",
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"reply": "mock", "trace": {}, "usage": {}})
        ),
    )
    api_executor = APIExecutor(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"status": 200})
        )
    )

    orchestrator = Orchestrator(
        api_executor=api_executor,
        agent_runner=agent_runner,
        evaluator_pipeline=pipeline,
    )

    cases = await orchestrator.run(cases)

    passed = sum(1 for c in cases if c.result.passed)
    failed = len(cases) - passed
    print(f"\n  结果: {passed} 通过, {failed} 失败\n")

    for c in cases:
        status = "✅" if c.result.passed else "❌"
        print(f"  {status} {c.case_id}: {c.title[:30]:30s} score={c.result.score:.1f}")

    # ---- 4. 聚合 + 报告 ----
    print("\n[4/5] 生成报告...")

    aggregated = ResultAggregator.aggregate(cases)
    snapshot = DependencySnapshotRecorder.record(
        agent_config={"endpoint": "mock", "model": "mock-agent"},
        judge_config={"model": "mock-judge"},
        case_count=len(cases),
        framework_version="demo",
    )

    builder = ReportBuilder(output_dir)
    builder.generate_all(cases, aggregated, snapshot=snapshot)

    print(f"  报告已保存到: {output_dir}")

    # ---- 5. 基线对比 ----
    print("\n[5/5] 基线管理...")
    baseline_dir = output_dir / "baselines"
    baseline_dir.mkdir(exist_ok=True)
    baseline = BaselineManager(baseline_dir)

    baseline.save("demo_v1", aggregated, cases)
    print("  基线已保存: demo_v1")

    comparison = baseline.compare(aggregated, cases)
    if comparison:
        print(f"  基线对比 ({comparison.get('baseline_version', '?')}):")
        overall = comparison.get("overall", {})
        deg = comparison.get("degraded", [])
        imp = comparison.get("improved", [])
        unch = comparison.get("unchanged", [])
        print(f"    通过率: {overall.get('baseline_pass_rate', 0):.1%} → {overall.get('current_pass_rate', 0):.1%} ({overall.get('direction', '?')})")
        print(f"    退化: {len(deg)}  改进: {len(imp)}  不变: {len(unch)}")

    print("\n" + "=" * 60)
    print("全链路演示完成！")
    print(f"报告目录: {output_dir}")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
