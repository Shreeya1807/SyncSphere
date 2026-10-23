"""Small document interface with atomic updates on SQLite, PostgreSQL or MongoDB."""

import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(
        self,
        path="data/syncsphere.sqlite",
        mongo_url=None,
        database="syncsphere",
        database_url=None,
    ):
        self.mongo = None
        self.database_url = database_url
        if database_url and mongo_url:
            raise ValueError("Configure DATABASE_URL or MONGO_URL, not both")
        if database_url:
            import psycopg
            import certifi

            self.pg = psycopg
            self.sslrootcert = certifi.where()
            with self.pg.connect(
                database_url, connect_timeout=10, sslrootcert=self.sslrootcert
            ) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS syncsphere_documents (id TEXT PRIMARY KEY, body JSONB NOT NULL)"
                )
        elif mongo_url:
            from pymongo import MongoClient

            self.client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
            self.client.admin.command("ping")
            self.mongo = self.client[database].documents
        else:
            self.path = str(path)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
                )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, key):
        if self.database_url:
            with self.pg.connect(
                self.database_url, connect_timeout=10, sslrootcert=self.sslrootcert
            ) as db:
                row = db.execute(
                    "SELECT body FROM syncsphere_documents WHERE id=%s", (key,)
                ).fetchone()
                return row[0] if row else None
        if self.mongo is not None:
            doc = self.mongo.find_one({"_id": key})
            return doc["body"] if doc else None
        with self.connect() as db:
            row = db.execute("SELECT body FROM documents WHERE id=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def mutate(self, key, change):
        if self.database_url:
            from psycopg.types.json import Jsonb

            with self.pg.connect(
                self.database_url, connect_timeout=10, sslrootcert=self.sslrootcert
            ) as db:
                db.execute("SET LOCAL lock_timeout = '10s'")
                # Transaction locks also serialize first writes, before a row exists.
                db.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,)
                )
                row = db.execute(
                    "SELECT body FROM syncsphere_documents WHERE id=%s", (key,)
                ).fetchone()
                body = change(row[0] if row else None)
                db.execute(
                    "INSERT INTO syncsphere_documents VALUES (%s, %s) ON CONFLICT(id) DO UPDATE SET body=EXCLUDED.body",
                    (key, Jsonb(body)),
                )
                return body
        if self.mongo is not None:
            from pymongo.errors import DuplicateKeyError

            for _ in range(20):
                old = self.mongo.find_one({"_id": key})
                body = change(copy.deepcopy(old["body"]) if old else None)
                if old:
                    result = self.mongo.update_one(
                        {"_id": key, "version": old["version"]},
                        {"$set": {"body": body}, "$inc": {"version": 1}},
                    )
                    if result.modified_count:
                        return body
                else:
                    try:
                        self.mongo.insert_one({"_id": key, "version": 1, "body": body})
                        return body
                    except DuplicateKeyError:
                        pass
            raise RuntimeError("Concurrent update limit reached; retry the request")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM documents WHERE id=?", (key,)).fetchone()
            body = change(json.loads(row[0]) if row else None)
            db.execute(
                "INSERT INTO documents VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (key, json.dumps(body)),
            )
            return body

    def put(self, key, body):
        return self.mutate(key, lambda _: body)

    def healthy(self):
        if self.database_url:
            with self.pg.connect(
                self.database_url, connect_timeout=10, sslrootcert=self.sslrootcert
            ) as db:
                db.execute("SELECT 1")
        elif self.mongo is not None:
            self.client.admin.command("ping")
        else:
            with self.connect() as db:
                db.execute("SELECT 1")
        return True
