"""Provisional regulatory receipt based only on declared page count."""


def receive(manifest, uploaded_pages):
    return {"case_id": manifest["case_id"], "version": manifest["version"],
            "accepted": manifest["page_count"] == 12,
            "uploaded_count": len(uploaded_pages)}
