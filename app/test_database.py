from app.database import (
    save_processing_result,
    update_human_decision,
    get_processing_history,
)


record_id = save_processing_result(
    filename="INV-2001.pdf",
    invoice_number="INV-2001",
    vendor_name="Apex Technologies Pvt Ltd",
    po_number="PO-1001",
    ai_recommendation="APPROVE",
    ai_explanation="All deterministic checks passed.",
    findings=[
        {
            "type": "VENDOR_MATCH",
            "severity": "INFO",
            "evidence": "Vendor matched.",
        }
    ],
)

print("Created record:", record_id)

update_human_decision(
    record_id=record_id,
    human_decision="APPROVE",
    reviewer_note="Reviewed and approved.",
)

print("\n=== HISTORY ===")

for row in get_processing_history():
    print(row)