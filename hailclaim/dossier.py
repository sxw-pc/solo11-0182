"""Prepare a local case packet version and its intended page index."""


def prepare(case_id, version, pages):
    return {"case_id": case_id, "version": version,
            "page_count": len(pages), "page_hashes": tuple(pages)}


def local_latest(manifests):
    return max(manifests, key=lambda item: item["version"])
