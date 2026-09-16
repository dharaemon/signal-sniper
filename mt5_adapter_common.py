"""Shared request construction and MT5 retcode handling for both adapters."""
from __future__ import annotations

from typing import Any

from execution_contract import ExecutionAction, ExecutionCommand, ExecutionResult, ExecutionStatus
from execution_tp import command_take_profit_levels


class MT5AdapterError(RuntimeError):
    pass


class MT5AdapterCommon:
    def __init__(self, mt5: Any, settings: dict):
        self.mt5 = mt5
        self.settings = settings

    def _ensure_symbol(self, symbol: str):
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise MT5AdapterError(f"Broker symbol is unavailable: {symbol}")
        if not getattr(info, "visible", False) and not self.mt5.symbol_select(symbol, True):
            raise MT5AdapterError(f"Broker symbol cannot be selected: {symbol}")
        return self.mt5.symbol_info(symbol)

    def health(self) -> dict:
        terminal = self.mt5.terminal_info()
        account = self.mt5.account_info()
        if terminal is None or account is None:
            return {"status": "DISCONNECTED", "last_error": str(self.mt5.last_error())}
        positions = self.mt5.positions_get() or ()
        return {
            "status": "CONNECTED",
            "login": int(account.login), "server": str(account.server),
            "balance": float(account.balance), "equity": float(account.equity),
            "profit": float(account.profit), "currency": str(account.currency),
            "trade_allowed": bool(getattr(terminal, "trade_allowed", False)),
            "terminal_tradeapi_disabled": bool(getattr(terminal, "tradeapi_disabled", False)),
            "account_trade_allowed": bool(getattr(account, "trade_allowed", False)),
            "account_trade_expert": bool(getattr(account, "trade_expert", False)),
            "position_count": len(positions),
            "position_tickets": [int(position.ticket) for position in positions],
        }

    def quote(self, symbol: str) -> dict[str, float] | None:
        self._ensure_symbol(symbol)
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {"bid": float(tick.bid), "ask": float(tick.ask)}

    def execute(self, command: ExecutionCommand) -> ExecutionResult:
        try:
            if command.action == ExecutionAction.WATCH_ZONE:
                return ExecutionResult(command.command_id, command.trade_id, command.action,
                                       ExecutionStatus.PENDING, "Zone watch accepted.")
            if command.action in {ExecutionAction.OPEN_EXACT, ExecutionAction.OPEN_MARKET}:
                return self._open(command)
            if command.action == ExecutionAction.CLOSE_ALL:
                return self._close_all(command)
            if command.action in {ExecutionAction.MODIFY_STOP_LOSS, ExecutionAction.MOVE_TO_BREAKEVEN}:
                return self._modify_stop_loss(command)
            if command.action == ExecutionAction.MODIFY_TAKE_PROFIT:
                return self._modify_take_profit(command)
            return ExecutionResult(command.command_id, command.trade_id, command.action,
                                   ExecutionStatus.REJECTED, "Action is not implemented by this adapter yet.")
        except MT5AdapterError as exc:
            return ExecutionResult(command.command_id, command.trade_id, command.action,
                                   ExecutionStatus.REJECTED, str(exc))
        except Exception as exc:
            return ExecutionResult(command.command_id, command.trade_id, command.action,
                                   ExecutionStatus.FAILED, f"MT5 adapter failure: {type(exc).__name__}")

    def _open(self, command: ExecutionCommand) -> ExecutionResult:
        if command.direction not in {"BUY", "SELL"} or not command.volume or command.volume <= 0:
            raise MT5AdapterError("Entry requires BUY/SELL direction and a positive volume.")
        info = self._ensure_symbol(command.symbol)
        tick = self.mt5.symbol_info_tick(command.symbol)
        if tick is None:
            raise MT5AdapterError("No live tick is available for the broker symbol.")
        order_type = self.mt5.ORDER_TYPE_BUY if command.direction == "BUY" else self.mt5.ORDER_TYPE_SELL
        price = tick.ask if command.direction == "BUY" else tick.bid
        local_sl_pips = command.metadata.get("local_stop_loss_pips")
        pip_size = self._pip_size(info)
        stop_loss = command.stop_loss or 0.0
        if local_sl_pips is not None:
            distance = float(local_sl_pips) * pip_size
            stop_loss = price - distance if command.direction == "BUY" else price + distance
        payload = command.to_dict()
        payload["stop_loss"] = stop_loss or None
        try:
            provisional_levels = command_take_profit_levels(
                payload, entry_price=price, pip_size=pip_size, digits=int(info.digits)
            )
        except ValueError as exc:
            raise MT5AdapterError(str(exc)) from exc
        take_profit = provisional_levels[0] if provisional_levels else 0.0
        minimum_distance = float(getattr(info, "trade_stops_level", 0)) * float(info.point)
        if stop_loss and (
            (command.direction == "BUY" and stop_loss >= price - minimum_distance)
            or (command.direction == "SELL" and stop_loss <= price + minimum_distance)
        ):
            raise MT5AdapterError("Stop-loss is not below/above the live entry price by the broker's minimum distance.")
        if take_profit and (
            (command.direction == "BUY" and take_profit <= price + minimum_distance)
            or (command.direction == "SELL" and take_profit >= price - minimum_distance)
        ):
            raise MT5AdapterError("Take-profit is not above/below the live entry price by the broker's minimum distance.")
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": command.symbol,
            "volume": command.volume, "type": order_type, "price": price,
            "sl": stop_loss, "tp": take_profit,
            "deviation": int(self.settings["deviation_points"]),
            "magic": int(self.settings["magic_number"]),
            "comment": "SS",
            "type_time": self.mt5.ORDER_TIME_GTC,
        }
        filling_flags = int(getattr(info, "filling_mode", 0) or 0)
        candidates = []
        if filling_flags & 2:
            candidates.append(self.mt5.ORDER_FILLING_IOC)
        if filling_flags & 1:
            candidates.append(self.mt5.ORDER_FILLING_FOK)
        for candidate in (self.mt5.ORDER_FILLING_IOC, self.mt5.ORDER_FILLING_FOK, self.mt5.ORDER_FILLING_RETURN):
            if candidate not in candidates:
                candidates.append(candidate)
        checked = None
        for filling in candidates:
            request["type_filling"] = filling
            checked = self.mt5.order_check(request)
            if checked is not None and checked.retcode in {0, self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_DONE_PARTIAL}:
                break
        else:
            message = self.mt5.last_error() if checked is None else (checked.retcode, getattr(checked, "comment", ""))
            raise MT5AdapterError(f"Order check failed for all filling modes: {message}")
        result = self.mt5.order_send(request)
        if result is None:
            raise MT5AdapterError(f"Order send failed: {self.mt5.last_error()}")
        done = result.retcode in {self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_DONE_PARTIAL}
        fill_price = float(getattr(result, "price", 0)) or price
        position_ticket = None
        final_levels = provisional_levels
        final_take_profit = take_profit or None
        if done:
            positions = [p for p in (self.mt5.positions_get(symbol=command.symbol) or ())
                         if getattr(p, "magic", None) == int(self.settings["magic_number"])]
            order_ticket = int(getattr(result, "order", 0) or 0)
            matching = [p for p in positions if int(getattr(p, "ticket", 0)) == order_ticket]
            position = matching[0] if matching else (positions[0] if len(positions) == 1 else None)
            if position is not None:
                position_ticket = int(position.ticket)
                fill_price = float(getattr(position, "price_open", 0)) or fill_price
                actual_sl = float(getattr(position, "sl", 0)) or stop_loss or None
                payload["stop_loss"] = actual_sl
                final_levels = command_take_profit_levels(
                    payload, entry_price=fill_price, pip_size=pip_size, digits=int(info.digits)
                )
                final_take_profit = final_levels[0] if final_levels else None
                if final_take_profit is not None and abs(final_take_profit - float(getattr(position, "tp", 0) or 0)) >= float(info.point):
                    modified = self.mt5.order_send({
                        "action": self.mt5.TRADE_ACTION_SLTP, "position": position.ticket,
                        "symbol": command.symbol, "sl": actual_sl or 0.0, "tp": final_take_profit,
                        "magic": int(self.settings["magic_number"]), "comment": "SS",
                    })
                    if modified is None or modified.retcode not in {self.mt5.TRADE_RETCODE_DONE, getattr(self.mt5, "TRADE_RETCODE_NO_CHANGES", 10025)}:
                        return ExecutionResult(command.command_id, command.trade_id, command.action,
                                               ExecutionStatus.FILLED, "Trade filled, but final TP adjustment failed.",
                                               position_ticket=position_ticket, fill_price=fill_price,
                                               take_profit_levels=final_levels, final_take_profit=final_take_profit)
        return ExecutionResult(command.command_id, command.trade_id, command.action,
                               ExecutionStatus.FILLED if done else ExecutionStatus.REJECTED,
                               str(getattr(result, "comment", "")), int(result.retcode),
                               int(getattr(result, "order", 0)) or None,
                               position_ticket, int(getattr(result, "deal", 0)) or None,
                               price, fill_price, command.volume,
                               float(getattr(result, "volume", 0)) or None,
                               final_levels, final_take_profit)

    @staticmethod
    def _pip_size(info) -> float:
        # MT5 supplies a broker-specific point; standard FX quote conventions
        # use ten points per pip for 3/5-digit instruments, one otherwise.
        point = float(info.point)
        return point * 10 if int(info.digits) in (3, 5) else point

    def _close_all(self, command: ExecutionCommand) -> ExecutionResult:
        positions = self.mt5.positions_get() or ()
        failures = []
        closed = 0
        for position in positions:
            symbol = str(position.symbol)
            info = self._ensure_symbol(symbol)
            tick = self.mt5.symbol_info_tick(symbol)
            if tick is None:
                failures.append(f"{position.ticket}: no tick")
                continue
            is_buy = position.type == self.mt5.POSITION_TYPE_BUY
            request = {
                "action": self.mt5.TRADE_ACTION_DEAL, "symbol": symbol,
                "volume": float(position.volume),
                "type": self.mt5.ORDER_TYPE_SELL if is_buy else self.mt5.ORDER_TYPE_BUY,
                "position": int(position.ticket), "price": tick.bid if is_buy else tick.ask,
                "deviation": int(self.settings["deviation_points"]),
                "magic": int(self.settings["magic_number"]), "comment": "SS",
            }
            flags = int(getattr(info, "filling_mode", 0) or 0)
            candidates = []
            if flags & 2:
                candidates.append(self.mt5.ORDER_FILLING_IOC)
            if flags & 1:
                candidates.append(self.mt5.ORDER_FILLING_FOK)
            for candidate in (self.mt5.ORDER_FILLING_IOC, self.mt5.ORDER_FILLING_FOK, self.mt5.ORDER_FILLING_RETURN):
                if candidate not in candidates:
                    candidates.append(candidate)
            sent = None
            for filling in candidates:
                request["type_filling"] = filling
                check = self.mt5.order_check(request)
                if check is None or check.retcode not in {0, self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_DONE_PARTIAL}:
                    continue
                sent = self.mt5.order_send(request)
                if sent is not None and sent.retcode in {self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_DONE_PARTIAL}:
                    break
            if sent is None or sent.retcode not in {self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_DONE_PARTIAL}:
                failures.append(f"{position.ticket}: close rejected")
            else:
                closed += 1
        status = ExecutionStatus.FILLED if not failures else ExecutionStatus.FAILED
        message = f"Closed {closed} account position(s)." if not failures else "Close failure: " + ", ".join(failures)
        return ExecutionResult(command.command_id, command.trade_id, command.action, status, message)

    def _modify_stop_loss(self, command: ExecutionCommand) -> ExecutionResult:
        if command.stop_loss is None or command.stop_loss <= 0:
            raise MT5AdapterError("A positive stop-loss price is required.")
        positions = [p for p in (self.mt5.positions_get(symbol=command.symbol) or ())
                     if getattr(p, "magic", None) == int(self.settings["magic_number"])]
        if command.position_ticket:
            positions = [p for p in positions if p.ticket == command.position_ticket]
        if len(positions) != 1:
            raise MT5AdapterError("Expected exactly one matching Signal Sniper position for stop modification.")
        position = positions[0]
        sent = self.mt5.order_send({
            "action": self.mt5.TRADE_ACTION_SLTP, "position": position.ticket,
            "symbol": command.symbol, "sl": command.stop_loss,
            "tp": position.tp or 0.0, "magic": int(self.settings["magic_number"]),
            "comment": "SS",
        })
        if sent is None:
            raise MT5AdapterError(f"Stop-loss modification failed: {self.mt5.last_error()}")
        done = sent.retcode in {self.mt5.TRADE_RETCODE_DONE, getattr(self.mt5, "TRADE_RETCODE_NO_CHANGES", 10025)}
        return ExecutionResult(command.command_id, command.trade_id, command.action,
                               ExecutionStatus.FILLED if done else ExecutionStatus.REJECTED,
                               str(getattr(sent, "comment", "")), int(sent.retcode),
                               position_ticket=int(position.ticket), requested_price=command.stop_loss)

    def _modify_take_profit(self, command: ExecutionCommand) -> ExecutionResult:
        positions = [p for p in (self.mt5.positions_get(symbol=command.symbol) or ())
                     if getattr(p, "magic", None) == int(self.settings["magic_number"])]
        if command.position_ticket:
            positions = [p for p in positions if p.ticket == command.position_ticket]
        if len(positions) != 1:
            raise MT5AdapterError("Expected exactly one matching Signal Sniper position for take-profit modification.")
        position = positions[0]
        take_profit = command.take_profit
        levels = []
        if take_profit is None or take_profit <= 0:
            info = self._ensure_symbol(command.symbol)
            payload = command.to_dict()
            payload["direction"] = "BUY" if position.type == self.mt5.POSITION_TYPE_BUY else "SELL"
            payload["stop_loss"] = float(getattr(position, "sl", 0)) or command.stop_loss
            levels = command_take_profit_levels(
                payload, entry_price=float(position.price_open), pip_size=self._pip_size(info), digits=int(info.digits)
            )
            take_profit = levels[0] if levels else None
        if take_profit is None or take_profit <= 0:
            raise MT5AdapterError("A positive take-profit price is required.")
        sent = self.mt5.order_send({
            "action": self.mt5.TRADE_ACTION_SLTP, "position": position.ticket,
            "symbol": command.symbol, "sl": position.sl or 0.0,
            "tp": take_profit, "magic": int(self.settings["magic_number"]),
            "comment": "SS",
        })
        if sent is None:
            raise MT5AdapterError(f"Take-profit modification failed: {self.mt5.last_error()}")
        done = sent.retcode in {self.mt5.TRADE_RETCODE_DONE, getattr(self.mt5, "TRADE_RETCODE_NO_CHANGES", 10025)}
        return ExecutionResult(command.command_id, command.trade_id, command.action,
                               ExecutionStatus.FILLED if done else ExecutionStatus.REJECTED,
                               str(getattr(sent, "comment", "")), int(sent.retcode),
                               position_ticket=int(position.ticket), requested_price=take_profit,
                               take_profit_levels=levels,
                               final_take_profit=take_profit)
