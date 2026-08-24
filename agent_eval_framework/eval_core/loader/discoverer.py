"""CaseDiscoverer：递归扫描目录，按需加载。

对应《Agent评测框架设计方案》4.2 用例加载模块。
加载范围（文档 4.2 加载流程）：
  --case-dir cases/agent/companion/smoke  -> 加载指定目录
  --case cases/agent/C001.json            -> 加载指定文件
  （无参数）                               -> 加载全部 cases/ 目录

校验失败的用例记录到 errors 列表，不中断整体加载（对应
《LLM-as-Judge评测执行流程》第 1 步："校验失败的用例记录到 skipped_cases，
不执行，评测报告中标注为跳过"）。
"""
from pathlib import Path

from eval_core.models import EvaluableCase
from eval_core.utils import logger

from .registry import LoaderRegistry


class CaseDiscoverer:
    """用例发现与加载"""

    def __init__(self, registry: LoaderRegistry):
        self.registry = registry

    def discover(
        self,
        root: Path,
        case_files: list[Path] | None = None,
        case_dirs: list[Path] | None = None,
    ) -> list[Path]:
        """确定要加载的 JSON 文件列表，根据case_files和case_dirs加载所有json文件并去重。

        Args:
            root: cases/ 根目录（无参数时全量扫描）
            case_files: 指定文件路径列表（--case）
            case_dirs: 指定目录列表（--case-dir）

        Raises:
            FileNotFoundError: 指定的文件或目录不存在
        """
        if not case_files and not case_dirs:
            return sorted(root.rglob("*.json"))

        paths: list[Path] = []
        seen: set[Path] = set()

        for f in case_files or []:
            if not f.is_file():
                raise FileNotFoundError(f"指定的用例文件不存在: {f}")
            resolved = f.resolve()
            if resolved not in seen:
                seen.add(resolved)
                paths.append(f)

        for d in case_dirs or []:
            if not d.is_dir():
                raise FileNotFoundError(f"指定的用例目录不存在: {d}")
            for f in sorted(d.rglob("*.json")):
                resolved = f.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    paths.append(f)

        return paths

    def load_all(
        self,
        root: Path,
        case_files: list[Path] | None = None,
        case_dirs: list[Path] | None = None,
    ) -> tuple[list[EvaluableCase], list[dict]]:
        """扫描 + 加载所有用例。

        Returns:
            (cases, errors) 元组：
            - cases: 加载成功的 EvaluableCase 列表
            - errors: 加载失败记录列表，每项 {"file": 路径, "error": 原因}
        """
        cases: list[EvaluableCase] = []
        errors: list[dict] = []

        for file_path in self.discover(root, case_files, case_dirs):
            try:
                case = self.registry.load(file_path)
                cases.append(case)
            except Exception as e:  # noqa: BLE001 - 单个失败不中断整体
                errors.append({"file": str(file_path), "error": str(e)})
                logger.warning(f"用例加载失败（跳过）: {file_path.name} - {e}")

        logger.info(
            f"用例加载完成: {len(cases)} 成功, {len(errors)} 失败/跳过"
        )
        return cases, errors
