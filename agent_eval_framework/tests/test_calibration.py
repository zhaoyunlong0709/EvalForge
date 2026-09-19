"""CalibrationTracker 测试：5 指标计算 / 判定 / 过期检测 / 防泄露。"""
import pytest
from eval_core.evaluators.judge.calibration import CalibrationTracker


class TestMetrics:
    def test_perfect_match(self):
        human = [5, 4, 3, 4, 5, 3, 2, 4, 5, 3]
        judge = [5, 4, 3, 4, 5, 3, 2, 4, 5, 3]
        r = CalibrationTracker('/tmp/test_cal').calculate(human, judge)
        assert r.spearman == 1.0
        assert r.mae == 0.0
        assert r.exact_match_rate == 1.0
        assert r.within_one_rate == 1.0
        assert r.qwk == 1.0
        assert r.verdict == "trusted"

    def test_completely_wrong(self):
        human = [5, 4, 3, 4, 5, 3, 2, 4, 5, 3]
        judge = [1, 1, 5, 1, 1, 5, 5, 1, 1, 5]
        r = CalibrationTracker('/tmp/test_cal').calculate(human, judge)
        assert r.spearman < 0
        assert r.mae > 2.0
        assert r.verdict == "untrusted"

    def test_spearman_perfect_correlation(self):
        human = [1, 2, 3, 4, 5]
        judge = [2, 3, 4, 5, 6]  # 完全单调递增
        r = CalibrationTracker('/tmp/test_cal').calculate(human, judge)
        assert r.spearman == 1.0  # 秩相关完美
        assert r.mae == 1.0  # 数值偏差 1

    def test_mae(self):
        human = [5, 3, 4]
        judge = [4, 4, 4]
        r = CalibrationTracker('/tmp/test_cal').calculate(human, judge)
        assert r.mae == pytest.approx(2 / 3, 0.01)

    def test_exact_match(self):
        human = [5, 3, 4, 5]
        judge = [5, 3, 5, 5]
        r = CalibrationTracker('/tmp/test_cal').calculate(human, judge)
        assert r.exact_match_rate == 0.75  # 3/4

    def test_within_one(self):
        human = [5, 3, 4, 2]
        judge = [4, 5, 4, 3]
        r = CalibrationTracker('/tmp/test_cal').calculate(human, judge)
        assert r.within_one_rate == 0.75  # 3/4

    def test_qwk_perfect(self):
        human = [5, 4, 3, 2, 1, 5, 4, 3, 2, 1]
        judge = [5, 4, 3, 2, 1, 5, 4, 3, 2, 1]
        r = CalibrationTracker('/tmp/test_cal').calculate(human, judge)
        assert r.qwk == 1.0

    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="校准样本为空"):
            CalibrationTracker('/tmp/test_cal').calculate([], [])

    def test_mismatched_length_raises(self):
        with pytest.raises(ValueError, match="数量不一致"):
            CalibrationTracker('/tmp/test_cal').calculate([1, 2], [1])


class TestVerdict:
    def test_trusted(self):
        human = [5, 4, 3, 4, 5, 3, 2, 4, 5, 3]
        judge = [5, 4, 3, 4, 5, 3, 2, 4, 5, 3]
        r = CalibrationTracker('/tmp/test_cal').calculate(human, judge)
        assert r.verdict == "trusted"

    def test_acceptable(self):
        # Spearman > 0.6, MAE < 1.0 -> acceptable
        human = [5, 5, 4, 4, 3, 3, 2, 2, 1, 1]
        judge = [4, 5, 5, 4, 2, 3, 3, 2, 1, 0]
        r = CalibrationTracker('/tmp/test_cal').calculate(human, judge)
        assert r.verdict in ("trusted", "acceptable")

    def test_untrusted(self):
        human = [5, 4, 3, 4, 5, 3, 2, 4, 5, 3]
        judge = [1, 1, 5, 1, 1, 5, 5, 1, 1, 5]
        r = CalibrationTracker('/tmp/test_cal').calculate(human, judge)
        assert r.verdict == "untrusted"


class TestExpiry:
    def test_no_calibration(self, tmp_path):
        tracker = CalibrationTracker(tmp_path)
        status = tracker.check_expired()
        assert status["expired"] is True
        assert "无校准记录" in status["reason"]

    def test_model_changed(self, tmp_path):
        human = [5, 4, 3, 4, 5, 3, 2, 4, 5, 3, 4, 5, 2, 3, 4,
                 5, 3, 4, 5, 2, 3, 4, 5, 3, 4, 2, 5, 3, 4, 5]
        judge = [5, 4, 3, 4, 5, 3, 2, 4, 5, 3, 4, 5, 2, 3, 4,
                 5, 3, 4, 5, 2, 3, 4, 5, 3, 4, 2, 5, 3, 4, 5]
        r = CalibrationTracker(tmp_path).calculate(human, judge, judge_model="gpt-4o")
        CalibrationTracker(tmp_path).save(r)
        status = CalibrationTracker(tmp_path).check_expired(current_model="claude-3")
        assert status["expired"] is True
        assert "模型已变更" in status["reason"]

    def test_valid_calibration(self, tmp_path):
        human = [5, 4, 3, 4, 5, 3, 2, 4, 5, 3, 4, 5, 2, 3, 4,
                 5, 3, 4, 5, 2, 3, 4, 5, 3, 4, 2, 5, 3, 4, 5]
        judge = [5, 4, 3, 4, 5, 3, 2, 4, 5, 3, 4, 5, 2, 3, 4,
                 5, 3, 4, 5, 2, 3, 4, 5, 3, 4, 2, 5, 3, 4, 5]
        r = CalibrationTracker(tmp_path).calculate(human, judge, judge_model="gpt-4o")
        CalibrationTracker(tmp_path).save(r)
        status = CalibrationTracker(tmp_path).check_expired(current_model="gpt-4o")
        assert status["expired"] is False


class TestLabelLeakage:
    async def test_calibrate_skips_cases_without_golden(self):
        """验证 calibrate 跳过无 golden.overall_score 的 case。"""
        from eval_core.models import AgentCase

        tracker = CalibrationTracker('/tmp/test_cal_skip')

        # 创建无 golden 的 case
        cases = []
        for i in range(5):
            case = AgentCase.model_validate({
                "case_id": f"C_CAL_{i:03d}",
                "title": f"测试 {i}",
                "version": "v0.1",
                "priority": "P1",
                "scenario": "测试",
                "conversation": [{"turn": 1, "user": f"测试输入 {i}"}],
                "evaluation": {"method": "llm_judge", "template": "test"},
            })
            case.result.actual_reply = f"回复 {i}"
            case.result.passed = True
            cases.append(case)

        from eval_core.evaluators.judge import JudgeEvaluator
        from eval_core.evaluators.judge import TemplateEngine
        from eval_core.evaluators.judge.judge_client import JudgeClient

        engine = TemplateEngine({"test": "模板"})
        client = JudgeClient(model="test", api_key="fake")

        with pytest.raises(ValueError, match="无有效校准样本"):
            await tracker.calibrate(cases, JudgeEvaluator(engine, client))
