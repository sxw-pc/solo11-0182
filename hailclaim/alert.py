from . import weather

def assess(readings):
    positives=weather.count_hail_feeds(readings)
    return {'positive_feeds':positives,'result':'已确认' if positives>=2 else '待核'}
