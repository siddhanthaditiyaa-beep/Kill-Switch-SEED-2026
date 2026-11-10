# KillSwitch Gateway
A gateway that sits **in front of** a network of AI agents. Every tool call carries the agent's
identity, is checked, and is written to a tamper-evident ledger. One agent can be quarantined
while the rest keep working, and its damage — and only its damage — can be rolled back.

## Run it (project root = the `killswitch` folder)
Install once (any terminal):
    pip install -r requirements.txt

Open **3 terminals**, each `cd`'d into the `killswitch` folder:

    Terminal 1 (gateway + dashboard):   uvicorn app.main:app --port 8000
    Terminal 2 (healthy agents):        python -m sim.agents
    Terminal 3 (red team):              python -m sim.redteam corrupt     # or overspend | inject | all

Dashboard: http://127.0.0.1:8000

Windows PowerShell note: if `uvicorn` is not found use `python -m uvicorn app.main:app --port 8000`.
Run the tests:  `python -m pytest -q`   (15 tests)

## What it does
| Layer | File | Behaviour |
|---|---|---|
| Identity | `config.py`, `gateway.py` | Each agent has a key + a declared tool scope; out-of-scope calls are blocked and quarantine the agent |
| Ledger | `ledger.py` | Hash-chained log of every call (allowed, blocked, rejected) with field-level before/after |
| Monitor | `monitor.py` | Spend velocity, hard spend cap, mass modification, write rate, repeated invalid calls, prompt-injection taint |
| Quarantine | `gateway.py` | Revokes one agent at the gateway; its later attempts are logged as evidence |
| Rollback | `rollback.py` | Rebuilds each touched field by replaying only surviving operations from its baseline, so healthy agents' later writes — even to the same field — are preserved |
| Audit | `rollback.py:audit` | Independently recomputes every touched field from the ledger and compares with the live DB (zero drift = everything put right) |

## Demo script (≈ 3 minutes)
1. Show the dashboard with the healthy agents running (no flags, ledger intact).
2. `python -m sim.redteam corrupt` → support is cut off in ~1 s; bad emails visible in the Business panel.
3. Click **Preview rollback** on support → show "N actions reverted, M healthy actions preserved".
4. **Execute rollback** → audit passes, emails back to normal, procurement/analyst never stopped.
5. `python -m sim.redteam inject` → the poisoned ticket is flagged and the obeyed instruction is denied before anything changes.
6. Tamper proof: stop the server, run
   `sqlite3 killswitch.db "UPDATE ledger SET args='{}' WHERE seq=1"`, restart → header turns red "LEDGER TAMPERED at #1".

## Honest limitations (say these before the judges find them)
- The agents here are scripted stand-ins; the gateway only sees tool calls, so a real LLM agent framework can be pointed at `/gateway/call` unchanged — but that adapter is not written yet (an MCP front-end is the next step).
- Detection is rule/behaviour based with fixed thresholds. A slow-drip fault below the thresholds will not trip it; a per-agent learned baseline is the next improvement.
- Rollback covers ledger-recorded tool actions only (database state), not side effects outside the gateway (emails sent, money moved at a bank).
- The damage window is chosen from the anomaly's detected onset; the preview lets the operator inspect it, and `?since_seq=` overrides it.
