import sqlite3
import time
import uuid
from typing import List, Optional, Dict, Any
from contextlib import contextmanager
from app.config import settings

def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or settings.DATABASE_PATH
    conn = sqlite3.connect(path, timeout=15.0, isolation_level=None) # autocommit mode with explicit transactions
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for high concurrency read/write
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=15000;")
    return conn

@contextmanager
def db_transaction(db_path: Optional[str] = None):
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        yield conn
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()

def init_db(db_path: Optional[str] = None):
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # 1. Rules table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                rule_id TEXT PRIMARY KEY,
                keyword TEXT NOT NULL,
                dm_message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        
        # 2. Processed events table (Deduplication on event_id)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                received_at REAL NOT NULL
            );
        """)
        
        # 3. Comments table (track deletion status)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                comment_id TEXT PRIMARY KEY,
                post_id TEXT,
                user_id TEXT,
                text TEXT,
                is_deleted INTEGER DEFAULT 0,
                created_at TEXT
            );
        """)
        
        # 4. User Rule Executions (Atomic Unique constraint for (user_id, rule_id))
        # "The same user never gets DMed twice for the same rule, no matter how many times they comment."
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_rule_executions (
                user_id TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                first_comment_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (user_id, rule_id)
            );
        """)
        
        # 5. DM Jobs (Persistent Outbound & Reconciliation Queue)
        # Statuses: 'queued', 'sending', 'reconciling', 'delivered', 'failed', 'cancelled'
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dm_jobs (
                job_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                comment_id TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL,
                idempotency_key TEXT UNIQUE,
                dm_id TEXT,
                retry_count INTEGER DEFAULT 0,
                next_retry_at REAL NOT NULL,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_dm_jobs_status_retry ON dm_jobs(status, next_retry_at);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_dm_jobs_comment ON dm_jobs(comment_id);")
        
        # 6. Stats log for blocked duplicates / cancellations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stats_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric TEXT NOT NULL,
                detail TEXT,
                occurred_at REAL NOT NULL
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stats_metric ON stats_log(metric);")

# ----------------- Rules Operations -----------------

def create_rule(keyword: str, dm_message: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    rule_id = f"rule_{uuid.uuid4().hex[:12]}"
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO rules (rule_id, keyword, dm_message, created_at) VALUES (?, ?, ?, ?)",
            (rule_id, keyword, dm_message, created_at)
        )
    return {
        "rule_id": rule_id,
        "keyword": keyword,
        "dm_message": dm_message,
        "created_at": created_at
    }

def get_all_rules(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT rule_id, keyword, dm_message, created_at FROM rules").fetchall()
        return [dict(row) for row in rows]

# ----------------- Events & Comments -----------------

def record_event_if_new(event_id: str, event_type: str, db_path: Optional[str] = None) -> bool:
    """Returns True if event is newly recorded, False if already exists (duplicate)."""
    now = time.time()
    try:
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO events (event_id, event_type, received_at) VALUES (?, ?, ?)",
                (event_id, event_type, now)
            )
            return True
    except sqlite3.IntegrityError:
        return False

def upsert_comment(comment_id: str, post_id: Optional[str], user_id: Optional[str], text: Optional[str], created_at: Optional[str], db_path: Optional[str] = None):
    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO comments (comment_id, post_id, user_id, text, is_deleted, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
            ON CONFLICT(comment_id) DO UPDATE SET
                post_id = COALESCE(excluded.post_id, comments.post_id),
                user_id = COALESCE(excluded.user_id, comments.user_id),
                text = COALESCE(excluded.text, comments.text),
                created_at = COALESCE(excluded.created_at, comments.created_at)
        """, (comment_id, post_id, user_id, text, created_at))

def mark_comment_deleted(comment_id: str, db_path: Optional[str] = None) -> int:
    """Marks comment deleted and cancels any pending/queued DM jobs for this comment."""
    with db_transaction(db_path) as conn:
        # Ensure comment record exists and is_deleted = 1
        conn.execute("""
            INSERT INTO comments (comment_id, is_deleted)
            VALUES (?, 1)
            ON CONFLICT(comment_id) DO UPDATE SET is_deleted = 1
        """, (comment_id,))
        
        # Cancel any dm_jobs for this comment that have NOT yet been sent / accepted
        cursor = conn.execute("""
            UPDATE dm_jobs
            SET status = 'cancelled', updated_at = ?, last_error = 'comment_deleted_before_send'
            WHERE comment_id = ? AND status IN ('queued', 'sending')
        """, (time.time(), comment_id))
        cancelled_count = cursor.rowcount
        
        if cancelled_count > 0:
            conn.execute(
                "INSERT INTO stats_log (metric, detail, occurred_at) VALUES (?, ?, ?)",
                ("comment_deleted_cancelled", f"Cancelled {cancelled_count} jobs for {comment_id}", time.time())
            )
        return cancelled_count

def is_comment_deleted(comment_id: str, db_path: Optional[str] = None) -> bool:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT is_deleted FROM comments WHERE comment_id = ?", (comment_id,)).fetchone()
        return bool(row and row["is_deleted"] == 1)

# ----------------- User Rule Atomic Deduplication & Enqueueing -----------------

def try_record_user_rule_and_enqueue_dm(
    user_id: str,
    rule_id: str,
    comment_id: str,
    message: str,
    db_path: Optional[str] = None
) -> Optional[str]:
    """
    Atomically attempts to record that this user matched this rule.
    If the user has ALREADY matched this rule previously:
      - Returns None (duplicate blocked, logs duplicate metric).
    If new:
      - Inserts into user_rule_executions.
      - Enqueues a new dm_job with status 'queued'.
      - Returns job_id.
    """
    now = time.time()
    job_id = f"job_{uuid.uuid4().hex[:14]}"
    idempotency_key = f"dm_{user_id}_{rule_id}"
    
    try:
        with db_transaction(db_path) as conn:
            # 1. Insert into user_rule_executions (will raise IntegrityError if (user_id, rule_id) exists)
            conn.execute(
                "INSERT INTO user_rule_executions (user_id, rule_id, first_comment_id, created_at) VALUES (?, ?, ?, ?)",
                (user_id, rule_id, comment_id, now)
            )
            
            # 2. Insert into dm_jobs
            conn.execute("""
                INSERT INTO dm_jobs (
                    job_id, user_id, rule_id, comment_id, message, status,
                    idempotency_key, retry_count, next_retry_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, 0, ?, ?, ?)
            """, (job_id, user_id, rule_id, comment_id, message, idempotency_key, now, now, now))
            
            return job_id
    except sqlite3.IntegrityError:
        # Duplicate user-rule combination!
        record_stats_metric("duplicate_user_rule", f"user={user_id}, rule={rule_id}", db_path=db_path)
        return None

def record_stats_metric(metric: str, detail: Optional[str] = None, db_path: Optional[str] = None):
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO stats_log (metric, detail, occurred_at) VALUES (?, ?, ?)",
            (metric, detail, time.time())
        )

# ----------------- DM Jobs Queue Operations -----------------

def get_next_pending_job(db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetches the oldest queued job ready for dispatch and atomically sets status to 'sending'."""
    now = time.time()
    with db_transaction(db_path) as conn:
        row = conn.execute("""
            SELECT job_id, user_id, rule_id, comment_id, message, idempotency_key, retry_count
            FROM dm_jobs
            WHERE status = 'queued' AND next_retry_at <= ?
            ORDER BY created_at ASC
            LIMIT 1
        """, (now,)).fetchone()
        
        if not row:
            return None
            
        job_data = dict(row)
        conn.execute(
            "UPDATE dm_jobs SET status = 'sending', updated_at = ? WHERE job_id = ?",
            (now, job_data["job_id"])
        )
        return job_data

def update_job_accepted(job_id: str, dm_id: str, db_path: Optional[str] = None):
    """Sets job status to 'reconciling' once the mock API returns 202 Accepted."""
    now = time.time()
    with get_connection(db_path) as conn:
        conn.execute("""
            UPDATE dm_jobs
            SET status = 'reconciling', dm_id = ?, updated_at = ?
            WHERE job_id = ?
        """, (dm_id, now, job_id))

def update_job_delivered(job_id: str, db_path: Optional[str] = None):
    """Marks job as terminal 'delivered'."""
    now = time.time()
    with get_connection(db_path) as conn:
        conn.execute("""
            UPDATE dm_jobs
            SET status = 'delivered', updated_at = ?
            WHERE job_id = ?
        """, (now, job_id))

def update_job_failed(job_id: str, error_msg: str, db_path: Optional[str] = None):
    """Marks job as terminal 'failed'."""
    now = time.time()
    with get_connection(db_path) as conn:
        conn.execute("""
            UPDATE dm_jobs
            SET status = 'failed', last_error = ?, updated_at = ?
            WHERE job_id = ?
        """, (error_msg, now, job_id))

def schedule_job_retry(job_id: str, retry_count: int, delay_seconds: float, error_msg: str, db_path: Optional[str] = None):
    """Reschedules job to 'queued' with exponential backoff / delay."""
    now = time.time()
    next_retry = now + delay_seconds
    with get_connection(db_path) as conn:
        conn.execute("""
            UPDATE dm_jobs
            SET status = 'queued', retry_count = ?, next_retry_at = ?, last_error = ?, updated_at = ?
            WHERE job_id = ?
        """, (retry_count, next_retry, error_msg, now, job_id))

def get_reconciling_jobs(limit: int = 50, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Gets jobs currently in 'reconciling' state that have a dm_id."""
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT job_id, dm_id, user_id, rule_id, comment_id, message, retry_count, updated_at
            FROM dm_jobs
            WHERE status = 'reconciling' AND dm_id IS NOT NULL
            ORDER BY updated_at ASC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]

# ----------------- Stats & Reporting -----------------

def get_stats_summary(db_path: Optional[str] = None) -> Dict[str, int]:
    """
    Computes real-time accurate counts for GET /stats:
    - sent: confirmed delivered
    - failed: given up after retries or terminal failure
    - queued: waiting to send, waiting on retry, or reconciling
    - duplicates_blocked: DMs correctly chosen not to send (repeated event_ids, repeat user_rules, cancelled deleted comments)
    """
    with get_connection(db_path) as conn:
        # 1. Sent (delivered)
        sent_row = conn.execute("SELECT COUNT(*) AS c FROM dm_jobs WHERE status = 'delivered'").fetchone()
        sent_count = sent_row["c"] if sent_row else 0
        
        # 2. Failed
        failed_row = conn.execute("SELECT COUNT(*) AS c FROM dm_jobs WHERE status = 'failed'").fetchone()
        failed_count = failed_row["c"] if failed_row else 0
        
        # 3. Queued (queued, sending, reconciling)
        queued_row = conn.execute("SELECT COUNT(*) AS c FROM dm_jobs WHERE status IN ('queued', 'sending', 'reconciling')").fetchone()
        queued_count = queued_row["c"] if queued_row else 0
        
        # 4. Duplicates Blocked:
        # Sum of:
        # - stats_log metric entries (duplicate_event, duplicate_user_rule, comment_deleted_cancelled)
        # - dm_jobs with status 'cancelled'
        dup_row = conn.execute("SELECT COUNT(*) AS c FROM stats_log").fetchone()
        dup_count = dup_row["c"] if dup_row else 0
        
        return {
            "sent": sent_count,
            "failed": failed_count,
            "queued": queued_count,
            "duplicates_blocked": dup_count
        }

def get_recent_jobs(limit: int = 100, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT job_id, user_id, rule_id, comment_id, message, status, dm_id, retry_count, last_error, created_at, updated_at
            FROM dm_jobs
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]
