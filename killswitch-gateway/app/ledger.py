"""Tamper-evident action ledger.

Every call an agent makes through the gateway (allowed, blocked or rejected) becomes one
row. Each row's hash covers the previous row's hash, so editing or deleting history breaks
the chain. Mutable state (rolled back or not) lives in `action_status`, outside the hash.
"""
import hashlib
import json
import time

from . import store

GENESIS = "0" * 64


def _dumps(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev, seq, ts, agent, tool, args, ops, result, flags, decision):
    payload = _dumps([seq, ts, agent, tool, args, ops, result, flags, decision])
    return hashlib.sha256((prev + payload).encode()).hexdigest()


def append(agent, tool, args, ops, result, flags, decision):
    c = store.conn()
    last = c.execute("SELECT seq, hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
    seq = (last["seq"] + 1) if last else 1
    prev = last["hash"] if last else GENESIS
    ts = round(time.time(), 3)
    a, o, r, f = _dumps(args), _dumps(ops), _dumps(result), _dumps(flags)
    h = _hash(prev, seq, ts, agent, tool, a, o, r, f, decision)
    c.execute(
        "INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (seq, ts, agent, tool, a, o, r, f, decision, prev, h),
    )
    if ops:
        c.execute("INSERT INTO action_status VALUES(?,?,?,NULL)", (seq, "active", ts))
    return seq


def verify_chain():
    """Recompute every hash. Returns {ok, length, broken_at}."""
    c = store.conn()
    prev, n = GENESIS, 0
    for r in c.execute("SELECT * FROM ledger ORDER BY seq"):
        n += 1
        exp = _hash(prev, r["seq"], r["ts"], r["agent"], r["tool"], r["args"], r["ops"],
                    r["result"], r["flags"], r["decision"])
        if r["prev_hash"] != prev or r["hash"] != exp:
            return {"ok": False, "length": n, "broken_at": r["seq"]}
        prev = r["hash"]
    return {"ok": True, "length": n, "broken_at": None}


def _row(r):
    d = dict(r)
    for k in ("args", "ops", "result", "flags"):
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def entries(limit=None, agent=None, since_seq=0, with_status=False):
    c = store.conn()
    q = ("SELECT l.*, s.status AS status FROM ledger l LEFT JOIN action_status s ON s.seq=l.seq "
         "WHERE l.seq>?")
    p = [since_seq]
    if agent:
        q += " AND l.agent=?"
        p.append(agent)
    q += " ORDER BY l.seq DESC LIMIT ?" if limit else " ORDER BY l.seq"
    if limit:
        p.append(limit)
    rows = [_row(r) for r in c.execute(q, p)]
    return rows


def write_entries():
    """All allowed entries that changed state, in execution order, with their status."""
    c = store.conn()
    q = ("SELECT l.*, s.status AS status FROM ledger l JOIN action_status s ON s.seq=l.seq "
         "WHERE l.decision='allowed' ORDER BY l.seq")
    return [_row(r) for r in c.execute(q)]


def event(kind, agent, detail):
    c = store.conn()
    c.execute("INSERT INTO events(ts,kind,agent,detail) VALUES(?,?,?,?)",
              (round(time.time(), 3), kind, agent, _dumps(detail)))
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def events(limit=50):
    c = store.conn()
    out = []
    for r in c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        d["detail"] = json.loads(d["detail"])
        out.append(d)
    return out
