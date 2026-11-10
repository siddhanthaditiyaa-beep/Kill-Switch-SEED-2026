"""Gateway configuration: agent identities, scopes and monitor thresholds."""
import os

DB_PATH = os.environ.get(
    "KS_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "killswitch.db")
)

# --- Agent identities (the gateway authenticates every call with these keys) ---
# `tools` is the agent's declared scope. Anything outside it is a scope violation.
AGENTS = {
    "procurement": {
        "key": "key-procurement-7f3a",
        "tools": {"create_order", "spend", "read_customer", "list_customers"},
    },
    "support": {
        "key": "key-support-91bc",
        "tools": {"read_customer", "list_customers", "update_customer", "read_document"},
    },
    "analyst": {
        "key": "key-analyst-c2d8",
        "tools": {"read_customer", "list_customers", "read_document", "write_report"},
    },
}

# --- Policy switches ---
AUTO_QUARANTINE = os.environ.get("KS_AUTO_QUARANTINE", "1") == "1"
AUTO_ROLLBACK = os.environ.get("KS_AUTO_ROLLBACK", "0") == "1"

# --- Monitor thresholds (behavioural, not tied to any specific fault) ---
HARD_SPEND_CAP = 5000.0          # a single spend above this is denied outright
SPEND_VELOCITY_LIMIT = 4000.0    # total spend by one agent inside the window -> critical
SPEND_WINDOW_S = 60

MASS_ROWS_LIMIT = 8              # distinct rows modified inside the window -> critical
MASS_ROWS_WINDOW_S = 15

WRITE_RATE_LIMIT = 20            # writes inside the window -> critical
WRITE_RATE_WINDOW_S = 10

INVALID_LIMIT = 3                # rejected/invalid values inside the window -> critical
INVALID_WINDOW_S = 60

TAINT_ACTIONS = 10               # how many actions after a suspicious read stay "tainted"

VALID_STATUSES = {"active", "inactive", "vip", "churned"}
