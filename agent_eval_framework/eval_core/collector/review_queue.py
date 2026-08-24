"""ReviewQueue：审核队列（SQLite），管理搜集 case 的审核流转。

对应《Agent评测框架设计方案》4.3 用例搜集模块。

状态流转：
  pending（待审核）-> approved（通过，写入 cases/）
                  -> rejected（驳回，记录原因）

自动通过规则：分类置信度 >= auto_approve_threshold（默认 0.8）
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from eval_core.utils import logger


class ReviewQueue:
    """审核队列"""

    def __init__(self, db_path: Path | str = "review_queue.db"):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_table()

    def _init_table(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT UNIQUE NOT NULL,
                case_json TEXT NOT NULL,
                classification TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                status TEXT DEFAULT 'pending',
                reviewer TEXT,
                reviewed_at TEXT,
                review_reason TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status);
        """)
        self.conn.commit()

    def enqueue(
        self,
        case: dict,
        classification: dict,
        source_type: str,
        source_id: str,
    ) -> str:
        """入队（状态 pending）。

        Returns:
            case_id
        """
        now = datetime.now().isoformat()
        self.conn.execute(
            """INSERT INTO review_queue
               (case_id, case_json, classification, source_type, source_id,
                confidence, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                case["case_id"],
                json.dumps(case, ensure_ascii=False),
                json.dumps(classification, ensure_ascii=False),
                source_type,
                source_id,
                classification.get("confidence", 0),
                now,
            ),
        )
        self.conn.commit()
        logger.debug(f"入队: {case['case_id']} (置信度 {classification.get('confidence', 0):.2f})")
        return case["case_id"]

    def list(self, status: str = "pending") -> list[dict]:
        """按状态查询队列。"""
        rows = self.conn.execute(
            """SELECT case_id, source_type, confidence, status,
                      created_at, review_reason
               FROM review_queue WHERE status = ?
               ORDER BY created_at DESC""",
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_case(self, case_id: str) -> dict | None:
        """获取完整 case JSON。"""
        row = self.conn.execute(
            "SELECT case_json FROM review_queue WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        return json.loads(row["case_json"]) if row else None

    def approve(self, case_id: str, reviewer: str = "") -> bool:
        """审核通过。"""
        cursor = self.conn.execute(
            """UPDATE review_queue SET
               status = 'approved', reviewer = ?, reviewed_at = ?
               WHERE case_id = ? AND status = 'pending'""",
            (reviewer, datetime.now().isoformat(), case_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def reject(self, case_id: str, reviewer: str = "", reason: str = "") -> bool:
        """驳回。"""
        cursor = self.conn.execute(
            """UPDATE review_queue SET
               status = 'rejected', reviewer = ?, reviewed_at = ?, review_reason = ?
               WHERE case_id = ? AND status = 'pending'""",
            (reviewer, datetime.now().isoformat(), reason, case_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def should_auto_approve(self, case_id: str, threshold: float = 0.8) -> bool:
        """判断是否可自动通过（置信度 >= threshold）。"""
        row = self.conn.execute(
            "SELECT confidence FROM review_queue WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        return row and row["confidence"] >= threshold

    def stats(self) -> dict:
        """队列统计。"""
        rows = self.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM review_queue GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def close(self):
        self.conn.close()
