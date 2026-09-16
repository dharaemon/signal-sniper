import tempfile
import unittest
from pathlib import Path

import execution_store
from entry_engine import EntryDecisionEngine
from execution_contract import ExecutionAction, ExecutionResult, ExecutionStatus
from signal_state import SignalStateManager


class ExecutionFoundationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = execution_store.DB_PATH
        execution_store.DB_PATH = Path(self.tempdir.name) / "execution.db"

    def tearDown(self):
        execution_store.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def test_exact_entry_is_not_marked_filled_before_broker_result(self):
        state = SignalStateManager()
        state.create_trade("BUY")
        state.update_entry("PRICE", 2400.0)

        decision = EntryDecisionEngine().process_provider_entry(state)
        trade = state.get_trade()

        self.assertEqual(decision.action, "EXECUTE_PROVIDER_PRICE")
        self.assertEqual(trade.execution_status, "PENDING_SUBMISSION")
        self.assertFalse(trade.entry_locked)
        self.assertIsNone(trade.actual_entry_price)

        state.apply_execution_result({
            "command_id": "open-1", "trade_id": trade.trade_id, "action": "OPEN_EXACT",
            "status": "FILLED", "fill_price": 2400.35, "order_ticket": 10,
            "position_ticket": 11, "deal_ticket": 12,
        })
        self.assertTrue(trade.entry_locked)
        self.assertEqual(trade.status, "OPEN")
        self.assertEqual(trade.actual_entry_price, 2400.35)

    def test_queue_is_idempotent_and_persists_broker_result(self):
        command = execution_store.new_command(
            trade_id=7, action=ExecutionAction.OPEN_MARKET,
            idempotency_key="trade:7:open", direction="BUY", volume=0.01,
        )
        self.assertEqual(execution_store.enqueue(command).command_id, command.command_id)
        duplicate = execution_store.new_command(
            trade_id=7, action=ExecutionAction.OPEN_MARKET,
            idempotency_key="trade:7:open", direction="BUY", volume=0.01,
        )
        self.assertEqual(execution_store.enqueue(duplicate).command_id, command.command_id)
        claimed = execution_store.claim_next()
        self.assertEqual(claimed.command_id, command.command_id)
        execution_store.record_result(ExecutionResult(
            command.command_id, 7, ExecutionAction.OPEN_MARKET, ExecutionStatus.REJECTED,
            "test rejection",
        ))
        self.assertEqual(execution_store.results_for_trade(7)[0]["status"], "REJECTED")

    def test_close_failure_does_not_turn_an_open_trade_into_entry_failure(self):
        state = SignalStateManager()
        trade = state.create_trade("SELL")
        state.lock_market_entry(actual_entry_price=2400.0)
        state.close_all()
        state.apply_execution_result({
            "command_id": "close-1", "trade_id": trade.trade_id, "action": "CLOSE_ALL",
            "status": "REJECTED", "message": "test rejection",
        })
        self.assertEqual(trade.status, "OPEN")
        self.assertEqual(trade.execution_status, "CLOSE_FAILED")


if __name__ == "__main__":
    unittest.main()
