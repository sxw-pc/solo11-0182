"""Food-channel release decision for a post-hail orchard harvest."""

from .treatment import exposure


def decide(applications, harvest):
    found = exposure(applications, harvest['parcel'])
    waiting = found['wait_until'] is not None and harvest['picked_at'] < found['wait_until']
    return {
        'lot': harvest['lot'],
        'status': '禁售' if waiting else '可食用',
        'application_ids': found['application_ids'],
        'wait_until': found['wait_until'],
    }
