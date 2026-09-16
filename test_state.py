from signal_state import SignalStateManager


manager = SignalStateManager()


print("=" * 70)
print("🎯 SIGNAL SNIPER — STATE MANAGER TEST")
print("=" * 70)


# ============================================================
# 1. GOLD BUY ACTIVATION
# ============================================================

trade = manager.create_trade(
    direction="BUY",
    message_id=6980,
)

print("\n🚨 GOLD BUY ACTIVATED")

manager.display()


# ============================================================
# 2. ENTRY
# ============================================================

manager.update_entry(
    entry_type="PRICE",
    entry_low=4397,
    entry_high=4397,
    message_id=6981,
)

print("\n📍 ENTRY RECEIVED")

manager.display()


# ============================================================
# 3. SL
# ============================================================

manager.update_stop_loss(
    stop_loss=4393.81,
    message_id=6982,
)

print("\n🛡️ CUTLOSS RECEIVED")

manager.display()


# ============================================================
# 4. TP
# ============================================================

manager.update_take_profits(
    [
        "OPEN",
        "1:2",
        "1:6",
    ],
    message_id=6982,
)

print("\n🎯 TP RECEIVED")

manager.display()


# ============================================================
# 5. EXECUTE
# ============================================================

manager.lock_entry(
    execution_type="PROVIDER_ENTRY"
)

print("\n🟢 ENTRY LOCKED")

manager.display()


# ============================================================
# 6. HOLD
# ============================================================

manager.hold()

print("\n✋ HOLD")

manager.display()


# ============================================================
# 7. PROFIT
# ============================================================

manager.update_profit(
    "RUNNING 40 PIPS"
)

print("\n📈 PROFIT UPDATE")

manager.display()


# ============================================================
# 8. PARTIAL CLOSE
# ============================================================

manager.close_partial()

print("\n🟡 CLOSE MOST")

manager.display()


print("\n")
print("=" * 70)
print("✅ STATE MANAGER TEST COMPLETE")
print("=" * 70)