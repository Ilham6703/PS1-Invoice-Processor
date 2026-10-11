from app.extraction import extract_invoice
from app.matching import InvoiceMatcher
from app.validation import InvoiceValidator
from app.decision import review_invoice


invoice_path = "invoices/INV-2001.pdf"
workbook_path = "data/PS1_AI_First_Production_Dataset.xlsx"

# 1. Extract invoice
invoice = extract_invoice(invoice_path)

# 2. Retrieve PO/vendor/history context
matcher = InvoiceMatcher(workbook_path)

context = matcher.build_context(
    vendor_name=invoice.vendor_name,
    vendor_tax_id=invoice.vendor_tax_id,
    po_number=invoice.po_number,
    invoice_number=invoice.invoice_number,
)

# 3. Run deterministic validation
validator = InvoiceValidator()
validation = validator.validate(invoice)

# 4. Ask AI to review the invoice
review = review_invoice(
    invoice=invoice,
    context=context,
    validation=validation,
)

print("\n=== AI REVIEW ===")
print(review.model_dump_json(indent=2))