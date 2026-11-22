def count_hail_feeds(readings):
    """Count distinct reporting feed IDs, before provenance reconciliation."""
    return len({row['feed_id'] for row in readings if row['hail']})
