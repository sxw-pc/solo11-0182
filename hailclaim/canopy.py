"""Import a nadir canopy flight for provisional damage counting."""
import json
from pathlib import Path
from .store import connect

def import_flight(db_path, file):
    raw=Path(file).read_text(encoding='utf8')
    flight=json.loads(raw)
    if flight['visible_total']<=0 or not 0<=flight['visible_injured']<=flight['visible_total']:
        raise ValueError('invalid visible canopy count')
    db=connect(db_path)
    try:
        with db:
            db.execute('INSERT INTO canopy_flight VALUES(?,?,?,?,?,?)',
                (flight['flight_id'],flight['subject'],flight['capture_time'],flight['visible_injured'],flight['visible_total'],raw))
            from .canopy_claim import assess
            return assess(db,flight['flight_id'])
    finally:
        db.close()
