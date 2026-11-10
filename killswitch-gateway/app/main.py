"""HTTP API + dashboard.   Run:  uvicorn app.main:app --port 8000"""
import json
import os
import time

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import config, gateway, ledger, monitor, rollback, store

app = FastAPI(title="KillSwitch Gateway")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_chain_cache = {"seq": -1, "res": None}


# ----------------------------- the gateway itself -----------------------------
@app.post("/gateway/call")
async def gateway_call(request: Request, x_agent_key: str = Header(default="")):
    """Agents send {"tool": "...", "args": {...}} with their identity key."""
    body = await request.json()
    status, out = gateway.handle_call(x_agent_key, body.get("tool", ""), body.get("args") or {})
    return JSONResponse(out, status_code=status)


# ----------------------------- operator / dashboard API -----------------------------
@app.get("/api/state")
def state():
    with store.LOCK:
        c = store.conn()
        entries = ledger.entries(limit=80)
        agents = {}
        for name in config.AGENTS:
            st = gateway.agent_status(name)
            rows = c.execute(
                "SELECT l.decision, l.ops, l.flags, s.status AS st, l.ts FROM ledger l "
                "LEFT JOIN action_status s ON s.seq=l.seq WHERE l.agent=?", (name,)).fetchall()
            agents[name] = {
                "status": st["status"], "reason": st["reason"], "since": st["since"],
                "total": len(rows),
                "writes": sum(1 for r in rows if r["ops"] != "[]" and r["decision"] == "allowed"),
                "flagged": sum(1 for r in rows if r["flags"] != "[]"),
                "blocked": sum(1 for r in rows if r["decision"] in ("blocked", "rejected")),
                "rolled_back": sum(1 for r in rows if r["st"] == "rolled_back"),
                "last_ts": max((r["ts"] for r in rows), default=None),
                "tainted": name in monitor.TAINT,
                "scope": sorted(config.AGENTS[name]["tools"]),
            }
        last = ledger.entries(limit=1)
        seq = last[0]["seq"] if last else 0
        if _chain_cache["seq"] != seq:
            _chain_cache.update(seq=seq, res=ledger.verify_chain())
        return {
            "now": time.time(), "agents": agents, "ledger": entries,
            "events": ledger.events(30), "chain": _chain_cache["res"],
            "budget": {r["dept"]: r["balance"] for r in c.execute("SELECT * FROM budget")},
            "counts": {
                "customers": c.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
                "orders": c.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
                "reports": c.execute("SELECT COUNT(*) FROM reports").fetchone()[0],
                "non_example_emails": c.execute(
                    "SELECT COUNT(*) FROM customers WHERE email NOT LIKE '%@example.com'").fetchone()[0],
            },
            "policy": {"auto_quarantine": config.AUTO_QUARANTINE, "auto_rollback": config.AUTO_ROLLBACK},
        }


@app.post("/api/agents/{agent}/quarantine")
def api_quarantine(agent: str):
    _need(agent)
    with store.LOCK:
        gateway.quarantine(agent, "manual kill switch", onset_ts=time.time(), manual=True)
    return {"ok": True}


@app.post("/api/agents/{agent}/release")
def api_release(agent: str):
    _need(agent)
    with store.LOCK:
        gateway.release(agent)
        monitor.TAINT.pop(agent, None)
    return {"ok": True}


@app.get("/api/agents/{agent}/rollback/preview")
def api_preview(agent: str, since_seq: int | None = None):
    _need(agent)
    with store.LOCK:
        return rollback.plan(agent, since_seq)


@app.post("/api/agents/{agent}/rollback")
def api_rollback(agent: str, since_seq: int | None = None):
    _need(agent)
    with store.LOCK:
        return rollback.apply(agent, since_seq)


@app.get("/api/audit")
def api_audit():
    with store.LOCK:
        return rollback.audit()


@app.get("/api/verify-chain")
def api_verify():
    with store.LOCK:
        return ledger.verify_chain()


@app.post("/api/config")
async def api_config(request: Request):
    body = await request.json()
    if "auto_quarantine" in body:
        config.AUTO_QUARANTINE = bool(body["auto_quarantine"])
    if "auto_rollback" in body:
        config.AUTO_ROLLBACK = bool(body["auto_rollback"])
    return {"auto_quarantine": config.AUTO_QUARANTINE, "auto_rollback": config.AUTO_ROLLBACK}


# ----------------------------- demo / red-team helpers -----------------------------
@app.post("/admin/reset")
def admin_reset():
    with store.LOCK:
        store.reset()
        _chain_cache["seq"] = -1
    return {"ok": True}


@app.post("/admin/plant_document")
async def admin_plant(request: Request):
    """Red team: plant an untrusted document (e.g. a support ticket with a hidden instruction)."""
    b = await request.json()
    with store.LOCK:
        store.conn().execute("INSERT OR REPLACE INTO documents VALUES(?,?,?)",
                             (b["id"], b.get("title", "Ticket"), b["body"]))
    return {"ok": True}


def _need(agent):
    if agent not in config.AGENTS:
        raise HTTPException(404, "unknown agent")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))
