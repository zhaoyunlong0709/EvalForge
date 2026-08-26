"""CaseRepository：SQLite 索引层，加速用例查询和统计。

对应《Agent评测框架设计方案》4.1 用例管理模块。

设计原则：
  - JSON 文件是 source of truth（Git 版本管理，完整内容）
  - SQLite 只存元数据索引（case_id / title / priority / tags 等）
  - 启动时 sync：UPSERT 已有 + 标记文件已删除的为 deprecated
  - 逻辑删除：case_status = 'deprecated'，不物理删除 SQLite 记录

同步策略：
  1. 遍历 SQLite 所有记录，检查 file_path 是否存在
  2. 文件不存在 -> 标记 deprecated（reason='file_deleted'）
  3. 遍历加载的 cases，UPSERT 到 SQLite（已 deprecated 但文件回来的 -> 恢复 active）
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from eval_core.models import AgentCase, APICase, EvaluableCase
from eval_core.utils import logger


class CaseRepository:
    """用例仓库：SQLite 索引 + 查询 + 统计。"""

    def __init__(self, db_path: str | Path = "cases.db"):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        """建表 + 索引（幂等，重复执行不报错）。"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT UNIQUE NOT NULL,
                case_type TEXT NOT NULL,
                title TEXT NOT NULL,
                file_path TEXT NOT NULL,
                version TEXT NOT NULL,
                priority TEXT NOT NULL,
                scenario TEXT NOT NULL,
                dimensions TEXT NOT NULL,
                tags TEXT NOT NULL,
                evaluation_method TEXT NOT NULL,
                turn_count INTEGER DEFAULT 1,
                has_mock INTEGER DEFAULT 0,
                has_trace INTEGER DEFAULT 0,
                has_golden INTEGER DEFAULT 0,
                case_status TEXT DEFAULT 'active',
                deprecated_at TEXT,
                deprecated_reason TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_priority ON cases(priority);
            CREATE INDEX IF NOT EXISTS idx_case_type ON cases(case_type);
            CREATE INDEX IF NOT EXISTS idx_scenario ON cases(scenario);
            CREATE INDEX IF NOT EXISTS idx_case_status ON cases(case_status);
            CREATE INDEX IF NOT EXISTS idx_version ON cases(version);
        """)
        self.conn.commit()

    # ==================== 同步 ====================

    def sync(self, cases: list[tuple[EvaluableCase, str]]) -> dict[str, int]:
        """全量同步：UPSERT 加载的 cases + 标记文件已删除的为 deprecated。

        Args:
            cases: (case 对象, file_path) 元组列表

        Returns:
            {"synced": 新增/更新数, "deprecated": 标记作废数, "restored": 恢复数}
        """
        now = datetime.now().isoformat()
        synced = 0
        restored = 0

        # 第 1 步：检查已有记录中，文件是否被删除
        deprecated = self._mark_deleted_files(now)

        # 第 2 步：UPSERT 加载的 cases
        for case, file_path in cases:
            restored += self._upsert(case, str(file_path), now)
            synced += 1

        self.conn.commit()
        logger.info(
            f"SQLite 同步完成: {synced} 条 UPSERT, "
            f"{deprecated} 条标记作废, {restored} 条恢复"
        )
        return {
            "synced": synced,
            "deprecated": deprecated,
            "restored": restored,
        }

    def sync_incremental(self, cases: list[tuple[EvaluableCase, str]]) -> dict[str, int]:
        """增量同步：仅同步文件修改时间晚于上次同步的 case。

        1000+ case 时，全量同步 O(n) 文件 IO 不可接受。
        增量同步只处理变更的文件，未变更的跳过。
        """
        now = datetime.now().isoformat()
        synced = 0
        restored = 0
        skipped = 0

        # 获取上次同步时间
        last_sync = self._get_last_sync_time()

        for case, file_path in cases:
            fp = Path(file_path)
            if fp.exists() and last_sync:
                mtime = fp.stat().st_mtime
                if mtime <= last_sync:
                    # 检查 SQLite 是否已有此记录
                    existing = self.conn.execute(
                        "SELECT updated_at FROM cases WHERE case_id = ?",
                        (case.case_id,),
                    ).fetchone()
                    if existing:
                        skipped += 1
                        continue

            restored += self._upsert(case, str(file_path), now)
            synced += 1

        # 标记文件已删除
        deprecated = self._mark_deleted_files(now)

        # 更新同步时间
        self._set_last_sync_time(now)

        self.conn.commit()
        logger.info(
            f"SQLite 增量同步完成: {synced} 条 UPSERT, {skipped} 条跳过, "
            f"{deprecated} 条标记作废"
        )
        return {
            "synced": synced, "skipped": skipped,
            "deprecated": deprecated, "restored": restored,
        }

    def _get_last_sync_time(self) -> float | None:
        """获取上次同步时间戳。"""
        try:
            row = self.conn.execute(
                "SELECT MAX(updated_at) as latest FROM cases"
            ).fetchone()
            if row and row["latest"]:
                from datetime import datetime as dt
                return dt.fromisoformat(row["latest"]).timestamp()
        except Exception:
            pass
        return None

    def _set_last_sync_time(self, timestamp: str) -> None:
        """记录同步时间（通过 updated_at 字段隐式记录）。"""
        pass  # 不需要额外操作，updated_at 已记录

    def _mark_deleted_files(self, now: str) -> int:
        """标记 file_path 已不存在的记录为 deprecated。"""
        rows = self.conn.execute(
            "SELECT case_id, file_path FROM cases WHERE case_status = 'active'"
        ).fetchall()

        count = 0
        for row in rows:
            if not Path(row["file_path"]).exists():
                self.conn.execute(
                    """UPDATE cases SET
                       case_status = 'deprecated',
                       deprecated_at = ?,
                       deprecated_reason = 'file_deleted',
                       updated_at = ?
                       WHERE case_id = ?""",
                    (now, now, row["case_id"]),
                )
                count += 1
                logger.warning(
                    f"用例文件已删除，标记为 deprecated: {row['case_id']}"
                )
        return count

    def _upsert(self, case: EvaluableCase, file_path: str, now: str) -> int:
        """UPSERT 单条记录。返回是否恢复了 deprecated 状态（0/1）。"""
        if isinstance(case, AgentCase):
            meta = self._extract_agent_meta(case, file_path)
        elif isinstance(case, APICase):
            meta = self._extract_api_meta(case, file_path)
        else:
            return 0

        self.conn.execute(
            """INSERT INTO cases (
                case_id, case_type, title, file_path, version, priority,
                scenario, dimensions, tags, evaluation_method,
                turn_count, has_mock, has_trace, has_golden,
                case_status, deprecated_at, deprecated_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, NULL, ?)
            ON CONFLICT(case_id) DO UPDATE SET
                title = excluded.title,
                file_path = excluded.file_path,
                version = excluded.version,
                priority = excluded.priority,
                scenario = excluded.scenario,
                dimensions = excluded.dimensions,
                tags = excluded.tags,
                evaluation_method = excluded.evaluation_method,
                turn_count = excluded.turn_count,
                has_mock = excluded.has_mock,
                has_trace = excluded.has_trace,
                has_golden = excluded.has_golden,
                updated_at = excluded.updated_at,
                -- 文件回来了 -> 恢复 active
                case_status = CASE
                    WHEN cases.case_status = 'deprecated'
                         AND cases.deprecated_reason = 'file_deleted'
                    THEN 'active'
                    ELSE cases.case_status
                END,
                deprecated_at = CASE
                    WHEN cases.case_status = 'deprecated'
                         AND cases.deprecated_reason = 'file_deleted'
                    THEN NULL
                    ELSE cases.deprecated_at
                END,
                deprecated_reason = CASE
                    WHEN cases.case_status = 'deprecated'
                         AND cases.deprecated_reason = 'file_deleted'
                    THEN NULL
                    ELSE cases.deprecated_reason
                END""",
            meta + (now,),
        )

        # 检查是否恢复
        row = self.conn.execute(
            "SELECT case_status FROM cases WHERE case_id = ?",
            (case.case_id,),
        ).fetchone()
        return 1 if row and row["case_status"] == "active" else 0

    def _extract_agent_meta(self, case: AgentCase, file_path: str) -> tuple:
        """从 AgentCase 提取元数据。"""
        return (
            case.case_id,
            "agent",
            case.title,
            file_path,
            case.version,
            case.priority.value,
            case.scenario,
            json.dumps(case.dimensions, ensure_ascii=False),
            json.dumps(case.tags, ensure_ascii=False),
            case.evaluation.method,
            len(case.conversation),
            1 if case.mock else 0,
            1 if case.trace else 0,
            1 if case.golden else 0,
        )

    def _extract_api_meta(self, case: APICase, file_path: str) -> tuple:
        """从 APICase 提取元数据。APICase 没有 dimensions/trace/golden 字段。"""
        return (
            case.case_id,
            "api",
            case.title,
            file_path,
            case.version,
            case.priority.value,
            case.scenario,
            "[]",  # APICase 无 dimensions
            json.dumps(case.tags, ensure_ascii=False),
            case.evaluation.method,
            0,
            0,
            0,
            0,
        )

    # ==================== 查询 ====================

    def search(
        self,
        case_type: str | None = None,
        version: str | None = None,
        priority: str | None = None,
        scenario: str | None = None,
        dimension: str | None = None,
        tag: str | None = None,
        case_status: str = "active",
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        """多条件查询。

        Args:
            case_type: "api" / "agent" / None（不限）
            version: 如 "v0.3"
            priority: "P0" / "P1" / ...
            scenario: 场景名
            dimension: 评测维度（模糊匹配，如 "D9"）
            tag: 标签（模糊匹配）
            case_status: "active"（默认）/ "deprecated" / "all"
            keyword: 关键词（匹配 title / scenario / case_id）
        """
        query = "SELECT * FROM cases WHERE 1=1"
        params: list[Any] = []

        if case_status != "all":
            query += " AND case_status = ?"
            params.append(case_status)

        if case_type:
            query += " AND case_type = ?"
            params.append(case_type)

        if version:
            query += " AND version = ?"
            params.append(version)

        if priority:
            query += " AND priority = ?"
            params.append(priority)

        if scenario:
            query += " AND scenario = ?"
            params.append(scenario)

        if dimension:
            query += " AND dimensions LIKE ?"
            params.append(f"%{dimension}%")

        if tag:
            query += " AND tags LIKE ?"
            params.append(f"%{tag}%")

        if keyword:
            query += " AND (title LIKE ? OR scenario LIKE ? OR case_id LIKE ?)"
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])

        query += " ORDER BY case_id"
        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, case_id: str) -> dict[str, Any] | None:
        """获取单个 case 的元数据。"""
        row = self.conn.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_file_path(self, case_id: str) -> str | None:
        """case_id -> 文件路径。"""
        row = self.conn.execute(
            "SELECT file_path FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return row["file_path"] if row else None

    # ==================== 生命周期管理 ====================

    def update_status(
        self,
        case_id: str,
        status: str,
        reason: str = "",
    ) -> bool:
        """更新用例状态（逻辑删除/恢复）。

        Args:
            case_id: 用例 ID
            status: "active" / "deprecated"
            reason: 作废原因

        Returns:
            是否更新成功（case 不存在返回 False）
        """
        now = datetime.now().isoformat()
        if status == "deprecated":
            cursor = self.conn.execute(
                """UPDATE cases SET
                   case_status = 'deprecated',
                   deprecated_at = ?,
                   deprecated_reason = ?,
                   updated_at = ?
                   WHERE case_id = ?""",
                (now, reason, now, case_id),
            )
        else:
            cursor = self.conn.execute(
                """UPDATE cases SET
                   case_status = 'active',
                   deprecated_at = NULL,
                   deprecated_reason = NULL,
                   updated_at = ?
                   WHERE case_id = ?""",
                (now, case_id),
            )
        self.conn.commit()
        return cursor.rowcount > 0

    # ==================== 统计 ====================

    def get_statistics(self) -> dict[str, Any]:
        """聚合统计：按优先级/类型/场景/状态。"""
        stats: dict[str, Any] = {}

        # 按状态
        stats["by_status"] = {
            row["case_status"]: row["cnt"]
            for row in self.conn.execute(
                "SELECT case_status, COUNT(*) as cnt FROM cases GROUP BY case_status"
            ).fetchall()
        }

        # 按优先级（仅 active）
        stats["by_priority"] = {
            row["priority"]: row["cnt"]
            for row in self.conn.execute(
                """SELECT priority, COUNT(*) as cnt FROM cases
                   WHERE case_status = 'active' GROUP BY priority"""
            ).fetchall()
        }

        # 按类型（仅 active）
        stats["by_case_type"] = {
            row["case_type"]: row["cnt"]
            for row in self.conn.execute(
                """SELECT case_type, COUNT(*) as cnt FROM cases
                   WHERE case_status = 'active' GROUP BY case_type"""
            ).fetchall()
        }

        # 按场景（仅 active）
        stats["by_scenario"] = {
            row["scenario"]: row["cnt"]
            for row in self.conn.execute(
                """SELECT scenario, COUNT(*) as cnt FROM cases
                   WHERE case_status = 'active'
                   GROUP BY scenario ORDER BY cnt DESC"""
            ).fetchall()
        }

        # 按维度（仅 active，dimensions 是 JSON array）
        dim_count: dict[str, int] = {}
        for row in self.conn.execute(
            "SELECT dimensions FROM cases WHERE case_status = 'active'"
        ).fetchall():
            for dim in json.loads(row["dimensions"]):
                key = dim.split(".")[0] if "." in dim else dim
                dim_count[key] = dim_count.get(key, 0) + 1
        stats["by_dimension"] = dict(
            sorted(dim_count.items(), key=lambda x: -x[1])
        )

        stats["total"] = self.conn.execute(
            "SELECT COUNT(*) FROM cases"
        ).fetchone()[0]

        return stats

    # ==================== 工具 ====================

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """sqlite3.Row -> dict，JSON array 字段还原为 list。"""
        d = dict(row)
        d["dimensions"] = json.loads(d["dimensions"])
        d["tags"] = json.loads(d["tags"])
        return d

    def close(self) -> None:
        self.conn.close()
