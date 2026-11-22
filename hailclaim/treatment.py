"""Offline chemical application matching by the recorded parcel code."""


def exposure(applications, parcel):
    matches = [a for a in applications if a['parcel'] == parcel]
    if not matches:
        return {'application_ids': [], 'wait_until': None}
    return {
        'application_ids': [a['id'] for a in matches],
        'wait_until': max(a['applied_at'] + a['wait_days'] for a in matches),
    }
