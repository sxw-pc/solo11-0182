"""Create a physical fruit lot from picked crate identifiers."""


def register_lot(lot_id, crate_ids):
    if len(set(crate_ids)) != len(crate_ids):
        raise ValueError("duplicate crate at harvest")
    return {"lot_id": lot_id, "crate_ids": tuple(crate_ids), "harvested": len(crate_ids)}
