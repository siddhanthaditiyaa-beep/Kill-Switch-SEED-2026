"""Behavioural monitor. Rules look at *what an agent is doing* (rates, spend velocity, scope,
rows touched, instruction-like content it just read), never at a specific known fault.

Each flag: {rule, sev: medium|high|critical, msg, onset_ts}. `onset_ts` is when the anomalous
behaviour started, so time-to-containment is measured from the real start, not from detection.
"""
import json
import re
import time

from . import config, store

TAINT = {}  # agent -> {"doc": id, "seq": ledger seq of the read, "ts": t, "left": actions remaining}

INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above|earlier) (instructions|rules|messages)",
    r"disregard (all |any |the )?(previous|prior|above|earlier|system)",
    r"(you must|you should|you are to) (now |immediately )?(transfer|send|forward|spend|update|change|email|pay)",
    r"system prompt", r"new instructions?:", r"override (the )?(policy|rules|limits?)",
    r"as (the )?(admin|administrator|ceo|system)", r"do not (tell|inform|notify|alert)",
    r"<!--", r"[​‌‍⁠]", r"\[\[?system\]?\]",
    r"(set|change|replace) (the )?(customer'?s? )?email (to|with)",
]
_INJ = re.compile("|".join(INJECTION_PATTERNS), re.I)


def flag(rule, sev, msg, onset_ts=None, onset_seq=None):
    """onset_seq (ledger position where the anomaly began) drives the rollback damage window;
    onset_ts drives time-to-containment."""
    return {"rule": rule, "sev": sev, "msg": msg, "onset_ts": onset_ts or time.time(),
            "onset_seq": onset_seq}


def scan_text(text):
    """Return suspicious snippets found in text read from an untrusted source."""
    return [m.group(0)[:60] for m in _INJ.finditer(text or "")][:5]


def _recent(agent, window):
    c = store.conn()
    cutoff = time.time() - window
    st = c.execute("SELECT status, since FROM agent_state WHERE agent=?", (agent,)).fetchone()
    if st and st["status"] == "active" and st["since"]:
        cutoff = max(cutoff, st["since"])        # a released agent starts with a clean slate
    rows = c.execute(
        "SELECT l.seq, l.ts, l.ops, l.decision, l.flags FROM ledger l "
        "LEFT JOIN action_status s ON s.seq=l.seq "
        "WHERE l.agent=? AND l.ts>? AND COALESCE(s.status,'active')!='rolled_back' ORDER BY l.seq",
        (agent, cutoff)).fetchall()
    return [(r["ts"], json.loads(r["ops"]), r["decision"], json.loads(r["flags"] or "[]"), r["seq"])
            for r in rows]


def _burst_start(points, max_gap=2.0):
    """points: [(ts, seq)] ascending. Start of the trailing run with no gap > max_gap."""
    i = len(points) - 1
    while i > 0 and points[i][0] - points[i - 1][0] <= max_gap:
        i -= 1
    return points[i]


def pre(agent, tool, args, intent):
    """Checks that can stop a call BEFORE it runs. Returns (flags, deny)."""
    flags, deny = [], False
    if intent["spend"] > config.HARD_SPEND_CAP:
        flags.append(flag("hard_spend_cap", "critical",
                          f"single spend of {intent['spend']:.0f} exceeds hard cap {config.HARD_SPEND_CAP:.0f}"))
        deny = True
    t = TAINT.get(agent)
    if t and t["left"] > 0 and intent["high_risk"]:
        flags.append(flag("tainted_high_risk_action", "critical",
                          f"high-risk action {tool} shortly after reading suspicious document "
                          f"#{t['doc']} (ledger #{t['seq']}) — possible prompt injection",
                          t["ts"], t["seq"]))
        deny = True
    return flags, deny


def post(agent, tool, args, result, ops):
    """Checks run right after an allowed call executed. Returns flags."""
    flags = []
    now = time.time()

    # 1) Untrusted content read -> taint the agent's next few actions
    if tool == "read_document":
        hits = scan_text(result.get("body", "") + " " + result.get("title", ""))
        if hits:
            TAINT[agent] = {"doc": args.get("id"), "seq": None, "ts": now,
                            "left": config.TAINT_ACTIONS}
            flags.append(flag("prompt_injection_suspected", "medium",
                              f"document #{args.get('id')} contains instruction-like text: {hits}"))
    elif agent in TAINT:
        TAINT[agent]["left"] -= 1
        if TAINT[agent]["left"] <= 0:
            TAINT.pop(agent, None)

    recent = _recent(agent, max(config.SPEND_WINDOW_S, config.MASS_ROWS_WINDOW_S,
                                config.WRITE_RATE_WINDOW_S, config.INVALID_WINDOW_S))
    # include the call that just ran (it is not in the ledger yet)
    recent.append((now, ops, "allowed", [], None))

    # 2) Spend velocity
    spend_pts = [(ts, -o["delta"], sq) for ts, ops_, dec, _, sq in recent if dec == "allowed"
                 for o in ops_ if o["k"] == "incr" and o["delta"] < 0
                 and ts > now - config.SPEND_WINDOW_S]
    total = sum(v for _, v, _ in spend_pts)
    if total > config.SPEND_VELOCITY_LIMIT:
        # Onset = start of the burst, not of the window: walk back from the newest spend over
        # the contiguous run of events that are outliers vs this agent's typical spend size
        # (>= 3x median). Falls back to the shortest run that alone breaches the limit.
        amts = sorted(v for _, v, _ in spend_pts)
        median = amts[len(amts) // 2]
        i = len(spend_pts)
        while i > 0 and spend_pts[i - 1][1] >= 3 * median:
            i -= 1
        if i == len(spend_pts):                      # no clear outliers (uniformly large spends)
            acc = 0.0
            while i > 0 and acc <= config.SPEND_VELOCITY_LIMIT:
                i -= 1
                acc += spend_pts[i][1]
        flags.append(flag("spend_velocity", "critical",
                          f"{total:.0f} spent in {config.SPEND_WINDOW_S}s "
                          f"(limit {config.SPEND_VELOCITY_LIMIT:.0f})", spend_pts[i][0], spend_pts[i][2]))

    # 3) Mass modification of distinct existing rows
    rows = {}
    for ts, ops_, dec, _, sq in recent:
        if dec != "allowed" or ts <= now - config.MASS_ROWS_WINDOW_S:
            continue
        for o in ops_:
            if o["k"] == "set":
                rows.setdefault((o["t"], o["pk"]), (ts, sq))
    if len(rows) >= config.MASS_ROWS_LIMIT:
        flags.append(flag("mass_modification", "critical",
                          f"{len(rows)} distinct records modified in {config.MASS_ROWS_WINDOW_S}s",
                          *_burst_start(sorted(rows.values()))))

    # 4) Raw write rate
    wts = [(ts, sq) for ts, ops_, dec, _, sq in recent
           if dec == "allowed" and ops_ and ts > now - config.WRITE_RATE_WINDOW_S]
    if len(wts) > config.WRITE_RATE_LIMIT:
        flags.append(flag("write_rate", "critical",
                          f"{len(wts)} writes in {config.WRITE_RATE_WINDOW_S}s", *wts[0]))
    return flags


def on_rejected(agent, reason):
    """A call failed validation. Repeated invalid calls are themselves suspicious."""
    flags = [flag("invalid_call", "medium", reason)]
    bad = [ts for ts, _, dec, _, _ in _recent(agent, config.INVALID_WINDOW_S) if dec == "rejected"]
    if len(bad) + 1 >= config.INVALID_LIMIT:
        flags.append(flag("repeated_invalid", "critical",
                          f"{len(bad) + 1} rejected calls in {config.INVALID_WINDOW_S}s",
                          bad[0] if bad else time.time()))
    return flags
