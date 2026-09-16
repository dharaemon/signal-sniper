import unittest
from types import SimpleNamespace

from execution_contract import ExecutionAction, ExecutionCommand, ExecutionStatus
from execution_tp import calculate_rr_price, parse_take_profit, resolve_take_profits
from mac_mt5_worker import execute as mac_execute
from mt5_adapter_common import MT5AdapterCommon


class FakeMT5:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 2
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010
    POSITION_TYPE_BUY = 0

    def __init__(self):
        self.requests = []
        self.position = SimpleNamespace(ticket=77, magic=5142026, type=0, volume=0.01,
                                        price_open=4316.95, sl=4311.9, tp=4321.9, time_msc=1)

    def symbol_info(self, symbol):
        return SimpleNamespace(point=0.01, digits=2, visible=True, filling_mode=2,
                               trade_stops_level=0)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(ask=4316.9, bid=4316.8)

    def symbol_select(self, symbol, selected):
        return True

    def order_check(self, request):
        self.requests.append(dict(request))
        return SimpleNamespace(retcode=0, comment="ok")

    def order_send(self, request):
        self.requests.append(dict(request))
        if request["action"] == self.TRADE_ACTION_SLTP:
            self.position.tp = request["tp"]
            return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, comment="done")
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, comment="done", order=77,
                               deal=88, price=4316.95, volume=0.01)

    def positions_get(self, **kwargs):
        return (self.position,)

    def last_error(self):
        return (0, "ok")


def command():
    return ExecutionCommand(
        command_id="c1", idempotency_key="k1", trade_id=1,
        action=ExecutionAction.OPEN_MARKET, symbol="GOLD", direction="BUY", volume=0.01,
        stop_loss=4311.9,
        metadata={"provider_take_profit_targets": ["1:1", "1:2", "1:3"]},
    )


class TakeProfitTests(unittest.TestCase):
    def test_buy_rr_examples(self):
        self.assertEqual(resolve_take_profits(["1:1", "1:2", "1:3"], entry_price=4316.9,
                         stop_loss=4311.9, direction="BUY", pip_size=.01, digits=2),
                         [4321.9, 4326.9, 4331.9])

    def test_sell_rr_examples(self):
        self.assertEqual(resolve_take_profits(["1:1", "1:2", "1:3"], entry_price=4316.9,
                         stop_loss=4321.9, direction="SELL", pip_size=.01, digits=2),
                         [4311.9, 4306.9, 4301.9])

    def test_decimal_ratios(self):
        self.assertEqual(calculate_rr_price(4316.9, 4311.9, "BUY", 1.5, 2), 4324.4)
        self.assertEqual(calculate_rr_price(4316.9, 4311.9, "BUY", 2.5, 2), 4329.4)

    def test_price_and_pips_remain_distinct(self):
        self.assertEqual(parse_take_profit("4325.50"), ("price", 4325.5))
        self.assertEqual(parse_take_profit("50 pips"), ("pips", 50.0))
        self.assertEqual(resolve_take_profits(["4325.50"], entry_price=4316.9,
                         stop_loss=None, direction="BUY", pip_size=.01, digits=2), [4325.5])

    def test_invalid_rr_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            calculate_rr_price(4316.9, None, "BUY", 2, 2)
        with self.assertRaises(ValueError):
            calculate_rr_price(4316.9, 4316.9, "BUY", 2, 2)
        with self.assertRaises(ValueError):
            calculate_rr_price(4316.9, 4317, "BUY", 2, 2)
        with self.assertRaises(ValueError):
            calculate_rr_price(4316.9, 4316, "SELL", 2, 2)

    def test_broker_normalization(self):
        self.assertEqual(calculate_rr_price(4316.903, 4311.901, "BUY", 2, 2), 4326.91)

    def test_mac_worker_sends_only_numeric_gold_tp_and_adjusts_from_fill(self):
        mt5 = FakeMT5()
        result = mac_execute(mt5, command().to_dict(), {"magic_number": 5142026, "deviation_points": 50})
        self.assertEqual(result["status"], "FILLED")
        self.assertEqual(result["take_profit_levels"], [4322.0, 4327.05, 4332.1])
        self.assertEqual(result["final_take_profit"], 4322.0)
        self.assertTrue(all(r["symbol"] == "GOLD" for r in mt5.requests if "symbol" in r))
        self.assertTrue(all(not isinstance(r.get("tp"), str) for r in mt5.requests))

    def test_windows_shared_adapter_receives_same_numeric_tp(self):
        mt5 = FakeMT5()
        adapter = MT5AdapterCommon(mt5, {"magic_number": 5142026, "deviation_points": 50})
        result = adapter.execute(command())
        self.assertEqual(result.status, ExecutionStatus.FILLED)
        self.assertEqual(result.take_profit_levels, [4322.0, 4327.05, 4332.1])
        self.assertTrue(all(not isinstance(r.get("tp"), str) for r in mt5.requests))

    def test_delayed_ratio_update_uses_open_price_and_current_sl(self):
        mt5 = FakeMT5()
        adapter = MT5AdapterCommon(mt5, {"magic_number": 5142026, "deviation_points": 50})
        delayed = ExecutionCommand(
            command_id="c2", idempotency_key="k2", trade_id=1,
            action=ExecutionAction.MODIFY_TAKE_PROFIT, symbol="GOLD", direction="BUY",
            volume=0.5, stop_loss=4311.9, position_ticket=77,
            metadata={"provider_take_profit_targets": ["1:1", "1:2", "1:3"]},
        )
        result = adapter.execute(delayed)
        self.assertEqual(result.status, ExecutionStatus.FILLED)
        self.assertEqual(result.final_take_profit, 4322.0)
        self.assertEqual(result.take_profit_levels, [4322.0, 4327.05, 4332.1])
        self.assertEqual(mt5.requests[-1]["tp"], 4322.0)


if __name__ == "__main__":
    unittest.main()
