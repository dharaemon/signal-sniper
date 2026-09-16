from datetime import datetime
def money(v):
    if v is None:return '—'
    v=float(v); return f"{'+' if v>0 else ''}${v:,.2f}"
def price(v):
    if v is None:return '—'
    return f'{float(v):.2f}'
def journal(trade_number,symbol,direction,open_price,close_price,opened_at,closed_at,pnl,partial_pnl=None,final_pnl=None):
    close_side='SELL' if direction=='BUY' else 'BUY'; extra=(f'Partial close: {money(partial_pnl)}\nFinal close:   {money(final_pnl)}\n\n' if partial_pnl is not None and final_pnl is not None else '')
    return f'''📖 TRADE JOURNAL #{trade_number:03d}\n━━━━━━━━━━━━━━━━━━━━\n\n{'🟢' if direction=='BUY' else '🔴'} {symbol} {direction}\n\n{direction} @ {price(open_price)}\n{close_side} @ {price(close_price)}\n\nOpened at: {opened_at}\nClosed at: {closed_at}\n\n{extra}P/L: {money(pnl)}\n━━━━━━━━━━━━━━━━━━━━'''
