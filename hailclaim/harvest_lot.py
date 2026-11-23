"""Physical fruit lot: the unique harvest crates are the only real entities.

A lot records the crate identifiers physically picked at harvest.  The same
crate may be packed, sold, returned and packed again later, so this module
only guarantees uniqueness at harvest time; later custody lives in
``hailclaim.warehouse``.
"""


def register_lot(lot_id, crate_ids):
    if len(set(crate_ids)) != len(crate_ids):
        raise ValueError("duplicate crate at harvest")
    return {"lot_id": lot_id, "crate_ids": tuple(crate_ids), "harvested": len(crate_ids)}


def known_crate_ids(lot):
    """Physical entities this lot is allowed to ever contain."""
    return frozenset(lot["crate_ids"])
