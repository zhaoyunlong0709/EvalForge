"""BaselineManager：基线管理 + 版本对比 + 退化检测。

对应《Agent评测框架设计方案》4.7 报告生成模块。

职责：
  1. 发版后保存评测结果为 baseline（--save-baseline v0.3）
  2. 正常评测时自动对比最新 baseline（如果存在）
  3. 退化检测：baseline 通过但当前失败的 case
  4. 修复检测：baseline 失败但当前通过的 case

存储：baselines/ 目录下按版本号命名（如 v0.2.json），JSON 格式。
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from eval_core.models import EvaluableCase
from eval_core.result.aggregator import ResultAggregator
from eval_core.utils import logger


class BaselineManager:
    """基线管理器"""

    def __init__(self, baselines_dir: Path | str):
        self.baselines_dir = Path(baselines_dir)
        self.baselines_dir.mkdir(parents=True, exist_ok=True)

    # ==================== 保存 / 加载 ====================

    def save(
        self,
        version: str,
        aggregated: dict,
        cases: list[EvaluableCase],
    ) -> Path:
        """保存评测结果为 baseline。

        Args:
            version: 版本号（如 "v0.3"）
            aggregated: ResultAggregator.aggregate() 的输出
            cases: 已执行评测的 case 列表

        Returns:
            baseline 文件路径
        """
        case_results: dict[str, dict] = {}
        for c in cases:
            case_results[c.case_id] = {
                "title": c.title,
                "passed": c.result.passed,
                "score": c.result.score,
                "priority": c.priority.value,
                "dimension_scores": dict(getattr(c.result, "dimension_scores", {}) or {}),
            }

        avg_score, dimension_avg_score = self._compute_avg_scores(cases)

        baseline = {
            "version": version,
            "saved_at": datetime.now().isoformat(),
            "summary": {
                "total": aggregated["total"],
                "avg_score": avg_score,
                "by_priority": aggregated.get("by_priority", {}),
                "by_dimension": aggregated.get("by_dimension", {}),
                "dimension_avg_score": dimension_avg_score,
            },
            "case_results": case_results,
        }

        file_path = self.baselines_dir / f"{version}.json"
        file_path.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"Baseline 已保存: {file_path}")
        return file_path

    def load_latest(self) -> dict[str, Any] | None:
        """加载最近的 baseline（按 saved_at 或文件名排序）。

        Returns:
            baseline dict，无 baseline 时返回 None
        """
        files = sorted(self.baselines_dir.glob("*.json"))
        if not files:
            return None
        return self._load_file(files[-1])

    def load(self, version: str) -> dict[str, Any] | None:
        """加载指定版本的 baseline。"""
        file_path = self.baselines_dir / f"{version}.json"
        if not file_path.exists():
            logger.warning(f"Baseline 不存在: {file_path}")
            return None
        return self._load_file(file_path)

    @staticmethod
    def _load_file(file_path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Baseline 加载失败: {file_path} - {e}")
            return None

    def list_versions(self) -> list[str]:
        """列出所有已保存的 baseline 版本号。"""
        return sorted(f.stem for f in self.baselines_dir.glob("*.json"))

    @staticmethod
    def _compute_avg_scores(cases: list[EvaluableCase]) -> tuple[float, dict[str, float]]:
        """计算整体平均分 + 各维度平均分。

        Returns:
            (整体平均分, {维度: 平均分})
        """
        scores = [c.result.score for c in cases]
        avg = round(sum(scores) / len(scores), 2) if scores else 0.0

        dim_score_map: dict[str, list[float]] = {}
        for c in cases:
            dim_scores = getattr(c.result, "dimension_scores", {}) or {}
            for dim, s in dim_scores.items():
                dim_score_map.setdefault(dim, []).append(float(s))

        dimension_avg = {
            dim: round(sum(vals) / len(vals), 2)
            for dim, vals in sorted(dim_score_map.items())
        }
        return avg, dimension_avg

    # ==================== 对比 ====================

    def compare(
        self,
        current_aggregated: dict,
        current_cases: list[EvaluableCase],
        baseline: dict | None = None,
    ) -> dict[str, Any] | None:
        """对比当前结果 vs baseline。

        Args:
            current_aggregated: ResultAggregator.aggregate() 的输出
            current_cases: 当前执行的 case 列表
            baseline: 要对比的 baseline（None 时自动加载最新）

        Returns:
            对比结果 dict，无 baseline 时返回 None
        """
        if baseline is None:
            baseline = self.load_latest()
        if baseline is None:
            logger.info("无 baseline，跳过对比")
            return None

        result: dict[str, Any] = {
            "baseline_version": baseline.get("version", "unknown"),
            "baseline_saved_at": baseline.get("saved_at", ""),
        }

        baseline_summary = baseline.get("summary", {})

        # 当前平均分 + 维度平均分
        current_avg, current_dim_avg = self._compute_avg_scores(current_cases)

        # 1. 整体对比（通过率 + 平均分）
        result["overall"] = self._compare_overall(
            baseline_summary,
            current_aggregated.get("total", {}),
            current_avg,
        )

        # 2. 按优先级对比（通过率）
        result["by_priority"] = self._compare_sections(
            baseline_summary.get("by_priority", {}),
            current_aggregated.get("by_priority", {}),
        )

        # 3. 按维度对比（通过率）
        result["by_dimension"] = self._compare_sections(
            baseline_summary.get("by_dimension", {}),
            current_aggregated.get("by_dimension", {}),
        )

        # 3.5 按维度对比（平均分）。旧 baseline 无此数据时返回空 dict
        baseline_dim_avg = baseline_summary.get("dimension_avg_score")
        result["dimension_avg_score"] = (
            self._compare_dimension_scores(baseline_dim_avg, current_dim_avg)
            if baseline_dim_avg is not None else {}
        )

        # 4. case 级对比
        result.update(
            self._compare_cases(baseline.get("case_results", {}), current_cases)
        )

        return result

    def _compare_overall(
        self,
        baseline_summary: dict,
        current_total: dict,
        current_avg_score: float,
    ) -> dict:
        """整体通过率 + 平均分对比。"""
        baseline_total = baseline_summary.get("total", {})
        b_rate = baseline_total.get("pass_rate", 0)
        c_rate = current_total.get("pass_rate", 0)
        change = round(c_rate - b_rate, 4)

        b_avg = baseline_summary.get("avg_score")
        avg_change = round(current_avg_score - b_avg, 2) if b_avg is not None else None

        return {
            "baseline_pass_rate": b_rate,
            "current_pass_rate": c_rate,
            "baseline_count": baseline_total.get("count", 0),
            "current_count": current_total.get("count", 0),
            "change": change,
            "direction": "improved" if change > 0 else
                         "degraded" if change < 0 else "unchanged",
            "baseline_avg_score": b_avg,
            "current_avg_score": current_avg_score,
            "avg_score_change": avg_change,
            "avg_score_direction": (
                "improved" if avg_change > 0 else
                "degraded" if avg_change < 0 else "unchanged"
            ) if avg_change is not None else "unknown",
        }

    def _compare_sections(self, baseline_section: dict, current_section: dict) -> dict:
        """按优先级/维度对比（处理 baseline 有但当前没有的 section）。"""
        comparison: dict[str, dict] = {}
        all_keys = sorted(set(baseline_section) | set(current_section))

        for key in all_keys:
            b = baseline_section.get(key, {})
            c = current_section.get(key, {})
            b_rate = b.get("pass_rate") if b else None
            c_rate = c.get("pass_rate") if c else None

            if b_rate is not None and c_rate is not None:
                change = round(c_rate - b_rate, 4)
                comparison[key] = {
                    "baseline": b_rate,
                    "current": c_rate,
                    "change": change,
                    "direction": "improved" if change > 0 else
                                 "degraded" if change < 0 else "unchanged",
                }
            elif b_rate is not None:
                comparison[key] = {
                    "baseline": b_rate, "current": None,
                    "change": None, "direction": "removed",
                }
            else:
                comparison[key] = {
                    "baseline": None, "current": c_rate,
                    "change": None, "direction": "new",
                }

        return comparison

    def _compare_dimension_scores(
        self,
        baseline_dim_avg: dict,
        current_dim_avg: dict,
    ) -> dict:
        """按维度平均分对比（0-5 分值，而非通过率）。"""
        comparison: dict[str, dict] = {}
        all_dims = sorted(set(baseline_dim_avg) | set(current_dim_avg))

        for dim in all_dims:
            b = baseline_dim_avg.get(dim)
            c = current_dim_avg.get(dim)
            if b is not None and c is not None:
                change = round(c - b, 2)
                comparison[dim] = {
                    "baseline": b,
                    "current": c,
                    "change": change,
                    "direction": "improved" if change > 0 else
                                 "degraded" if change < 0 else "unchanged",
                }
            elif b is not None:
                comparison[dim] = {
                    "baseline": b, "current": None,
                    "change": None, "direction": "removed",
                }
            else:
                comparison[dim] = {
                    "baseline": None, "current": c,
                    "change": None, "direction": "new",
                }

        return comparison

    def _compare_cases(
        self, baseline_cases: dict, current_cases: list[EvaluableCase]
    ) -> dict:
        """case 级对比：退化 / 修复 / 分数变化 / 新增 / 移除。"""
        current_ids = {c.case_id for c in current_cases}
        baseline_ids = set(baseline_cases.keys())

        regressions: list[dict] = []
        fixes: list[dict] = []
        score_changes: list[dict] = []

        for c in current_cases:
            b = baseline_cases.get(c.case_id)
            if b is None:
                continue
            b_passed = b.get("passed")
            c_passed = c.result.passed
            if b_passed and not c_passed:
                regressions.append({
                    "case_id": c.case_id,
                    "title": c.title,
                    "priority": c.priority.value,
                    "baseline_passed": True,
                    "current_passed": False,
                    "error": c.result.error_analysis[:100],
                })
            elif not b_passed and c_passed:
                fixes.append({
                    "case_id": c.case_id,
                    "title": c.title,
                    "baseline_passed": False,
                    "current_passed": True,
                })
            else:
                # 通过状态不变，记录分数变化
                b_score = b.get("score")
                c_score = c.result.score
                if b_score is not None and b_score != c_score:
                    score_changes.append({
                        "case_id": c.case_id,
                        "title": c.title,
                        "baseline_score": b_score,
                        "current_score": c_score,
                        "change": round(c_score - b_score, 2),
                    })

        new_cases: list[dict] = []
        for c in current_cases:
            if c.case_id not in baseline_ids:
                new_cases.append({
                    "case_id": c.case_id,
                    "title": c.title,
                    "priority": c.priority.value,
                    "current_passed": c.result.passed,
                })
        new_cases.sort(key=lambda x: x["case_id"])

        removed_cases: list[dict] = []
        for cid in sorted(baseline_ids - current_ids):
            b = baseline_cases.get(cid, {})
            removed_cases.append({
                "case_id": cid,
                "title": b.get("title") or "-",
                "priority": b.get("priority") or "-",
                "baseline_passed": b.get("passed"),
            })

        if regressions:
            logger.warning(
                f"检测到 {len(regressions)} 个退化 case: "
                + ", ".join(r["case_id"] for r in regressions)
            )
        if fixes:
            logger.info(f"检测到 {len(fixes)} 个修复 case")
        if score_changes:
            logger.info(f"检测到 {len(score_changes)} 个分数变化 case")

        return {
            "regressions": regressions,
            "fixes": fixes,
            "score_changes": score_changes,
            "new_cases": new_cases,
            "removed_cases": removed_cases,
        }
