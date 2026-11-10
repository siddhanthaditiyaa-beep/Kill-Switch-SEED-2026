"""Shared SQLite state: the business data the agents work on + the gateway's own tables."""
import sqlite3
import threading

from . import config

LOCK = threading.RLock()  # the gateway serialises every call; keeps ledger order == execution order
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers(
    id INTEGER PRIMARY KEY, name TEXT, email TEXT, status TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER, item TEXT, amount REAL, created_by TEXT);
CREATE TABLE IF NOT EXISTS budget(dept TEXT PRIMARY KEY, balance REAL);
CREATE TABLE IF NOT EXISTS reports(
    id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, title TEXT, body TEXT);

CREATE TABLE IF NOT EXISTS ledger(
    seq INTEGER PRIMARY KEY, ts REAL, agent TEXT, tool TEXT,
    args TEXT, ops TEXT, result TEXT, flags TEXT, decision TEXT,
    prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS action_status(
    seq INTEGER PRIMARY KEY, status TEXT, changed_ts REAL, incident_id INTEGER);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, agent TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS agent_state(
    agent TEXT PRIMARY KEY, status TEXT, since REAL, reason TEXT);
"""

NAMES = [
    "Aarav Mehta", "Diya Nair", "Kabir Rao", "Isha Kulkarni", "Vihaan Shah", "Anaya Iyer",
    "Rohan Desai", "Meera Pillai", "Arjun Menon", "Saanvi Joshi", "Kavya Reddy", "Neel Patil",
    "Tanvi Bhat", "Aditya Sen", "Riya Kapoor", "Yash Gupta", "Pooja Verma", "Dev Malhotra",
    "Sneha Naik", "Karan Singh", "Ananya Das", "Rahul Jain", "Nisha Thomas", "Sahil Khan",
    "Ira Chopra", "Manav Bose", "Tara Sethi", "Om Prakash", "Jiya Shetty", "Varun Ghosh",
    "Lata Pawar", "Zoya Ali", "Harsh Vora", "Mira George", "Ishaan Dutta", "Pari Saxena",
    "Reyansh Rane", "Siya Kamat", "Atharv More", "Navya Sinha",
]

DOCS = [
    (1, "Refund policy", "Refunds are issued within 7 days of an approved request."),
    (2, "Shipping FAQ", "Standard shipping takes 3-5 working days across India."),
    (3, "Escalation SOP", "Escalate unresolved tickets to a human after two failed attempts."),
]


def conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        if _conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
            seed(_conn)
    return _conn


def seed(c):
    for i, n in enumerate(NAMES, start=1):
        email = n.lower().replace(" ", ".") + "@example.com"
        c.execute("INSERT INTO customers VALUES(?,?,?,?,?)", (i, n, email, "active", ""))
    for dept, bal in (("procurement", 100000.0), ("support", 20000.0), ("ops", 50000.0)):
        c.execute("INSERT INTO budget VALUES(?,?)", (dept, bal))
    for d in DOCS:
        c.execute("INSERT INTO documents VALUES(?,?,?)", d)


def reset():
    """Wipe everything (business data + ledger) and reseed. Used for demo resets and tests."""
    from . import monitor
    with LOCK:
        c = conn()
        for t in ("customers", "orders", "budget", "reports", "documents", "ledger",
                  "action_status", "events", "agent_state"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM sqlite_sequence")
        seed(c)
        monitor.TAINT.clear()


def close():
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
