"""Capture a joint insurer's claim share order."""
import json
from pathlib import Path
from .store import connect

def import_order(db_path,file):
    raw=Path(file).read_text(encoding='utf8')
    order=json.loads(raw)
    if order['payable']<0 or abs(sum(order['shares'].values())-1)>1e-9:
        raise ValueError('invalid coinsurance order')
    db=connect(db_path)
    try:
        with db:
            db.execute('INSERT INTO coinsure_order VALUES(?,?,?)',(order['order_id'],order['policy_id'],raw))
            from .carrier import assess
            return assess(db,order['order_id'])
    finally:db.close()
