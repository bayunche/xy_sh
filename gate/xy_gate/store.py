"""sqlite 状态存储（WAL，同步访问；流量很小，无需异步驱动）。

表：
- messages   聊天史（含机器人发出方向），供大脑 job 注入上下文
- events     事件与大脑 job 台账
- confirm_log 自动确认交易台账（幂等/审计）
- kv         杂项状态（大脑冷却时间、审计上次运行等）
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class Store:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()   # 可重入：conversations 聚合内嵌套调 kv_get
        self._migrate()

    def _migrate(self) -> None:
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL, chat_id TEXT, msg_id TEXT, direction TEXT,
                    sender_id TEXT, sender_name TEXT, text TEXT, item_id TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_msgid ON messages(msg_id)
                WHERE msg_id != '';
                CREATE INDEX IF NOT EXISTS ix_messages_chat ON messages(chat_id, id);

                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL, kind TEXT, chat_id TEXT, item_id TEXT, summary TEXT,
                    brain_job TEXT, brain_status TEXT, brain_output TEXT
                );

                CREATE TABLE IF NOT EXISTS confirm_log(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL, order_id TEXT UNIQUE, item_id TEXT, buyer_id TEXT,
                    amount REAL, decision TEXT, result TEXT, simulated INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
                """
            )

    # ── 消息 ──────────────────────────────────────────────────────────

    def add_message(self, chat_id: str, msg_id: str, direction: str, sender_id: str,
                    sender_name: str, text: str, item_id: str = "") -> bool:
        """入库；msg_id 非空且已存在时返回 False（消息去重）。"""
        with self._lock, self._db:
            try:
                self._db.execute(
                    "INSERT INTO messages(ts, chat_id, msg_id, direction, sender_id, sender_name, text, item_id)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (time.time(), chat_id, msg_id or "", direction, sender_id, sender_name,
                     (text or "")[:2000], item_id),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def history(self, chat_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, direction, sender_name, text, item_id FROM messages"
                " WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                (chat_id, limit),
            ).fetchall()
        return [
            {"ts": r[0], "direction": r[1], "sender": r[2], "text": r[3], "item_id": r[4]}
            for r in reversed(rows)
        ]

    def count_replies_last_hour(self) -> int:
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM messages WHERE direction='out' AND ts > ?",
                (time.time() - 3600,),
            ).fetchone()[0]

    # ── 会话（按买家 chat_id 聚合，消息页用）──────────────────────────

    def conversations(self, limit: int = 100) -> List[Dict[str, Any]]:
        """按买家会话聚合：昵称/最近消息/未读数（未读 = in 消息 ts > 已读水位）。"""
        with self._lock:
            rows = self._db.execute(
                "SELECT chat_id, MAX(ts) AS last_ts, COUNT(*) AS cnt FROM messages"
                " WHERE chat_id != '' GROUP BY chat_id ORDER BY last_ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            out = []
            for chat_id, last_ts, cnt in rows:
                last = self._db.execute(
                    "SELECT direction, sender_name, text FROM messages"
                    " WHERE chat_id=? ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
                buyer = self._db.execute(
                    "SELECT sender_id, sender_name FROM messages"
                    " WHERE chat_id=? AND direction='in' ORDER BY id DESC LIMIT 1",
                    (chat_id,)).fetchone()
                read_upto = float(self.kv_get(f"msg_read:{chat_id}") or 0)
                unread = self._db.execute(
                    "SELECT COUNT(*) FROM messages"
                    " WHERE chat_id=? AND direction='in' AND ts > ?",
                    (chat_id, read_upto)).fetchone()[0]
                out.append({
                    "chat_id": chat_id,
                    "buyer_id": buyer[0] if buyer else "",
                    "buyer_name": buyer[1] if buyer else chat_id,
                    "last_msg": (last[2] or "")[:80] if last else "",
                    "last_direction": last[0] if last else "",
                    "last_ts": last_ts, "count": cnt, "unread": unread,
                })
        return out

    def mark_conversation_read(self, chat_id: str) -> None:
        self.kv_set(f"msg_read:{chat_id}", str(time.time()))

    # ── 事件 ──────────────────────────────────────────────────────────

    def add_event(self, kind: str, chat_id: str = "", item_id: str = "", summary: str = "",
                  brain_job: str = "", brain_status: str = "", brain_output: str = "") -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO events(ts, kind, chat_id, item_id, summary, brain_job, brain_status, brain_output)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (time.time(), kind, chat_id, item_id, summary[:500], brain_job[:200],
                 brain_status, brain_output[:4000]),
            )
            return cur.lastrowid

    def update_event_brain(self, event_id: int, status: str, output: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE events SET brain_status=?, brain_output=? WHERE id=?",
                (status, output[:4000], event_id),
            )

    def recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, ts, kind, chat_id, item_id, summary, brain_status FROM events"
                " ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "kind": r[2], "chat_id": r[3], "item_id": r[4],
             "summary": r[5], "brain_status": r[6]}
            for r in rows
        ]

    # ── 确认台账 ──────────────────────────────────────────────────────

    def confirm_last_time(self, order_id: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute(
                "SELECT datetime(ts,'unixepoch','localtime') FROM confirm_log WHERE order_id=?",
                (order_id,),
            ).fetchone()
        return row[0] if row else None

    def record_confirm(self, order_id: str, item_id: str, buyer_id: str,
                       amount: Optional[float], decision: str, result: str,
                       simulated: bool) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO confirm_log(ts, order_id, item_id, buyer_id, amount, decision, result, simulated)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (time.time(), order_id, item_id, buyer_id, amount, decision, result[:1000],
                 1 if simulated else 0),
            )

    def confirm_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, order_id, item_id, buyer_id, amount, decision, result, simulated"
                " FROM confirm_log ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [
            {"ts": r[0], "order_id": r[1], "item_id": r[2], "buyer_id": r[3], "amount": r[4],
             "decision": r[5], "result": r[6], "simulated": bool(r[7])}
            for r in rows
        ]

    # ── 代订酒店比价缓存 ──────────────────────────────────────────────

    def _ensure_hotel_quotes_table(self) -> None:
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS hotel_quotes("
                "k TEXT PRIMARY KEY, ts REAL, quotes TEXT)")

    def hotel_quote_cache_get(self, hotel, date, nights, room):
        k = f"{hotel}|{date}|{nights}|{room}"
        with self._lock:
            row = self._db.execute(
                "SELECT ts, quotes FROM hotel_quotes WHERE k=?", (k,)).fetchone()
        if not row:
            return None
        return row[0], json.loads(row[1])

    def hotel_quote_cache_set(self, hotel, date, nights, room, quotes):
        k = f"{hotel}|{date}|{nights}|{room}"
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO hotel_quotes(k, ts, quotes) VALUES(?,?,?)",
                (k, time.time(), json.dumps(quotes, ensure_ascii=False)))

    def hotel_quote_cache_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM hotel_quotes").fetchone()[0]

    # ── kv ────────────────────────────────────────────────────────────

    def kv_set(self, k: str, v: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?,?)", (k, str(v)))

    def kv_get(self, k: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._db.close()
