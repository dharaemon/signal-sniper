from signal_state import SignalStateManager
from entry_engine import EntryDecisionEngine


def separator(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


engine = EntryDecisionEngine()


# ============================================================
# TEST 1 — EXACT PROVIDER ENTRY
# ============================================================

separator("TEST 1 — EXACT PROVIDER ENTRY")

state = SignalStateManager()

state.create_trade("BUY", 1001)

state.update_entry(
    entry_type="PRICE",
    entry_low=4397,
    entry_high=4397,
)

decision = engine.process_entry_timeout(state)

print("Action        :", decision.action)
print("Entry Type    :", decision.entry_type)
print("Signal Low    :", decision.signal_low)
print("Signal High   :", decision.signal_high)
print("Execution     :", decision.execution_price)
print("Reason        :", decision.reason)

state.display()


# ============================================================
# TEST 2 — PROVIDER ZONE
# ============================================================

separator("TEST 2 — PROVIDER ZONE")

state = SignalStateManager()

state.create_trade("BUY", 1002)

state.update_entry(
    entry_type="ZONE",
    entry_low=4388,
    entry_high=4391,
)

decision = engine.process_entry_timeout(state)

print("Action        :", decision.action)
print("Entry Type    :", decision.entry_type)
print("Signal Low    :", decision.signal_low)
print("Signal High   :", decision.signal_high)
print("Execution     :", decision.execution_price)
print("Reason        :", decision.reason)

state.display()


# ============================================================
# TEST 3 — NO PROVIDER ENTRY
# ============================================================

separator("TEST 3 — NO PROVIDER ENTRY")

state = SignalStateManager()

state.create_trade("SELL", 1003)

decision = engine.process_entry_timeout(state)

print("Action        :", decision.action)
print("Entry Type    :", decision.entry_type)
print("Signal Low    :", decision.signal_low)
print("Signal High   :", decision.signal_high)
print("Execution     :", decision.execution_price)
print("Reason        :", decision.reason)

state.display()


# ============================================================
# TEST 4 — ZONE MUST NOT BECOME EXECUTION PRICE
# ============================================================

separator("TEST 4 — ZONE SAFETY CHECK")

state = SignalStateManager()

state.create_trade("BUY", 1004)

state.update_entry(
    entry_type="ZONE",
    entry_low=4388,
    entry_high=4391,
)

decision = engine.process_entry_timeout(state)

trade = state.get_trade()

assert decision.action == "WAIT_FOR_ZONE"
assert decision.execution_price is None
assert trade.actual_entry_price is None
assert trade.execution_type == "PROVIDER_ZONE"
assert trade.execution_status == "WAITING_FOR_PRICE"
assert trade.status == "WAITING_FOR_ZONE"
assert trade.entry_locked is False

print("✅ Zone was NOT converted into an execution price.")
print("✅ Actual entry remains None.")
print("✅ State remains WAITING_FOR_ZONE.")


# ============================================================
# TEST 5 — EXACT ENTRY MUST LOCK
# ============================================================

separator("TEST 5 — EXACT ENTRY SAFETY CHECK")

state = SignalStateManager()

state.create_trade("BUY", 1005)

state.update_entry(
    entry_type="PRICE",
    entry_low=4397,
    entry_high=4397,
)

decision = engine.process_entry_timeout(state)

trade = state.get_trade()

assert decision.action == "EXECUTE_PROVIDER_PRICE"
assert decision.execution_price == 4397
assert trade.actual_entry_price is None
assert trade.execution_type == "PROVIDER_ENTRY"
assert trade.execution_status == "PENDING_SUBMISSION"
assert trade.entry_locked is False
assert trade.status == "SUBMITTING_ENTRY"

print("✅ Exact provider entry is submitted, not assumed filled.")
print("✅ Actual Entry remains unset until MT5 confirms fill.")
print("✅ Entry remains unlocked until broker confirmation.")


# ============================================================
# COMPLETE
# ============================================================

separator("✅ ENTRY ENGINE TEST COMPLETE")
