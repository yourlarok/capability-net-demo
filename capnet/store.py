"""持久化注册表：SQLite + 自维护倒排索引。

设计目标：4 万张卡下查询毫秒级、索引随写入增量更新、零第三方依赖。
- entries：拍平后的路由条目（应用卡的 capability 维度）；
- postings/idf：倒排索引，w 为该 token 在该条目中的最大字段权重（intents 1.0 / name 0.6 / desc 0.3）；
- events/seeds：遥测事件与种子统计，打分侧按需聚合（带半衰期衰减）；
- review_status=draft 的条目不进入路由池，先评审。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time

from .cards import AppCard, Card, flatten
from .scoring import Stats
from .tokens import FIELD_WEIGHTS, tokenize

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id TEXT PRIMARY KEY, app_id TEXT, name TEXT, vendor TEXT, domain TEXT,
  description TEXT, cost REAL, latency TEXT, accessibility TEXT,
  healthy INTEGER, review_status TEXT, true_quality REAL,
  inputs TEXT, outputs TEXT, intents TEXT, extras TEXT, popularity TEXT, access TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS postings (token TEXT, entry_id TEXT, w REAL);
CREATE INDEX IF NOT EXISTS idx_postings_token ON postings(token);
CREATE INDEX IF NOT EXISTS idx_postings_entry ON postings(entry_id);
CREATE TABLE IF NOT EXISTS idf (token TEXT PRIMARY KEY, df INTEGER, idf REAL);
CREATE TABLE IF NOT EXISTS events (entry_id TEXT, kind TEXT, t REAL, value REAL);
CREATE INDEX IF NOT EXISTS idx_events_entry ON events(entry_id);
CREATE TABLE IF NOT EXISTS seeds (
  entry_id TEXT PRIMARY KEY, exposures REAL, selections REAL, successes REAL,
  failures REAL, approvals REAL, rating_sum REAL, rating_count REAL
);
"""


def _field_weighted_tokens(card: Card) -> dict[str, float]:
    fields = {
        "intent": set(tokenize(" ".join(card.intents))),
        "name": set(tokenize(card.name)),
        "desc": set(tokenize(card.description + " " + card.domain)),
    }
    weights: dict[str, float] = {}
    for fname, tokens in fields.items():
        for t in tokens:
            weights[t] = max(weights.get(t, 0.0), FIELD_WEIGHTS[fname])
    return weights


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(entries)")}
        if "popularity" not in cols:  # 旧库迁移
            self.conn.execute("ALTER TABLE entries ADD COLUMN popularity TEXT NOT NULL DEFAULT '{}'")
        if "access" not in cols:
            self.conn.execute("ALTER TABLE entries ADD COLUMN access TEXT NOT NULL DEFAULT '{}'")

    def close(self) -> None:
        self.conn.close()

    # ---------- 写入 ----------

    def upsert_apps(self, apps: list[AppCard]) -> int:
        n = 0
        with self.conn:
            for app in apps:
                for card in flatten(app):
                    self._upsert_entry(card)
                    n += 1
        return n

    def _upsert_entry(self, c: Card) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO entries
               (id, app_id, name, vendor, domain, description, cost, latency,
                accessibility, healthy, review_status, true_quality, inputs, outputs, intents, extras, popularity, access)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (c.id, c.app_id, c.name, c.vendor, c.domain, c.description, c.cost_per_call,
             c.latency, c.accessibility, int(c.healthy), "approved", c.true_quality,
             json.dumps(c.inputs, ensure_ascii=False), json.dumps(c.outputs, ensure_ascii=False),
             json.dumps(c.intents, ensure_ascii=False), json.dumps(c.extras, ensure_ascii=False),
             json.dumps(c.popularity, ensure_ascii=False), json.dumps(c.access, ensure_ascii=False)),
        )
        self.conn.execute("DELETE FROM postings WHERE entry_id = ?", (c.id,))
        rows = [(t, c.id, w) for t, w in _field_weighted_tokens(c).items()]
        self.conn.executemany("INSERT INTO postings (token, entry_id, w) VALUES (?,?,?)", rows)

    def upsert_draft(self, app: AppCard) -> None:
        with self.conn:
            for card in flatten(app):
                self._upsert_entry(card)
                self.conn.execute("UPDATE entries SET review_status = ? WHERE id = ?",
                                  (app.review_status, card.id))

    def rebuild_idf(self) -> None:
        with self.conn:
            n = self.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] or 1
            self.conn.execute("DELETE FROM idf")
            self.conn.execute(
                "INSERT INTO idf SELECT token, COUNT(DISTINCT entry_id), 0.0 FROM postings GROUP BY token"
            )
            self.conn.execute("UPDATE idf SET idf = ln(1.0 + ? * 1.0 / df)", (n,))

    # ---------- 检索 ----------

    def recall(self, query: str, domain: str | None, limit: int, min_relevance: float) -> list[tuple[str, float]]:
        q_tokens = {}
        for t in tokenize(query):
            q_tokens[t] = True
        if not q_tokens:
            return []
        marks = ",".join("?" * len(q_tokens))
        idf_rows = self.conn.execute(
            f"SELECT token, idf FROM idf WHERE token IN ({marks})", tuple(q_tokens)
        ).fetchall()
        if not idf_rows:
            return []
        total = sum(r[1] for r in idf_rows)
        if total <= 0:
            return []
        sql = f"""
          SELECT p.entry_id, SUM(p.w * i.idf) / ? AS rel
          FROM postings p JOIN idf i ON i.token = p.token
          JOIN entries e ON e.id = p.entry_id
          WHERE p.token IN ({marks}) AND e.healthy = 1 AND e.review_status = 'approved'
        """
        params: list = [total, *q_tokens]
        if domain:
            sql += " AND (e.domain = ? OR e.domain LIKE ?)"
            params += [domain, domain + ".%"]
        sql += " GROUP BY p.entry_id HAVING rel >= ? ORDER BY rel DESC LIMIT ?"
        params += [min_relevance, limit]
        return [(r[0], r[1]) for r in self.conn.execute(sql, params).fetchall()]

    def idf_for(self, tokens: list[str]) -> dict[str, float]:
        """查 token 的 idf；未收录的 token 不在结果中（调用方按最大稀有度处理）。"""
        if not tokens:
            return {}
        marks = ",".join("?" * len(tokens))
        return {r[0]: r[1] for r in self.conn.execute(
            f"SELECT token, idf FROM idf WHERE token IN ({marks})", tuple(tokens))}

    def list_domain(self, domain: str | None) -> list[str]:
        if domain:
            rows = self.conn.execute(
                "SELECT id FROM entries WHERE healthy = 1 AND review_status = 'approved' "
                "AND (domain = ? OR domain LIKE ?)", (domain, domain + ".%")).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id FROM entries WHERE healthy = 1 AND review_status = 'approved'").fetchall()
        return [r[0] for r in rows]

    def get_entries(self, ids: list[str]) -> dict[str, Card]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT id, app_id, name, vendor, domain, description, cost, latency, accessibility,"
            f" healthy, true_quality, inputs, outputs, intents, extras, popularity, access FROM entries WHERE id IN ({marks})",
            tuple(ids)).fetchall()
        out = {}
        for r in rows:
            out[r[0]] = Card(
                id=r[0], app_id=r[1], name=r[2], vendor=r[3], domain=r[4], description=r[5],
                cost_per_call=r[6], latency=r[7], accessibility=r[8], transport=r[8],
                healthy=bool(r[9]), true_quality=r[10],
                inputs=json.loads(r[11]), outputs=json.loads(r[12]), intents=json.loads(r[13]),
                extras=json.loads(r[14]), popularity=json.loads(r[15]), access=json.loads(r[16] or "{}"),
            )
        return out

    # ---------- 遥测 ----------

    def record(self, entry_id: str, kind: str, t: float, value: float = 1.0) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO events VALUES (?,?,?,?)", (entry_id, kind, t, value))

    def add_seed(self, entry_id: str, stats: Stats) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO seeds VALUES (?,?,?,?,?,?,?,?)",
                (entry_id, stats.exposures, stats.selections, stats.successes,
                 stats.failures, stats.approvals, stats.rating_sum, stats.rating_count))

    def stats_for(self, ids: list[str], now: float, half_life: float) -> dict[str, Stats]:
        out: dict[str, Stats] = {}
        if not ids:
            return out
        marks = ",".join("?" * len(ids))
        for r in self.conn.execute(
                f"SELECT entry_id, exposures, selections, successes, failures, approvals,"
                f" rating_sum, rating_count FROM seeds WHERE entry_id IN ({marks})", tuple(ids)):
            out[r[0]] = Stats(exposures=r[1], selections=r[2], successes=r[3], failures=r[4],
                              approvals=r[5], rating_sum=r[6], rating_count=r[7])
        hl = max(half_life, 1e-9)
        for r in self.conn.execute(
                f"SELECT entry_id, kind, SUM(value * pow(0.5, MAX(0.0, (? - t)) / ?)) FROM events"
                f" WHERE entry_id IN ({marks}) GROUP BY entry_id, kind",
                (now, hl, *ids)):
            st = out.setdefault(r[0], Stats())
            if r[1] == "rating":
                st.rating_sum += r[2] or 0.0
            else:
                attr = {"exposure": "exposures", "selection": "selections", "success": "successes",
                        "failure": "failures", "approval": "approvals"}.get(r[1])
                if attr:
                    setattr(st, attr, getattr(st, attr) + (r[2] or 0.0))
        for r in self.conn.execute(
                f"SELECT entry_id, SUM(pow(0.5, MAX(0.0, (? - t)) / ?)) FROM events WHERE kind = 'rating'"
                f" AND entry_id IN ({marks}) GROUP BY entry_id", (now, hl, *ids)):
            out.setdefault(r[0], Stats()).rating_count += r[1] or 0.0
        return out

    def total_selections(self, now: float = 0.0, half_life: float = 30.0) -> float:
        row = self.conn.execute("SELECT SUM(selections) FROM seeds").fetchone()
        seed_total = row[0] or 0.0
        hl = max(half_life, 1e-9)
        row = self.conn.execute(
            "SELECT SUM(value * pow(0.5, MAX(0.0, (? - t)) / ?)) FROM events WHERE kind = 'selection'",
            (now, hl)).fetchone()
        return seed_total + (row[0] or 0.0)

    # ---------- 评审与统计 ----------

    def approve(self, entry_prefix: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE entries SET review_status = 'approved' WHERE review_status = 'draft'"
                " AND (app_id = ? OR domain = ?)", (entry_prefix, entry_prefix))
            return cur.rowcount

    def counts(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        drafts = self.conn.execute(
            "SELECT COUNT(*) FROM entries WHERE review_status = 'draft'").fetchone()[0]
        by_domain = self.conn.execute(
            "SELECT domain, COUNT(*) FROM entries GROUP BY domain ORDER BY 2 DESC").fetchall()
        return {"entries": total, "drafts": drafts, "approved": total - drafts,
                "by_domain": dict(by_domain)}
