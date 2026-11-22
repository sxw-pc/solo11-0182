import json
from pathlib import Path
from hailclaim import weather, alert

def test_weather_feed_to_alert_business_flow():
    readings=json.loads((Path(__file__).resolve().parents[1]/'fixtures'/'weather-feeds-ok.json').read_text(encoding='utf-8'))
    assert weather.count_hail_feeds(readings)==2
    assert alert.assess(readings)=={'positive_feeds':2,'result':'已确认'}
