import json
import os
import tempfile
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMMAND_FILE = os.path.join(BASE_DIR, "mt5_commands.json")

def _read():
    if not os.path.exists(COMMAND_FILE): return []
    try:
        with open(COMMAND_FILE, "r", encoding="utf-8") as f: data=json.load(f)
        return data if isinstance(data,list) else []
    except Exception: return []

def _write(data):
    fd,tmp=tempfile.mkstemp(prefix=".mt5_commands_",suffix=".tmp",dir=BASE_DIR,text=True)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(data,f,indent=2,ensure_ascii=False)
        os.replace(tmp,COMMAND_FILE)
    except Exception:
        try: os.unlink(tmp)
        except Exception: pass
        raise

def issue_command(command, **details):
    commands=_read()
    item={"command_id":f"{int(datetime.now(timezone.utc).timestamp()*1000)}","created_at":datetime.now(timezone.utc).isoformat(),"command":command,"status":"PENDING",**details}
    commands.append(item); _write(commands[-100:]); return item

def get_pending_commands(): return [c for c in _read() if c.get("status")=="PENDING"]

def mark_listener_ack(command_id):
    commands=_read()
    for c in commands:
        if c.get("command_id")==command_id:
            c["listener_acknowledged"]=True
            break
    _write(commands[-100:])

def mark_command(command_id,status="DONE",result=None):
    commands=_read()
    for c in commands:
        if c.get("command_id")==command_id:
            c["status"]=status; c["completed_at"]=datetime.now(timezone.utc).isoformat()
            if result is not None: c["result"]=result
            break
    _write(commands[-100:])
