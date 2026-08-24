"""CalibrationTracker：Judge 校准计算 + 元数据管理 + 过期检测。

对应《Agent评测框架设计方案》4.5 Judge 校准（Phase 2）+ 6.3 校准流程。

校准流程（文档 6.3）：
  1. 从 golden set 抽取 30-50 条 case（已有人工标注 overall_score）
  2. 让 JudgeEvaluator 对同一批 case 打分（judge 不感知人工分）
  3. 对比人工分 vs judge 分，计算 5 个指标
  4. 判定 judge 是否可信
  5. 记录校准元数据到 calibration_meta.json
  6. 每次评测时检查校准是否过期

防标签泄露：
  judge 评分时只看 conversation + actual_reply + 评分标准 + 参考答案。
  golden.overall_score / dimension_scores / failure 不进入 judge prompt，
  仅在校准对比阶段使用。
"""
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from eval_core.utils import logger


@dataclass
class CalibrationResult:
    """单次校准的结果"""

    # 5 个指标
    spearman: float = 0.0            # 秩相关系数（> 0.8 可信）
    mae: float = 0.0                 # 平均绝对误差（< 0.5 可信）
    exact_match_rate: float = 0.0    # 完全一致率（> 30%）
    within_one_rate: float = 0.0     # ±1 容错率（> 85%）
    qwk: float = 0.0                 # 二次加权 Kappa（> 0.6）

    # 判定
    verdict: str = ""                # "trusted" / "acceptable" / "untrusted"
    sample_size: int = 0             # 校准样本数
    judge_model: str = ""            # 校准时使用的 judge 模型

    # 明细
    human_scores: list[float] = field(default_factory=list)
    judge_scores: list[float] = field(default_factory=list)

    @property
    def summary(self) -> str:
        marks = {
            "spearman": "✅" if self.spearman > 0.8 else ("⚠️" if self.spearman > 0.6 else "❌"),
            "mae": "✅" if self.mae < 0.5 else ("⚠️" if self.mae < 1.0 else "❌"),
            "qwk": "✅" if self.qwk > 0.6 else "❌",
        }
        verdict_str = {
            "trusted": "✅ 可信",
            "acceptable": "⚠️ 基本可信（需人工抽检）",
            "untrusted": "❌ 不可信",
        }.get(self.verdict, self.verdict)
        return (
            f"Spearman={self.spearman:.3f} {marks['spearman']} | "
            f"MAE={self.mae:.3f} {marks['mae']} | "
            f"EM={self.exact_match_rate:.1%} | "
            f"±1={self.within_one_rate:.1%} | "
            f"QWK={self.qwk:.3f} {marks['qwk']} -> {verdict_str}"
        )


class CalibrationTracker:
    """Judge 校准追踪器"""

    CALIBRATION_FILE = "calibration_meta.json"
    EXPIRY_DAYS = 30                  # 校准有效期（天）
    MIN_SAMPLE_SIZE = 30              # 最小校准样本数

    def __init__(self, storage_dir: Path | str = "."):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.storage_dir / self.CALIBRATION_FILE

    # ==================== 指标计算 ====================

    def calculate(
        self,
        human_scores: list[float],
        judge_scores: list[float],
        judge_model: str = "",
    ) -> CalibrationResult:
        """对比人工分和 judge 分，计算 5 个校准指标。

        Args:
            human_scores: 人工标注分数列表（来自 golden.overall_score）
            judge_scores: judge 评分列表（来自 result.score）
            judge_model: 校准时使用的 judge 模型名

        Returns:
            CalibrationResult 含 5 个指标 + 判定结果
        """
        if len(human_scores) != len(judge_scores):
            raise ValueError(
                f"人工分和 judge 分数量不一致: "
                f"{len(human_scores)} vs {len(judge_scores)}"
            )
        if not human_scores:
            raise ValueError("校准样本为空")

        result = CalibrationResult(
            sample_size=len(human_scores),
            judge_model=judge_model,
            human_scores=human_scores,
            judge_scores=judge_scores,
        )

        result.spearman = self._spearman(human_scores, judge_scores)
        result.mae = self._mae(human_scores, judge_scores)
        result.exact_match_rate = self._exact_match(human_scores, judge_scores)
        result.within_one_rate = self._within_one(human_scores, judge_scores)
        result.qwk = self._qwk(human_scores, judge_scores)
        result.verdict = self._verdict(result)

        logger.info(f"校准结果: {result.summary}")
        return result

    @staticmethod
    def _spearman(x: list[float], y: list[float]) -> float:
        """Spearman 秩相关系数。"""
        if len(x) < 2:
            return 0.0

        def rank(arr: list[float]) -> list[float]:
            indexed = sorted(range(len(arr)), key=lambda i: arr[i])
            ranks = [0.0] * len(arr)
            i = 0
            while i < len(indexed):
                # 处理并列：取平均秩
                j = i
                while j < len(indexed) - 1 and arr[indexed[j + 1]] == arr[indexed[i]]:
                    j += 1
                avg_rank = (i + j) / 2 + 1
                for k in range(i, j + 1):
                    ranks[indexed[k]] = avg_rank
                i = j + 1
            return ranks

        rx, ry = rank(x), rank(y)
        return CalibrationTracker._pearson(rx, ry)

    @staticmethod
    def _pearson(x: list[float], y: list[float]) -> float:
        """Pearson 相关系数（Spearman 的底层计算）。"""
        n = len(x)
        if n < 2:
            return 0.0
        mx = sum(x) / n
        my = sum(y) / n
        cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
        var_x = sum((a - mx) ** 2 for a in x)
        var_y = sum((b - my) ** 2 for b in y)
        if var_x == 0 or var_y == 0:
            return 0.0
        return cov / math.sqrt(var_x * var_y)

    @staticmethod
    def _mae(x: list[float], y: list[float]) -> float:
        """平均绝对误差。"""
        return sum(abs(a - b) for a, b in zip(x, y)) / len(x)

    @staticmethod
    def _exact_match(x: list[float], y: list[float]) -> float:
        """完全一致率。"""
        return sum(1 for a, b in zip(x, y) if a == b) / len(x)

    @staticmethod
    def _within_one(x: list[float], y: list[float]) -> float:
        """±1 容错率。"""
        return sum(1 for a, b in zip(x, y) if abs(a - b) <= 1) / len(x)

    @staticmethod
    def _qwk(x: list[float], y: list[float]) -> float:
        """二次加权 Kappa（Quadratic Weighted Kappa）。

        适用于有序评分（如 1-5 分），考虑距离权重。
        """
        if not x or not y:
            return 0.0

        # 评分范围（如 0-5）
        min_val = min(min(x), min(y))
        max_val = max(max(x), max(y))
        n_categories = int(max_val - min_val) + 1
        if n_categories < 2:
            return 1.0  # 只有一个类别，完全一致

        # 构建混淆矩阵
        confusion = [[0] * n_categories for _ in range(n_categories)]
        for a, b in zip(x, y):
            i = int(a - min_val)
            j = int(b - min_val)
            confusion[i][j] += 1

        n = len(x)
        # 权重矩阵（二次权重）
        weights = [[(i - j) ** 2 / (n_categories - 1) ** 2
                    for j in range(n_categories)] for i in range(n_categories)]

        # 观测一致性
        observed = sum(weights[i][j] * confusion[i][j]
                       for i in range(n_categories) for j in range(n_categories)) / n

        # 期望一致性（假设独立）
        row_marginal = [sum(confusion[i]) / n for i in range(n_categories)]
        col_marginal = [sum(confusion[i][j] for i in range(n_categories)) / n
                        for j in range(n_categories)]
        expected = sum(weights[i][j] * row_marginal[i] * col_marginal[j]
                       for i in range(n_categories) for j in range(n_categories))

        if expected == 0:
            return 1.0
        return 1 - observed / expected

    @staticmethod
    def _verdict(result: CalibrationResult) -> str:
        """根据指标判定 judge 可信度。"""
        if result.spearman > 0.8 and result.mae < 0.5:
            return "trusted"
        if result.spearman > 0.6 and result.mae < 1.0:
            return "acceptable"
        return "untrusted"

    # ==================== 元数据管理 ====================

    def save(self, result: CalibrationResult) -> Path:
        """保存校准元数据。"""
        meta = {
            "calibrated_at": datetime.now().isoformat(),
            "judge_model": result.judge_model,
            "sample_size": result.sample_size,
            "metrics": {
                "spearman": result.spearman,
                "mae": result.mae,
                "exact_match_rate": result.exact_match_rate,
                "within_one_rate": result.within_one_rate,
                "qwk": result.qwk,
            },
            "verdict": result.verdict,
        }
        self.meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"校准元数据已保存: {self.meta_path}")
        return self.meta_path

    def load_latest(self) -> dict[str, Any] | None:
        """加载最近的校准元数据。"""
        if not self.meta_path.exists():
            return None
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"校准元数据加载失败: {e}")
            return None

    # ==================== 过期检测 ====================

    def check_expired(self, current_model: str = "") -> dict[str, Any]:
        """检查校准是否过期。

        过期条件（任一满足）：
          1. 无校准记录
          2. 超过 EXPIRY_DAYS 天
          3. judge 模型变了
          4. 校准样本数不足 MIN_SAMPLE_SIZE

        Returns:
            {"expired": bool, "reason": str, "last_calibration": dict | None}
        """
        meta = self.load_latest()

        if meta is None:
            return {"expired": True, "reason": "无校准记录", "last_calibration": None}

        # 检查模型是否变更
        if current_model and meta.get("judge_model") != current_model:
            return {
                "expired": True,
                "reason": f"Judge 模型已变更（校准时: {meta.get('judge_model')}, 当前: {current_model}）",
                "last_calibration": meta,
            }

        # 检查是否超期
        calibrated_at = meta.get("calibrated_at", "")
        if calibrated_at:
            try:
                cal_time = datetime.fromisoformat(calibrated_at)
                if datetime.now() - cal_time > timedelta(days=self.EXPIRY_DAYS):
                    return {
                        "expired": True,
                        "reason": f"校准已超过 {self.EXPIRY_DAYS} 天",
                        "last_calibration": meta,
                    }
            except ValueError:
                pass

        # 检查样本数
        if meta.get("sample_size", 0) < self.MIN_SAMPLE_SIZE:
            return {
                "expired": True,
                "reason": f"校准样本不足（{meta.get('sample_size')}/{self.MIN_SAMPLE_SIZE}）",
                "last_calibration": meta,
            }

        return {"expired": False, "reason": "", "last_calibration": meta}

    # ==================== 校准流程编排 ====================

    async def calibrate(
        self,
        cases: list,
        judge_evaluator,
    ) -> CalibrationResult:
        """执行完整校准流程。

        防标签泄露：传入 judge 前，剥离 golden 中的分数信息，
        judge 只看 conversation + actual_reply + 评分标准。

        Args:
            cases: 已有 golden.overall_score 的 case 列表（已执行过 Agent）
            judge_evaluator: JudgeEvaluator 实例

        Returns:
            CalibrationResult
        """
        from copy import deepcopy

        human_scores: list[float] = []
        judge_scores: list[float] = []

        for case in cases:
            if not hasattr(case, "golden") or case.golden is None:
                continue
            if not case.golden.overall_score:
                continue  # 跳过无人工分的 case

            human_scores.append(float(case.golden.overall_score))

            # 防标签泄露：深拷贝 case，剥离 golden 的分数信息
            judge_case = deepcopy(case)
            judge_case.golden = None  # judge 不感知任何 golden 标注

            # 执行 judge 评分
            judge_case = await judge_evaluator.evaluate(judge_case)
            judge_scores.append(float(judge_case.result.score))

        if not human_scores:
            raise ValueError("无有效校准样本（case 缺少 golden.overall_score）")

        result = self.calculate(
            human_scores=human_scores,
            judge_scores=judge_scores,
            judge_model=getattr(judge_evaluator.judge_client, "model", ""),
        )
        self.save(result)
        return result
