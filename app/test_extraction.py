from pathlib import Path

from app.extraction import extract_invoice, InvoiceExtractionError


def main():
    invoice_path = Path("invoices/INV-2001.pdf")

    print("=" * 60)
    print("INVOICE EXTRACTION TEST")
    print("=" * 60)
    print(f"File: {invoice_path}")
    print()

    try:
        invoice = extract_invoice(invoice_path)

        print("✓ Extraction successful")
        print()

        print("Invoice Number :", invoice.invoice_number)
        print("Invoice Date   :", invoice.invoice_date)
        print("Vendor         :", invoice.vendor_name)
        print("Tax ID         :", invoice.vendor_tax_id)
        print("PO Number      :", invoice.po_number)
        print("Currency       :", invoice.currency)
        print("Subtotal       :", invoice.subtotal)
        print("Tax            :", invoice.tax)
        print("Total          :", invoice.total)

        print()
        print("Line Items")
        print("-" * 60)

        for index, item in enumerate(invoice.line_items, start=1):
            print(f"{index}. {item.description}")
            print(f"   SKU         : {item.sku}")
            print(f"   Quantity    : {item.quantity}")
            print(f"   Unit Price  : {item.unit_price}")
            print(f"   Tax Rate    : {item.tax_rate_pct}")
            print(f"   Line Total  : {item.line_total}")

        print()
        print("Extraction Notes:")
        if invoice.extraction_notes:
            for note in invoice.extraction_notes:
                print(f"- {note}")
        else:
            print("- None")

        print()
        print("=" * 60)
        print("EXTRACTION TEST PASSED")
        print("=" * 60)

    except InvoiceExtractionError as exc:
        print()
        print("✗ EXTRACTION FAILED")
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()