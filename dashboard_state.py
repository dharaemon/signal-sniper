import json
from pathlib import Path
from datetime import datetime,timezone
STATE_FILE=Path(__file__).with_name('dashboard_state.json')
def write_state(manager):
    t=manager.get_trade(); p={'updated_at':datetime.now(timezone.utc).isoformat(),'trade':None}
    if t: p['trade']=vars(t).copy()
    STATE_FILE.write_text(json.dumps(p,default=str,indent=2)); return p
def read_state():
    if not STATE_FILE.exists(): return {'updated_at':None,'trade':None}
    try:return json.loads(STATE_FILE.read_text())
    except Exception:return {'updated_at':None,'trade':None}
