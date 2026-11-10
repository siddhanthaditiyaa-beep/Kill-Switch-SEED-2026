"""The gateway: every agent tool call passes through handle_call()."""
import time

from . import config, ledger, monitor, rollback, store, tools

SEV_RANK = {"medium": 1, "high": 2, "critical": 3}


def agent_for_key(key):
    for name, a in config.AGENTS.items():
        if a["key"] == key:
            return name
    return None


def agent_status(agent):
    r = store.conn().execute("SELECT * FROM agent_state WHERE agent=?", (agent,)).fetchone()
    return dict(r) if r else {"agent": agent, "status": "active", "since": None, "reason": None}


def _set_state(agent, status, reason):
    store.conn().execute(
        "INSERT INTO agent_state VALUES(?,?,?,?) ON CONFLICT(agent) DO UPDATE SET "
        "status=excluded.status, since=excluded.since, reason=excluded.reason",
        (agent, status, round(time.time(), 3), reason))


def quarantine(agent, reason, onset_ts=None, trigger_seq=None, rule=None, manual=False):
    """Revoke one agent. Everyone else keeps working. Returns the incident id."""
    now = time.time()
    _set_state(agent, "quarantined", reason)
    ttc = round((now - onset_ts) * 1000) if onset_ts else None
    inc = ledger.event("quarantine", agent, {
        "reason": reason, "rule": rule, "trigger_seq": trigger_seq, "manual": manual,
        "onset_ts": onset_ts, "time_to_containment_ms": ttc})
    if config.AUTO_ROLLBACK and not manual:
        rollback.apply(agent, incident_id=inc)
    return inc


def release(agent):
    _set_state(agent, "active", "released by operator")
    ledger.event("release", agent, {})


def handle_call(key, tool, args):
    """Returns (http_status, body)."""
    args = args or {}
    with store.LOCK:
        agent = agent_for_key(key)
        if agent is None:
            return 401, {"error": "unknown agent key"}
        c = store.conn()

        # 1) Quarantined agents are cut off — but the attempt is still recorded as evidence.
        if agent_status(agent)["status"] == "quarantined":
            ledger.append(agent, tool, args, [], {"error": "agent quarantined"},
                          [monitor.flag("quarantined_attempt", "medium", "call from quarantined agent")],
                          "blocked")
            return 403, {"error": "agent quarantined"}

        # 2) Scope: an agent may only use the tools in its declared manifest.
        if tool not in config.AGENTS[agent]["tools"] or tool not in tools.TOOLS:
            fl = [monitor.flag("scope_violation", "critical",
                               f"{agent} called {tool}, outside its declared scope")]
            seq = ledger.append(agent, tool, args, [], {"error": "out of scope"}, fl, "blocked")
            _contain(agent, fl, seq)
            return 403, {"error": f"{tool} is outside the scope of agent {agent}"}

        # 3) Pre-checks that can deny before anything runs.
        intent = tools.intent(tool, args)
        pre_flags, deny = monitor.pre(agent, tool, args, intent)
        if deny:
            seq = ledger.append(agent, tool, args, [], {"error": "denied by policy"}, pre_flags, "blocked")
            _contain(agent, pre_flags, seq)
            return 403, {"error": "denied by policy", "flags": pre_flags}

        # 4) Execute.
        try:
            c.execute("BEGIN")
            result, ops = tools.TOOLS[tool](c, agent, args)
            c.execute("COMMIT")
        except tools.ToolError as e:
            c.execute("ROLLBACK")
            fl = monitor.on_rejected(agent, str(e))
            seq = ledger.append(agent, tool, args, [], {"error": str(e)}, fl, "rejected")
            _contain(agent, fl, seq)
            return 400, {"error": str(e)}
        except Exception:
            c.execute("ROLLBACK")
            raise

        # 5) Post-checks (behaviour over time), then record.
        flags = pre_flags + monitor.post(agent, tool, args, result, ops)
        seq = ledger.append(agent, tool, args, ops, result, flags, "allowed")
        if tool == "read_document" and agent in monitor.TAINT and monitor.TAINT[agent]["seq"] is None:
            monitor.TAINT[agent]["seq"] = seq
        _contain(agent, flags, seq)
        body = dict(result)
        if any(f["sev"] == "critical" for f in flags):
            body["_gateway"] = "this agent has been quarantined"
        return 200, body


def _contain(agent, flags, seq):
    crit = [f for f in flags if f["sev"] == "critical"]
    if crit and config.AUTO_QUARANTINE and agent_status(agent)["status"] != "quarantined":
        onset = min(f["onset_ts"] for f in crit)
        quarantine(agent, "; ".join(f["msg"] for f in crit), onset_ts=onset,
                   trigger_seq=seq, rule=crit[0]["rule"])
