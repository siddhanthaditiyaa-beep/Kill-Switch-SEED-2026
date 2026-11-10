"""Three healthy agents doing realistic work through the gateway.  Ctrl-C to stop.

    python -m sim.agents                 (terminal 2, project root)

These are scripted stand-ins for real LLM agents: the gateway only sees tool calls + an
identity key, so a real agent framework can be pointed at /gateway/call the same way.
"""
import os
import random
import threading
import time

import requests

URL = os.environ.get("KS_URL", "http://127.0.0.1:8000") + "/gateway/call"
KEYS = {"procurement": "key-procurement-7f3a", "support": "key-support-91bc",
        "analyst": "key-analyst-c2d8"}
ITEMS = ["laptop stand", "usb hub", "ergonomic chair", "whiteboard", "monitor arm", "headset",
         "desk lamp", "notebook pack"]
NOTES = ["Followed up by email", "Customer asked about refund", "Resolved on call",
         "Waiting for customer reply", "Sent shipping update"]


def call(agent, tool, **args):
    try:
        r = requests.post(URL, json={"tool": tool, "args": args},
                          headers={"X-Agent-Key": KEYS[agent]}, timeout=5)
        return r.status_code, r.json()
    except Exception as e:  # server not up yet
        return 0, {"error": str(e)}


def loop(agent, step, lo, hi):
    while True:
        code, body = step()
        if code == 403:
            print(f"[{agent}] blocked: {body.get('error')} — standing by")
            time.sleep(6)
        time.sleep(random.uniform(lo, hi))


def procurement():
    cid = random.randint(1, 40)
    if random.random() < 0.15:
        return call("procurement", "list_customers")
    code, body = call("procurement", "create_order", customer_id=cid,
                      item=random.choice(ITEMS), amount=random.randint(40, 300))
    print(f"[procurement] order for customer {cid}: {code}")
    return code, body


def support():
    cid = random.randint(1, 40)
    r = random.random()
    if r < 0.2:
        return call("support", "read_document", id=random.randint(1, 3))
    call("support", "read_customer", id=cid)
    if r < 0.8:
        code, body = call("support", "update_customer", id=cid, field="notes",
                          value=random.choice(NOTES))
    else:
        code, body = call("support", "update_customer", id=cid, field="status",
                          value=random.choice(["active", "vip", "inactive"]))
    print(f"[support] updated customer {cid}: {code}")
    return code, body


def analyst():
    if random.random() < 0.3:
        code, body = call("analyst", "write_report", text="Daily summary: order volume steady.")
        print(f"[analyst] report: {code}")
        return code, body
    return call("analyst", "read_customer", id=random.randint(1, 40))


if __name__ == "__main__":
    print("Healthy agents running against", URL)
    for name, fn, lo, hi in (("procurement", procurement, 3, 6), ("support", support, 3, 6),
                             ("analyst", analyst, 4, 8)):
        threading.Thread(target=loop, args=(name, fn, lo, hi), daemon=True).start()
    while True:
        time.sleep(1)
