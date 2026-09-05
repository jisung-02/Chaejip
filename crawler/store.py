"""SQLite storage with source-local identity and bounded retrieval."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(path)) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, key TEXT NOT NULL,
                title TEXT NOT NULL, body TEXT NOT NULL, url TEXT NOT NULL,
                author TEXT NOT NULL, collected_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(source_id, key))""")
            db.commit()
        path.chmod(0o600)

    def save(self, source_id: str, items: list[dict]) -> dict:
        counts = dict(new=0, updated=0, unchanged=0)
        now = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path)) as db, db:
            for item in items:
                values = tuple(item.get(k, "") for k in ("title", "body", "url", "author"))
                if any(not isinstance(v, str) or "\x00" in v for v in (source_id, item["key"], *values)):
                    raise ValueError("Stored fields must be text without NUL characters")
                old = db.execute(
                    "SELECT title, body, url, author FROM items WHERE source_id=? AND key=?",
                    (source_id, item["key"]),
                ).fetchone()
                if old == values:
                    counts["unchanged"] += 1
                elif old is None:
                    db.execute(
                        "INSERT INTO items (source_id,key,title,body,url,author,collected_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?)", (source_id, item["key"], *values, now, now),
                    )
                    counts["new"] += 1
                else:
                    db.execute(
                        "UPDATE items SET title=?,body=?,url=?,author=?,updated_at=? WHERE source_id=? AND key=?",
                        (*values, now, source_id, item["key"]),
                    )
                    counts["updated"] += 1
        return counts

    def search(self, query: str, source_id: str | None = None,
               since: str | None = None, limit: int = 20) -> list[dict]:
        if not 1 <= limit <= 100 or len(query) > 500:
            raise ValueError("limit must be 1..100; query must be at most 500 characters")
        where, args = [], []
        if query:
            pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' OR author LIKE ? ESCAPE '\\')")
            args.extend([pattern] * 3)
        if source_id is not None:
            where.append("source_id=?")
            args.append(source_id)
        if since:
            dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            where.append("updated_at>=?")
            args.append(dt.astimezone(timezone.utc).isoformat())
        sql = "SELECT id,source_id,substr(title,1,300) AS title,url,substr(author,1,200) AS author,substr(body,1,300) AS excerpt,collected_at,updated_at FROM items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql + " ORDER BY updated_at DESC,id DESC LIMIT ?", (*args, limit))]

    def get(self, item_id: int, offset: int = 0, max_chars: int = 8000) -> dict:
        if offset < 0 or not 1 <= max_chars <= 20000:
            raise ValueError("offset must be >=0; max_chars must be 1..20000")
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT id,source_id,title,url,author,collected_at,updated_at,length(body) AS total_chars,"
                "substr(body,?,?) AS body FROM items WHERE id=?", (offset + 1, max_chars, item_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown item: {item_id}")
        result = dict(row)
        result["offset"] = offset
        result["next_offset"] = offset + len(result["body"]) if offset + len(result["body"]) < result["total_chars"] else None
        return result
