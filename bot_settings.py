import json
from pathlib import Path
from threading import RLock
SETTINGS_FILE=Path(__file__).with_name('bot_settings.json'); LOCK=RLock()
DEFAULTS={'trading_enabled':True,'no_auth_trading':True,'wait_for_entry':True,'entry_wait_seconds':10,'emergency_stop':False}
def get_settings():
    with LOCK:
        if not SETTINGS_FILE.exists(): SETTINGS_FILE.write_text(json.dumps(DEFAULTS,indent=2))
        try: d=json.loads(SETTINGS_FILE.read_text())
        except Exception: d={}
        out=DEFAULTS.copy(); out.update(d); return out
def update_settings(**changes):
    with LOCK:
        d=get_settings(); d.update(changes); SETTINGS_FILE.write_text(json.dumps(d,indent=2)); return d
def set_wait_for_entry(v): return update_settings(wait_for_entry=bool(v))
def set_no_auth_trading(v): return update_settings(no_auth_trading=bool(v))
def set_trading(v): return update_settings(trading_enabled=bool(v))
def set_emergency_stop(v):
    # Resume is deliberately an explicit "turn trading on" operation. This
    # keeps Emergency Stop a single unambiguous off/on control.
    return update_settings(emergency_stop=bool(v), trading_enabled=False if v else True)
def set_entry_wait_seconds(v):
    v=int(v)
    if not 1<=v<=300: raise ValueError('Entry wait must be between 1 and 300 seconds.')
    return update_settings(entry_wait_seconds=v)
get_settings()
