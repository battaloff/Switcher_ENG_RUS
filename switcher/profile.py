"""Everything the switcher has learned about its user, stored locally in SQLite.

* rules — ``layout`` rules map a key sequence to the language the user wants
  it in ("ghbdtn" → ru, "ok" → en); ``replace`` rules fix the user's typical
  typos ("превет" → "привет").  Each rule remembers who made it: the user,
  the learner (from corrections) or the AI review.
* vocab — the user's own words (slang, names, jargon) with usage counts.
* app stats — how much the user writes in each language per application.
* tuning — per-app threshold offsets, grown or shrunk by feedback.
* events — a bounded journal of corrections, used by the AI review.

Hot data is cached in memory; the engine never waits on disk.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

from .layouts import EN, RU

SOURCE_PRIORITY = {"ai": 1, "learned": 2, "user": 3}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    kind TEXT NOT NULL, pattern TEXT NOT NULL, app TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL, source TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 0,
    misses INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '', PRIMARY KEY (kind, pattern, app));
CREATE TABLE IF NOT EXISTS vocab (
    lang TEXT NOT NULL, word TEXT NOT NULL, count INTEGER NOT NULL, last REAL NOT NULL,
    PRIMARY KEY (lang, word));
CREATE TABLE IF NOT EXISTS app_stats (
    app TEXT NOT NULL, lang TEXT NOT NULL, words INTEGER NOT NULL, PRIMARY KEY (app, lang));
CREATE TABLE IF NOT EXISTS tuning (
    app TEXT PRIMARY KEY, offset REAL NOT NULL DEFAULT 0, pref_lang TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,
    app TEXT NOT NULL DEFAULT '', keys TEXT NOT NULL DEFAULT '', typed_lang TEXT NOT NULL DEFAULT '',
    final_lang TEXT NOT NULL DEFAULT '', typed_text TEXT NOT NULL DEFAULT '',
    final_text TEXT NOT NULL DEFAULT '', margin REAL, detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class Rule:
    kind: str       # "layout" | "replace"
    pattern: str    # layout: key codes ("ghbdtn"); replace: the wrong word
    value: str      # layout: "en" | "ru"; replace: the right word
    app: str = ""   # "" = everywhere
    source: str = "learned"
    hits: int = 0
    misses: int = 0
    created: float = 0.0
    updated: float = 0.0
    note: str = ""


class Profile:
    def __init__(self, path: str | Path = ":memory:", journal_size: int = 5000, min_vocab_count: int = 2):
        self.path = str(path)
        self.journal_size = journal_size
        self.min_vocab_count = min_vocab_count
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._rules: dict[tuple[str, str, str], Rule] = {}
        self._vocab: dict[tuple[str, str], int] = {}
        self._app_stats: dict[str, dict[str, int]] = {}
        self._tuning: dict[str, tuple[float, str]] = {}
        self._dirty_vocab: set[tuple[str, str]] = set()
        self._dirty_stats: set[tuple[str, str]] = set()
        self._events_since_prune = 0
        self._load()

    # -- loading / flushing ------------------------------------------------

    def _load(self) -> None:
        with self._lock:
            for row in self._db.execute(
                "SELECT kind, pattern, value, app, source, hits, misses, created, updated, note FROM rules"
            ):
                rule = Rule(*row)
                self._rules[(rule.kind, rule.pattern, rule.app)] = rule
            self._vocab = {(l, w): c for l, w, c in self._db.execute("SELECT lang, word, count FROM vocab")}
            for app, lang, n in self._db.execute("SELECT app, lang, words FROM app_stats"):
                self._app_stats.setdefault(app, {})[lang] = n
            self._tuning = {a: (o, p) for a, o, p in self._db.execute("SELECT app, offset, pref_lang FROM tuning")}

    def flush(self) -> None:
        with self._lock:
            now = time.time()
            if self._dirty_vocab:
                self._db.executemany(
                    "INSERT INTO vocab(lang, word, count, last) VALUES(?,?,?,?) "
                    "ON CONFLICT(lang, word) DO UPDATE SET count=excluded.count, last=excluded.last",
                    [(l, w, self._vocab[(l, w)], now) for l, w in self._dirty_vocab if (l, w) in self._vocab],
                )
                self._dirty_vocab.clear()
            if self._dirty_stats:
                self._db.executemany(
                    "INSERT INTO app_stats(app, lang, words) VALUES(?,?,?) "
                    "ON CONFLICT(app, lang) DO UPDATE SET words=excluded.words",
                    [(a, l, self._app_stats[a][l]) for a, l in self._dirty_stats],
                )
                self._dirty_stats.clear()
            self._db.commit()

    def close(self) -> None:
        self.flush()
        with self._lock:
            self._db.close()

    # -- engine view (ProfileView) -----------------------------------------

    def layout_rule(self, keys: str, app: str) -> tuple[str, str] | None:
        for scope in (app, ""):
            rule = self._rules.get(("layout", keys, scope))
            if rule:
                return rule.value, rule.source
        return None

    def personal_zipf(self, word: str, lang: str) -> float | None:
        count = self._vocab.get((lang, word))
        if not count or count < self.min_vocab_count:
            return None
        return min(6.0, 2.5 + 0.8 * math.log2(count))

    def app_bias(self, app: str) -> float:
        stats = self._app_stats.get(app, {})
        ru, en = stats.get(RU, 0), stats.get(EN, 0)
        bias = (ru - en) / (ru + en + 10)
        pref = self._tuning.get(app, (0.0, ""))[1]
        if pref == RU:
            bias += 0.5
        elif pref == EN:
            bias -= 0.5
        return max(-1.0, min(1.0, bias))

    def keeps_prefix(self, keys: str, text: str, lang: str, app: str) -> bool:
        """The user taught us a word in ``lang`` starting with these keys: a rule or a personal word."""
        with self._lock:
            for (kind, pattern, scope), rule in self._rules.items():
                if kind == "layout" and rule.value == lang and scope in ("", app) and pattern.startswith(keys):
                    return True
            return any(l == lang and count >= self.min_vocab_count and word.startswith(text)
                       for (l, word), count in self._vocab.items())

    def threshold_offset(self, app: str) -> float:
        offset = self._tuning.get("", (0.0, ""))[0]
        if app:
            offset += self._tuning.get(app, (0.0, ""))[0]
        return max(-1.5, min(3.0, offset))

    # -- rules -------------------------------------------------------------

    def rules(self, kind: str | None = None) -> list[Rule]:
        with self._lock:
            return sorted(
                (r for r in self._rules.values() if kind is None or r.kind == kind),
                key=lambda r: (r.kind, r.app, r.pattern),
            )

    def get_rule(self, kind: str, pattern: str, app: str = "") -> Rule | None:
        return self._rules.get((kind, pattern, app))

    def replace_rule(self, word: str, app: str) -> Rule | None:
        for scope in (app, ""):
            rule = self._rules.get(("replace", word, scope))
            if rule:
                return rule
        return None

    def add_rule(self, kind: str, pattern: str, value: str, *, app: str = "", source: str = "learned",
                 note: str = "") -> Rule | None:
        """Create or update a rule; a weaker source never overrides a stronger one."""
        with self._lock:
            key = (kind, pattern, app)
            existing = self._rules.get(key)
            now = time.time()
            if existing:
                if existing.value == value:
                    existing.hits += 1
                    existing.updated = now
                    if SOURCE_PRIORITY[source] > SOURCE_PRIORITY[existing.source]:
                        existing.source = source
                    if note:
                        existing.note = note
                    self._save_rule(existing)
                    return existing
                if SOURCE_PRIORITY[source] < SOURCE_PRIORITY[existing.source]:
                    return None
            rule = Rule(kind, pattern, value, app, source, 1, 0, now, now, note)
            self._rules[key] = rule
            self._save_rule(rule)
            self.log_event("rule_added", app=app, keys=pattern if kind == "layout" else "",
                           final_lang=value if kind == "layout" else "",
                           typed_text=pattern if kind == "replace" else "",
                           final_text=value if kind == "replace" else "",
                           detail={"kind": kind, "source": source, "note": note})
            return rule

    def _save_rule(self, rule: Rule) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO rules(kind, pattern, value, app, source, hits, misses, created, updated, note) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (rule.kind, rule.pattern, rule.value, rule.app, rule.source, rule.hits, rule.misses,
             rule.created, rule.updated, rule.note),
        )
        self._db.commit()

    def rule_used(self, kind: str, pattern: str, app: str, ok: bool = True) -> None:
        with self._lock:
            for scope in (app, ""):
                rule = self._rules.get((kind, pattern, scope))
                if rule:
                    if ok:
                        rule.hits += 1
                    else:
                        rule.misses += 1
                    rule.updated = time.time()
                    self._save_rule(rule)
                    return

    def remove_rule(self, kind: str, pattern: str, app: str = "") -> bool:
        with self._lock:
            rule = self._rules.pop((kind, pattern, app), None)
            if rule is None:
                return False
            self._db.execute("DELETE FROM rules WHERE kind=? AND pattern=? AND app=?", (kind, pattern, app))
            self._db.commit()
            return True

    # -- vocabulary & stats ------------------------------------------------

    def bump_vocab(self, lang: str, word: str, n: int = 1) -> int:
        with self._lock:
            key = (lang, word)
            count = self._vocab.get(key, 0) + n
            self._vocab[key] = count
            self._dirty_vocab.add(key)
            return count

    def drop_vocab(self, lang: str, word: str) -> None:
        with self._lock:
            if self._vocab.pop((lang, word), None) is not None:
                self._dirty_vocab.discard((lang, word))
                self._db.execute("DELETE FROM vocab WHERE lang=? AND word=?", (lang, word))
                self._db.commit()

    def vocab(self, lang: str | None = None, limit: int = 50) -> list[tuple[str, str, int]]:
        items = [(l, w, c) for (l, w), c in self._vocab.items() if lang is None or l == lang]
        items.sort(key=lambda it: -it[2])
        return items[:limit]

    def count_word(self, app: str, lang: str) -> None:
        with self._lock:
            stats = self._app_stats.setdefault(app, {})
            stats[lang] = stats.get(lang, 0) + 1
            self._dirty_stats.add((app, lang))

    def app_stats(self) -> dict[str, dict[str, int]]:
        return {app: dict(v) for app, v in self._app_stats.items()}

    def adjust_threshold(self, app: str, delta: float, lo: float = -1.5, hi: float = 3.0) -> float:
        with self._lock:
            offset, pref = self._tuning.get(app, (0.0, ""))
            offset = max(lo, min(hi, offset + delta))
            self._tuning[app] = (offset, pref)
            self._db.execute(
                "INSERT INTO tuning(app, offset, pref_lang) VALUES(?,?,?) "
                "ON CONFLICT(app) DO UPDATE SET offset=excluded.offset",
                (app, offset, pref),
            )
            self._db.commit()
            return offset

    def set_app_preference(self, app: str, lang: str, note: str = "") -> None:
        with self._lock:
            offset, _ = self._tuning.get(app, (0.0, ""))
            self._tuning[app] = (offset, lang)
            self._db.execute(
                "INSERT INTO tuning(app, offset, pref_lang, note) VALUES(?,?,?,?) "
                "ON CONFLICT(app) DO UPDATE SET pref_lang=excluded.pref_lang, note=excluded.note",
                (app, offset, lang, note),
            )
            self._db.commit()

    def tuning(self) -> dict[str, tuple[float, str]]:
        return dict(self._tuning)

    # -- journal -----------------------------------------------------------

    def log_event(self, kind: str, *, app: str = "", keys: str = "", typed_lang: str = "", final_lang: str = "",
                  typed_text: str = "", final_text: str = "", margin: float | None = None,
                  detail: dict[str, Any] | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events(ts, kind, app, keys, typed_lang, final_lang, typed_text, final_text, margin, detail)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (time.time(), kind, app, keys, typed_lang, final_lang, typed_text, final_text, margin,
                 json.dumps(detail, ensure_ascii=False) if detail else ""),
            )
            self._db.commit()
            self._events_since_prune += 1
            if self._events_since_prune >= 200:
                self._events_since_prune = 0
                self._db.execute(
                    "DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?", (self.journal_size,)
                )
                self._db.commit()
            return int(cur.lastrowid)

    def events(self, kinds: Iterable[str] | None = None, limit: int = 200, after_id: int = 0) -> list[dict]:
        query = "SELECT id, ts, kind, app, keys, typed_lang, final_lang, typed_text, final_text, margin, detail " \
                "FROM events WHERE id > ?"
        params: list[Any] = [after_id]
        kinds = list(kinds or [])
        if kinds:
            query += f" AND kind IN ({','.join('?' * len(kinds))})"
            params += kinds
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        cols = ("id", "ts", "kind", "app", "keys", "typed_lang", "final_lang", "typed_text", "final_text",
                "margin", "detail")
        result = [dict(zip(cols, row)) for row in reversed(rows)]
        for item in result:
            item["detail"] = json.loads(item["detail"]) if item["detail"] else {}
        return result

    def count_events(self, kind: str, typed_text: str | None = None, final_text: str | None = None) -> int:
        query, params = "SELECT COUNT(*) FROM events WHERE kind=?", [kind]
        if typed_text is not None:
            query += " AND typed_text=?"
            params.append(typed_text)
        if final_text is not None:
            query += " AND final_text=?"
            params.append(final_text)
        with self._lock:
            return int(self._db.execute(query, params).fetchone()[0])

    def last_event_id(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT MAX(id) FROM events").fetchone()
        return int(row[0] or 0)

    # -- meta --------------------------------------------------------------

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, value))
            self._db.commit()

    def forget(self) -> None:
        """Erase everything learned."""
        with self._lock:
            for table in ("rules", "vocab", "app_stats", "tuning", "events", "meta"):
                self._db.execute(f"DELETE FROM {table}")
            self._db.commit()
            self._rules.clear()
            self._vocab.clear()
            self._app_stats.clear()
            self._tuning.clear()
            self._dirty_vocab.clear()
            self._dirty_stats.clear()

    def export(self) -> dict:
        self.flush()
        return {
            "rules": [asdict(r) for r in self.rules()],
            "vocab": self.vocab(limit=10_000),
            "app_stats": self.app_stats(),
            "tuning": self.tuning(),
            "style": self.get_meta("style_summary"),
        }
