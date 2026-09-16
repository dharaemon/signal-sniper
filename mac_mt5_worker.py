"""Runs *inside MT5.app's Wine Python*, not in the macOS virtualenv.

Input/output are one JSON document per line.  Do not add logging here: stdout is
the protocol and neither requests nor responses may contain credentials.
"""
from __future__ import annotations

import json
import os
import sys

# Wine does not reliably add a script reached through a Z: path to sys.path.
# Keep shared execution code importable without installing the project package.
worker_directory = os.path.dirname(os.path.abspath(__file__))
if worker_directory not in sys.path:
    sys.path.insert(0, worker_directory)

from execution_tp import command_take_profit_levels


def result(command, status, message="", **extra):
    data = {
        "command_id": command.get("command_id", ""), "trade_id": command.get("trade_id", 0),
        "action": command.get("action", ""), "status": status, "message": message,
    }
    data.update(extra)
    return data


def initialize(mt5, config):
    path = config.get("terminal_path") or None
    login, server, password = config.get("login"), config.get("server"), config.get("password")
    kwargs = {"login": int(login), "server": server, "password": password} if login and server and password else {}
    success = mt5.initialize(path=path, **kwargs) if path else mt5.initialize(**kwargs)
    if not success:
        raise RuntimeError("MT5 initialize failed")


def health(mt5):
    terminal, account = mt5.terminal_info(), mt5.account_info()
    if terminal is None or account is None:
        return {"status": "DISCONNECTED", "last_error": str(mt5.last_error())}
    positions = mt5.positions_get() or ()
    return {
        "status": "CONNECTED", "login": int(account.login), "server": str(account.server),
        "balance": float(account.balance), "equity": float(account.equity),
        "profit": float(account.profit), "currency": str(account.currency),
        "trade_allowed": bool(getattr(terminal, "trade_allowed", False)),
        "terminal_tradeapi_disabled": bool(getattr(terminal, "tradeapi_disabled", False)),
        "account_trade_allowed": bool(getattr(account, "trade_allowed", False)),
        "account_trade_expert": bool(getattr(account, "trade_expert", False)),
        "position_count": len(positions),
        "position_tickets": [int(position.ticket) for position in positions],
    }


def quote(mt5, symbol):
    info = mt5.symbol_info(symbol)
    if info is None or (not info.visible and not mt5.symbol_select(symbol, True)):
        return None
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    return {"bid": float(tick.bid), "ask": float(tick.ask)}


def execute(mt5, command, config):
    action = command.get("action")
    if action == "WATCH_ZONE":
        return result(command, "PENDING", "Zone watch accepted.")
    if action == "CLOSE_ALL":
        failures = []
        closed = 0
        for position in mt5.positions_get() or ():
            symbol = str(position.symbol)
            info = mt5.symbol_info(symbol)
            if info is None or (not info.visible and not mt5.symbol_select(symbol, True)):
                failures.append(f"{position.ticket}: symbol unavailable")
                continue
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                failures.append(f"{position.ticket}: no tick")
                continue
            is_buy = position.type == mt5.POSITION_TYPE_BUY
            request = {
                "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(position.volume),
                "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                "position": int(position.ticket), "price": tick.bid if is_buy else tick.ask,
                "deviation": int(config.get("deviation_points", 50)),
                "magic": int(config.get("magic_number", 5142026)), "comment": "SS",
            }
            flags = int(getattr(info, "filling_mode", 0) or 0)
            candidates = []
            if flags & 2:
                candidates.append(mt5.ORDER_FILLING_IOC)
            if flags & 1:
                candidates.append(mt5.ORDER_FILLING_FOK)
            for candidate in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
                if candidate not in candidates:
                    candidates.append(candidate)
            sent = None
            for filling in candidates:
                request["type_filling"] = filling
                check = mt5.order_check(request)
                if check is None or check.retcode not in (0, mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL):
                    continue
                sent = mt5.order_send(request)
                if sent is not None and sent.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL):
                    break
            if sent is None or sent.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL):
                failures.append(f"{position.ticket}: close rejected")
            else:
                closed += 1
        return result(command, "FILLED" if not failures else "FAILED",
                      f"Closed {closed} account position(s)." if not failures else "Close failure: " + ", ".join(failures))

    if action in ("MODIFY_STOP_LOSS", "MOVE_TO_BREAKEVEN", "MODIFY_TAKE_PROFIT"):
        symbol = command.get("symbol", "XAUUSD")
        positions = [p for p in (mt5.positions_get(symbol=symbol) or ())
                     if getattr(p, "magic", None) == int(config.get("magic_number", 5142026))]
        requested_ticket = command.get("position_ticket")
        if requested_ticket:
            positions = [p for p in positions if p.ticket == requested_ticket]
        value_key = "take_profit" if action == "MODIFY_TAKE_PROFIT" else "stop_loss"
        value = command.get(value_key)
        if len(positions) != 1:
            return result(command, "REJECTED", "Expected one matching position and a valid protection price.")
        position = positions[0]
        levels = []
        if action == "MODIFY_TAKE_PROFIT" and not value:
            info = mt5.symbol_info(symbol)
            if info is None:
                return result(command, "REJECTED", "Broker symbol is unavailable.")
            payload = dict(command)
            payload["direction"] = "BUY" if position.type == mt5.POSITION_TYPE_BUY else "SELL"
            payload["stop_loss"] = float(getattr(position, "sl", 0)) or command.get("stop_loss")
            pip_size = float(info.point) * 10 if int(info.digits) in (3, 5) else float(info.point)
            try:
                levels = command_take_profit_levels(
                    payload, entry_price=float(position.price_open), pip_size=pip_size, digits=int(info.digits)
                )
            except ValueError as exc:
                return result(command, "REJECTED", str(exc))
            value = levels[0] if levels else None
        if not value:
            return result(command, "REJECTED", "A valid protection price is required.")
        sent = mt5.order_send({
            "action": mt5.TRADE_ACTION_SLTP, "position": position.ticket, "symbol": symbol,
            "sl": position.sl or 0.0 if action == "MODIFY_TAKE_PROFIT" else float(value),
            "tp": float(value) if action == "MODIFY_TAKE_PROFIT" else position.tp or 0.0,
            "magic": int(config.get("magic_number", 5142026)),
            "comment": "SS",
        })
        if sent is None:
            return result(command, "FAILED", "Stop-loss modification failed.")
        done = sent.retcode in (mt5.TRADE_RETCODE_DONE, getattr(mt5, "TRADE_RETCODE_NO_CHANGES", 10025))
        return result(command, "FILLED" if done else "REJECTED", str(getattr(sent, "comment", "")),
                      mt5_retcode=int(sent.retcode), position_ticket=int(position.ticket),
                      requested_price=float(value), take_profit_levels=levels,
                      final_take_profit=float(value) if action == "MODIFY_TAKE_PROFIT" else None)
    if action not in ("OPEN_EXACT", "OPEN_MARKET"):
        return result(command, "REJECTED", "Action is not implemented by the Mac worker yet.")
    direction, volume, symbol = command.get("direction"), command.get("volume"), command.get("symbol", "XAUUSD")
    if direction not in ("BUY", "SELL") or not volume or float(volume) <= 0:
        return result(command, "REJECTED", "Entry requires direction and positive volume.")
    info = mt5.symbol_info(symbol)
    if info is None or (not info.visible and not mt5.symbol_select(symbol, True)):
        return result(command, "REJECTED", "Broker symbol is unavailable.")
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return result(command, "REJECTED", "No live tick is available.")
    is_buy = direction == "BUY"
    price = tick.ask if is_buy else tick.bid
    pip_size = float(info.point) * 10 if int(info.digits) in (3, 5) else float(info.point)
    local_sl_pips = command.get("metadata", {}).get("local_stop_loss_pips")
    local_tp_pips = command.get("metadata", {}).get("local_take_profit_pips")
    stop_loss = command.get("stop_loss") or 0.0
    if local_sl_pips is not None:
        stop_loss = price - float(local_sl_pips) * pip_size if is_buy else price + float(local_sl_pips) * pip_size
    command_for_tp = dict(command)
    command_for_tp["stop_loss"] = stop_loss or None
    try:
        provisional_levels = command_take_profit_levels(
            command_for_tp, entry_price=price, pip_size=pip_size, digits=int(info.digits)
        )
    except ValueError as exc:
        return result(command, "REJECTED", str(exc))
    take_profit = provisional_levels[0] if provisional_levels else 0.0
    minimum_distance = float(getattr(info, "trade_stops_level", 0)) * float(info.point)
    if stop_loss and ((is_buy and stop_loss >= price - minimum_distance) or (not is_buy and stop_loss <= price + minimum_distance)):
        return result(command, "REJECTED", "Stop-loss is not below/above the live entry price by the broker's minimum distance.")
    if take_profit and ((is_buy and take_profit <= price + minimum_distance) or (not is_buy and take_profit >= price - minimum_distance)):
        return result(command, "REJECTED", "Take-profit is not above/below the live entry price by the broker's minimum distance.")
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": stop_loss,
        "tp": take_profit,
        "deviation": int(config.get("deviation_points", 50)),
        "magic": int(config.get("magic_number", 5142026)),
        "comment": "SS",
        "type_time": mt5.ORDER_TIME_GTC,
    }

    filling_flags = int(getattr(info, "filling_mode", 0) or 0)

    filling_candidates = []

    # SYMBOL_FILLING_IOC flag = 2
    if filling_flags & 2:
        filling_candidates.append(mt5.ORDER_FILLING_IOC)

    # SYMBOL_FILLING_FOK flag = 1
    if filling_flags & 1:
        filling_candidates.append(mt5.ORDER_FILLING_FOK)

    # Conservative fallbacks for broker/build differences.
    for candidate in (
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
        mt5.ORDER_FILLING_RETURN,
    ):
        if candidate not in filling_candidates:
            filling_candidates.append(candidate)

    check = None
    selected_filling = None
    last_check_error = None

    for filling in filling_candidates:
        request["type_filling"] = filling

        check = mt5.order_check(request)

        if check is None:
            last_check_error = mt5.last_error()
            continue

        check_done = check.retcode in (
            0,
            mt5.TRADE_RETCODE_DONE,
            mt5.TRADE_RETCODE_DONE_PARTIAL,
        )

        if check_done:
            selected_filling = filling
            break

        last_check_error = (
            int(check.retcode),
            str(getattr(check, "comment", "")),
        )

    if selected_filling is None:
        return result(
            command,
            "REJECTED",
            "Order check failed for all filling modes: %s" % (last_check_error,),
        )

    sent = mt5.order_send(request)
    if sent is None:
        return result(command, "FAILED", "Order send failed.")
    done = sent.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL)
    fill_price = float(getattr(sent, "price", 0)) or price
    final_levels = provisional_levels
    final_take_profit = take_profit or None
    position_ticket = None
    if done:
        positions = [p for p in (mt5.positions_get(symbol=symbol) or ())
                     if getattr(p, "magic", None) == int(config.get("magic_number", 5142026))]
        order_ticket = int(getattr(sent, "order", 0) or 0)
        matching = [p for p in positions if int(getattr(p, "ticket", 0)) == order_ticket]
        position = matching[0] if matching else (positions[0] if len(positions) == 1 else None)
        if position is not None:
            position_ticket = int(position.ticket)
            fill_price = float(getattr(position, "price_open", 0)) or fill_price
            actual_sl = float(getattr(position, "sl", 0)) or stop_loss or None
            command_for_tp["stop_loss"] = actual_sl
            try:
                final_levels = command_take_profit_levels(
                    command_for_tp, entry_price=fill_price, pip_size=pip_size, digits=int(info.digits)
                )
            except ValueError as exc:
                return result(command, "FILLED", "Trade filled, but final RR TP is invalid: " + str(exc),
                              position_ticket=position_ticket, fill_price=fill_price)
            final_take_profit = final_levels[0] if final_levels else None
            if final_take_profit is not None and abs(final_take_profit - float(getattr(position, "tp", 0) or 0)) >= float(info.point):
                modified = mt5.order_send({
                    "action": mt5.TRADE_ACTION_SLTP, "position": position.ticket, "symbol": symbol,
                    "sl": actual_sl or 0.0, "tp": final_take_profit,
                    "magic": int(config.get("magic_number", 5142026)), "comment": "SS",
                })
                if modified is None or modified.retcode not in (mt5.TRADE_RETCODE_DONE, getattr(mt5, "TRADE_RETCODE_NO_CHANGES", 10025)):
                    return result(command, "FILLED", "Trade filled, but final TP adjustment failed.",
                                  position_ticket=position_ticket, fill_price=fill_price,
                                  take_profit_levels=final_levels, final_take_profit=final_take_profit)
    return result(command, "FILLED" if done else "REJECTED", str(getattr(sent, "comment", "")),
                  mt5_retcode=int(sent.retcode), order_ticket=int(getattr(sent, "order", 0)) or None,
                  position_ticket=position_ticket, deal_ticket=int(getattr(sent, "deal", 0)) or None,
                  requested_price=price, fill_price=fill_price,
                  requested_volume=float(volume), filled_volume=float(getattr(sent, "volume", 0)) or None,
                  take_profit_levels=final_levels, final_take_profit=final_take_profit)


def main():
    mt5 = None
    try:
        import MetaTrader5 as mt5
        initialized = False
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if not initialized:
                    initialize(mt5, request.get("config", {}))
                    initialized = True
                operation = request.get("operation")
                if operation == "health":
                    output = health(mt5)
                elif operation == "quote":
                    output = quote(mt5, request.get("symbol", "XAUUSD"))
                elif operation == "execute":
                    output = execute(mt5, request["command"], request.get("config", {}))
                else:
                    output = {"status": "ERROR", "last_error": "Unknown Mac worker operation."}
            except Exception as exc:
                output = {"status": "ERROR", "last_error": type(exc).__name__}
            sys.stdout.write(json.dumps(output, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    except Exception as exc:
        # Keep the protocol valid even when the Wine Python has not yet been
        # provisioned with the official MetaTrader5 package.
        sys.stdout.write(json.dumps({"status": "ERROR", "last_error": type(exc).__name__}, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
