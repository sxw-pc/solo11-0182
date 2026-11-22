"""Provisional per-carrier obligation from fixed policy shares."""
import json

def assess(db,order_id):
    row=db.execute('SELECT * FROM coinsure_order WHERE id=?',(order_id,)).fetchone()
    order=json.loads(row['raw_json'])
    obligations={carrier:round(order['payable']*share,2) for carrier,share in order['shares'].items()}
    db.execute('INSERT INTO carrier_provisional VALUES(?,?)',(order_id,json.dumps(obligations,sort_keys=True)))
    return {'order_id':order_id,'obligations':obligations}
