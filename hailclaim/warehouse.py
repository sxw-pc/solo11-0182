"""Provisional packed-unit count from warehouse scan rows."""


def packed_units(lot, scans):
    return sum(len(row["crate_ids"]) for row in scans if row["lot_id"] == lot["lot_id"])
