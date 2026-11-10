"""The business tools agents can call. Every write returns *ops* — a field-level record of
exactly what changed (with the value before) — which is what makes selective rollback possible.

Op kinds:
  set    {t, pk, f, k:'set',  before, after}   overwrite a field
  incr   {t, pk, f, k:'incr', before, delta}   add a delta to a numeric field (commutes)
  insert {t, pk, k:'insert',  row}             a new row
"""
import re

from . import config


class ToolError(Exception):
    pass


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CUSTOMER_FIELDS = {"name", "email", "status", "notes"}


def _num(v, name):
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ToolError(f"{name} must be a number")
    if v <= 0 or v != v:
        raise ToolError(f"{name} must be positive")
    return round(v, 2)


def list_customers(c, agent, a):
    rows = c.execute("SELECT id, name, status FROM customers ORDER BY id").fetchall()
    return {"customers": [dict(r) for r in rows]}, []


def read_customer(c, agent, a):
    r = c.execute("SELECT * FROM customers WHERE id=?", (a.get("id"),)).fetchone()
    if not r:
        raise ToolError("customer not found")
    return dict(r), []


def update_customer(c, agent, a):
    cid, field, value = a.get("id"), a.get("field"), a.get("value")
    if field not in CUSTOMER_FIELDS:
        raise ToolError(f"field must be one of {sorted(CUSTOMER_FIELDS)}")
    value = "" if value is None else str(value)
    if field == "email" and not EMAIL_RE.match(value):
        raise ToolError("invalid email")
    if field == "status" and value not in config.VALID_STATUSES:
        raise ToolError("invalid status")
    if field == "notes" and len(value) > 300:
        raise ToolError("notes too long")
    if field == "name" and not (1 <= len(value) <= 80):
        raise ToolError("invalid name")
    r = c.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not r:
        raise ToolError("customer not found")
    c.execute(f"UPDATE customers SET {field}=? WHERE id=?", (value, cid))
    op = {"t": "customers", "pk": cid, "f": field, "k": "set", "before": r[field], "after": value}
    return {"updated": cid, "field": field}, [op]


def _take_budget(c, dept, amount):
    b = c.execute("SELECT balance FROM budget WHERE dept=?", (dept,)).fetchone()
    if not b:
        raise ToolError("unknown department")
    if b["balance"] < amount:
        raise ToolError("insufficient budget")
    c.execute("UPDATE budget SET balance=ROUND(balance-?,2) WHERE dept=?", (amount, dept))
    return {"t": "budget", "pk": dept, "f": "balance", "k": "incr",
            "before": b["balance"], "delta": -amount}


def spend(c, agent, a):
    amount = _num(a.get("amount"), "amount")
    dept = a.get("dept", agent)
    op = _take_budget(c, dept, amount)
    return {"spent": amount, "dept": dept, "memo": a.get("memo", "")}, [op]


def create_order(c, agent, a):
    amount = _num(a.get("amount"), "amount")
    cust = c.execute("SELECT id FROM customers WHERE id=?", (a.get("customer_id"),)).fetchone()
    if not cust:
        raise ToolError("customer not found")
    item = str(a.get("item", ""))[:80]
    bop = _take_budget(c, "procurement", amount)
    cur = c.execute("INSERT INTO orders(customer_id,item,amount,created_by) VALUES(?,?,?,?)",
                    (cust["id"], item, amount, agent))
    oid = cur.lastrowid
    row = {"id": oid, "customer_id": cust["id"], "item": item, "amount": amount, "created_by": agent}
    return {"order_id": oid, "amount": amount}, [
        {"t": "orders", "pk": oid, "k": "insert", "row": row}, bop]


def read_document(c, agent, a):
    r = c.execute("SELECT * FROM documents WHERE id=?", (a.get("id"),)).fetchone()
    if not r:
        raise ToolError("document not found")
    return dict(r), []


def write_report(c, agent, a):
    text = str(a.get("text", ""))[:500]
    cur = c.execute("INSERT INTO reports(agent,text) VALUES(?,?)", (agent, text))
    rid = cur.lastrowid
    return {"report_id": rid}, [
        {"t": "reports", "pk": rid, "k": "insert", "row": {"id": rid, "agent": agent, "text": text}}]


TOOLS = {
    "list_customers": list_customers, "read_customer": read_customer,
    "update_customer": update_customer, "spend": spend, "create_order": create_order,
    "read_document": read_document, "write_report": write_report,
}
WRITE_TOOLS = {"update_customer", "spend", "create_order", "write_report"}


def intent(tool, args):
    """What the call is about to do, computed BEFORE running it (used by pre-checks)."""
    amount = 0.0
    if tool in ("spend", "create_order"):
        try:
            amount = float(args.get("amount", 0))
        except (TypeError, ValueError):
            amount = 0.0
    high_risk = tool in ("spend", "create_order") or (
        tool == "update_customer" and args.get("field") == "email")
    return {"write": tool in WRITE_TOOLS, "spend": amount, "high_risk": high_risk}
