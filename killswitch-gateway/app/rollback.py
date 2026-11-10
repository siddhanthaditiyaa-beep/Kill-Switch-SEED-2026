"""Selective rollback by replay.

To undo one agent's damage without touching anyone else's work we do NOT blindly restore
snapshots (that would also erase healthy agents' later changes to the same fields). Instead,
for every field the bad agent touched we rebuild its value from the field's original baseline
by replaying only the surviving (non-bad, still active) operations in order. Healthy writes
that landed after the damage are therefore preserved, even on the very same field.
"""
import time

from . import ledger, store


def _key(o):
    return (o["t"], o["pk"], o["f"])


def _replay(ops_by_key, key, skip_seqs):
    seq_ops = ops_by_key[key]
    val = seq_ops[0][1]["before"]            # baseline: value before the first-ever op
    for seq, o, active in seq_ops:
        if not active or seq in skip_seqs:
            continue
        val = o["after"] if o["k"] == "set" else round(val + o["delta"], 2)
    return val


def _index():
    """Index all state-changing ops by field and collect inserts."""
    ops_by_key, inserts = {}, {}
    entries = ledger.write_entries()
    for e in entries:
        active = e["status"] == "active"
        for o in e["ops"]:
            if o["k"] == "insert":
                inserts[(o["t"], o["pk"])] = {"seq": e["seq"], "active": active, "agent": e["agent"]}
            else:
                ops_by_key.setdefault(_key(o), []).append((e["seq"], o, active))
    return entries, ops_by_key, inserts


def plan(agent, since_seq=None):
    """Compute exactly what a rollback would do. Pure read; changes nothing."""
    entries, ops_by_key, inserts = _index()
    mine = [e for e in entries if e["agent"] == agent and e["status"] == "active"]
    if since_seq is None:
        # Damage window starts where the earliest high/critical anomaly BEGAN (by ledger
        # position), which can be earlier than the action that finally tripped the alarm.
        own = ledger.entries(agent=agent)
        crit = [f for e in own for f in (e["flags"] or []) if f.get("sev") in ("high", "critical")]
        seqs = [f["onset_seq"] for f in crit if f.get("onset_seq")]
        flagged = [e["seq"] for e in own if e["flags"] and e["decision"] == "allowed"
                   and e["status"] == "active"]
        starts = seqs + flagged
        since_seq = min(starts) if starts else 0
    bad = [e for e in mine if e["seq"] >= since_seq]
    bad_seqs = {e["seq"] for e in bad}

    restores, deletes, dependents = [], [], []
    touched = set()
    for e in bad:
        for o in e["ops"]:
            if o["k"] == "insert":
                deletes.append({"t": o["t"], "pk": o["pk"], "by_seq": e["seq"]})
            else:
                touched.add(_key(o))

    c = store.conn()
    for key in sorted(touched, key=str):
        t, pk, f = key
        restored = _replay(ops_by_key, key, bad_seqs)
        row = c.execute(f"SELECT {f} AS v FROM {t} WHERE " + ("dept" if t == "budget" else "id") + "=?",
                        (pk,)).fetchone()
        current = row["v"] if row else None
        healthy_after = [s for s, o, act in ops_by_key[key] if act and s not in bad_seqs
                         and s > min(b for b in bad_seqs)]
        if current is not None and (round(float(current), 2) != round(float(restored), 2)
                                    if isinstance(restored, (int, float)) else current != restored):
            restores.append({"t": t, "pk": pk, "f": f, "current": current, "restored": restored,
                             "healthy_changes_kept": healthy_after})

    # healthy actions that depended on rows the bad agent created
    for d in deletes:
        for e in entries:
            if e["status"] == "active" and e["seq"] not in bad_seqs and e["seq"] > d["by_seq"]:
                if any(o.get("t") == d["t"] and o.get("pk") == d["pk"] and o["k"] != "insert"
                       for o in e["ops"]):
                    dependents.append({"seq": e["seq"], "agent": e["agent"],
                                       "row": f"{d['t']}#{d['pk']}"})

    first = min(bad_seqs) if bad_seqs else None
    healthy_kept = [e["seq"] for e in entries if e["status"] == "active"
                    and e["agent"] != agent and first is not None and e["seq"] > first]
    return {
        "agent": agent, "since_seq": since_seq if bad else None,
        "bad_actions": sorted(bad_seqs), "restores": restores, "deletes": deletes,
        "dependents_needing_review": dependents,
        "healthy_actions_preserved": len(healthy_kept),
    }


def apply(agent, since_seq=None, incident_id=None):
    p = plan(agent, since_seq)
    if not p["bad_actions"]:
        return {**p, "applied": False, "audit": audit()}
    c = store.conn()
    t0 = time.time()
    c.execute("BEGIN")
    try:
        for r in p["restores"]:
            c.execute(f"UPDATE {r['t']} SET {r['f']}=? WHERE "
                      + ("dept" if r["t"] == "budget" else "id") + "=?", (r["restored"], r["pk"]))
        for d in p["deletes"]:
            c.execute(f"DELETE FROM {d['t']} WHERE id=?", (d["pk"],))
        for s in p["bad_actions"]:
            c.execute("UPDATE action_status SET status='rolled_back', changed_ts=?, incident_id=? "
                      "WHERE seq=?", (t0, incident_id, s))
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    ledger.append("gateway", "rollback", {"agent": agent, "since_seq": p["since_seq"]}, [],
                  {"reverted_actions": len(p["bad_actions"]), "fields_restored": len(p["restores"]),
                   "rows_removed": len(p["deletes"])}, [], "allowed")
    ledger.event("rollback", agent, {"actions": len(p["bad_actions"]), "restores": len(p["restores"]),
                                     "deletes": len(p["deletes"]),
                                     "healthy_preserved": p["healthy_actions_preserved"],
                                     "duration_ms": round((time.time() - t0) * 1000, 1)})
    return {**p, "applied": True, "audit": audit()}


def audit():
    """Independent check: recompute the expected value of every touched field from baseline +
    active ops, and the expected existence of every inserted row, then compare with the live DB.
    ok == True means every change was put right and nothing else drifted."""
    _, ops_by_key, inserts = _index()
    c = store.conn()
    drift, checked = [], 0
    for key in ops_by_key:
        t, pk, f = key
        expected = _replay(ops_by_key, key, set())
        row = c.execute(f"SELECT {f} AS v FROM {t} WHERE " + ("dept" if t == "budget" else "id") + "=?",
                        (pk,)).fetchone()
        live = row["v"] if row else None
        checked += 1
        same = (live is not None and round(float(live), 2) == round(float(expected), 2)) \
            if isinstance(expected, (int, float)) else live == expected
        if not same:
            drift.append({"field": f"{t}#{pk}.{f}", "expected": expected, "live": live})
    for (t, pk), info in inserts.items():
        exists = c.execute(f"SELECT 1 FROM {t} WHERE id=?", (pk,)).fetchone() is not None
        checked += 1
        if exists != info["active"]:
            drift.append({"field": f"{t}#{pk}", "expected": "present" if info["active"] else "absent",
                          "live": "present" if exists else "absent"})
    return {"ok": not drift, "checked": checked, "drift": drift}
