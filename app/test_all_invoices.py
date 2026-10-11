from pathlib import Path

from app.extraction import extract_invoice
from app.matching import InvoiceMatcher
from app.validation import InvoiceValidator
from app.decision import review_invoice


WORKBOOK_PATH = "data/PS1_AI_First_Production_Dataset.xlsx"
INVOICE_DIR = Path("invoices")


EXPECTED_DECISIONS = {
    "INV-2001.pdf": "APPROVE",
    "INV-2002.pdf": "APPROVE",
    "INV-2003.pdf": "APPROVE",
    "INV-2004.pdf": "REVIEW",
    "INV-2005.pdf": "APPROVE",
    "INV-2006.pdf": "APPROVE",
    "INV-2007.pdf": "REVIEW",
    "INV-2008.pdf": "REVIEW",
    "INV-2009.pdf": "REVIEW",
    "INV-2010.pdf": "REJECT",
    "INV-2011.pdf": "REVIEW",
    "INV-2012.pdf": "REVIEW",
    "INV-2013.pdf": "APPROVE",
    "INV-20014.pdf": "REJECT",
}


def main():
    matcher = InvoiceMatcher(WORKBOOK_PATH)

    invoice_files = sorted(
        INVOICE_DIR.glob("INV-*.pdf"),
        key=lambda p: int(p.stem.replace("INV-", "")),
    )

    print("\n" + "=" * 90)
    print("AI INVOICE PROCESSOR — FULL SYSTEM TEST")
    print("=" * 90)

    results = []

    # Test-only history. Does not modify the production SQLite database.
    approved_history = []

    for invoice_path in invoice_files:

        print("\n" + "-" * 90)
        print(f"PROCESSING: {invoice_path.name}")
        print("-" * 90)

        expected_decision = EXPECTED_DECISIONS.get(invoice_path.name)

        try:
            # -------------------------------------------------
            # 1. Extraction
            # -------------------------------------------------
            invoice = extract_invoice(str(invoice_path))

            # -------------------------------------------------
            # 2. Matching
            # -------------------------------------------------
            context = matcher.build_context(
                vendor_name=invoice.vendor_name,
                vendor_tax_id=invoice.vendor_tax_id,
                po_number=invoice.po_number,
                invoice_number=invoice.invoice_number,
            )

            # Prefer actual extracted records from this test run
            # over lightweight Test_Matrix history records.
            existing_history = context.get(
                "previous_approved_invoices", []
            )

            runtime_by_number = {
                str(item.get("invoice_number", "")).strip().upper(): item
                for item in approved_history
                if item.get("invoice_number")
            }

            merged_history = []

            for item in existing_history:
                number = str(
                    item.get("invoice_number", "")
                ).strip().upper()

                if number in runtime_by_number:
                    merged_history.append(runtime_by_number[number])
                else:
                    merged_history.append(item)

            existing_numbers = {
                str(item.get("invoice_number", "")).strip().upper()
                for item in merged_history
            }

            for item in approved_history:
                number = str(
                    item.get("invoice_number", "")
                ).strip().upper()

                if number and number not in existing_numbers:
                    merged_history.append(item)
                    existing_numbers.add(number)

            context["previous_approved_invoices"] = merged_history

            # -------------------------------------------------
            # 3. Validation
            # -------------------------------------------------
            validator = InvoiceValidator(
                processed_invoices=approved_history
            )

            validation = validator.validate(
                invoice,
                vendor_context=context.get("vendor"),
            )

            # -------------------------------------------------
            # 4. AI review
            # -------------------------------------------------
            review = review_invoice(
                invoice=invoice,
                context=context,
                validation=validation,
            )

            recommendation = review.recommendation.value

            print(f"File          : {invoice_path.name}")
            print(f"Invoice       : {invoice.invoice_number}")
            print(f"Vendor        : {invoice.vendor_name}")
            print(f"PO            : {invoice.po_number or 'None'}")
            print(f"Recommendation: {recommendation}")
            print(f"Expected      : {expected_decision or 'NOT CONFIGURED'}")

            print("\nFindings:")

            for finding in review.findings:
                print(
                    f"  [{finding.severity.value}] "
                    f"{finding.type}: "
                    f"{finding.evidence}"
                )

            print("\nExplanation:")
            print(review.explanation)

            # -------------------------------------------------
            # 5. Test-only approved history
            # -------------------------------------------------
            # Use the expected decision to simulate the approved
            # invoices defined by the test scenarios.
            # Do not write to the production database.
            if expected_decision == "APPROVE":
                approved_history.append(
                    {
                        "invoice_number": invoice.invoice_number,
                        "vendor_name": invoice.vendor_name,
                        "po_number": invoice.po_number,
                        "vendor_tax_id": invoice.vendor_tax_id,
                        "invoice_total": (
                            float(invoice.total)
                            if invoice.total is not None
                            else None
                        ),
                        "line_items": [
                            item.model_dump(mode="json")
                            for item in invoice.line_items
                        ],
                        "filename": invoice_path.name,
                    }
                )

            # -------------------------------------------------
            # 6. Compare actual and expected decisions
            # -------------------------------------------------
            if expected_decision is None:
                status = "UNCONFIGURED"
            elif recommendation == expected_decision:
                status = "PASS"
            else:
                status = "FAIL"

            results.append(
                {
                    "invoice": invoice_path.name,
                    "recommendation": recommendation,
                    "expected": expected_decision or "NOT SET",
                    "status": status,
                }
            )

        except Exception as exc:
            print(f"ERROR: {exc}")

            results.append(
                {
                    "invoice": invoice_path.name,
                    "recommendation": "ERROR",
                    "expected": expected_decision or "NOT SET",
                    "status": "FAIL",
                }
            )

    # -------------------------------------------------
    # Final summary
    # -------------------------------------------------
    print("\n\n" + "=" * 90)
    print("FINAL TEST SUMMARY")
    print("=" * 90)

    print(
        f"{'File':<18}"
        f"{'Actual':<12}"
        f"{'Expected':<12}"
        f"{'Status':<12}"
    )

    print("-" * 54)

    for result in results:
        print(
            f"{result['invoice']:<18}"
            f"{result['recommendation']:<12}"
            f"{result['expected']:<12}"
            f"{result['status']:<12}"
        )

    passed = sum(
        result["status"] == "PASS"
        for result in results
    )

    failed = sum(
        result["status"] == "FAIL"
        for result in results
    )

    unconfigured = sum(
        result["status"] == "UNCONFIGURED"
        for result in results
    )

    print("-" * 54)
    print(f"Passed      : {passed}/{len(results)}")
    print(f"Failed      : {failed}/{len(results)}")
    print(f"Unconfigured: {unconfigured}")
    print("=" * 90)


if __name__ == "__main__":
    main()