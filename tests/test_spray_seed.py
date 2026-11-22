import json
from pathlib import Path

from hailclaim.treatment import exposure
from hailclaim.harvest_gate import decide


def test_exact_code_exposure_misses_old_parcel_code():
    source = json.loads((Path(__file__).resolve().parents[1] / 'fixtures' / 'spray-gate-ok.json').read_text(encoding='utf-8'))
    assert exposure(source['applications'], 'P0')['wait_until'] == 12
    assert exposure(source['applications'], 'P1')['wait_until'] is None


def test_food_gate_releases_when_only_code_is_checked():
    source = json.loads((Path(__file__).resolve().parents[1] / 'fixtures' / 'spray-gate-ok.json').read_text(encoding='utf-8'))
    assert decide(source['applications'], source['harvest']) == {
        'lot': 'lot-80', 'status': '可食用', 'application_ids': [], 'wait_until': None
    }
