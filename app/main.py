from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

import streamlit as st

from database import (
    delete_processing_history,
    get_processing_history,
    save_processing_result,
    update_human_decision,
)
from decision import review_invoice
from extraction import extract_invoice
from matching import InvoiceMatcher
from validation import InvoiceValidator


WORKBOOK_PATH = "data/PS1_AI_First_Production_Dataset.xlsx"
HISTORY_PDF_DIR = Path("outputs/processed_invoices")
HISTORY_PDF_DIR.mkdir(parents=True, exist_ok=True)


st.set_page_config(
    page_title="AI Invoice Processor",
    page_icon="📄",
    layout="wide",
)


# -----------------------------
# Session state
# -----------------------------

if "result" not in st.session_state:
    st.session_state.result = None

if "record_id" not in st.session_state:
    st.session_state.record_id = None

if "processed_filename" not in st.session_state:
    st.session_state.processed_filename = None

if "open_history_id" not in st.session_state:
    st.session_state.open_history_id = None

if "delete_confirm_id" not in st.session_state:
    st.session_state.delete_confirm_id = None

if "clear_history_confirm" not in st.session_state:
    st.session_state.clear_history_confirm = False


# -----------------------------
# Helper functions
# -----------------------------


def save_history_pdf(uploaded_file) -> str:
    """Save a copy of the processed PDF for later history access."""
    safe_name = Path(uploaded_file.name).stem.replace(" ", "_")
    unique_name = f"{safe_name}_{uuid.uuid4().hex[:8]}.pdf"
    destination = HISTORY_PDF_DIR / unique_name
    destination.write_bytes(uploaded_file.getvalue())
    return str(destination)


def parse_json(value, default=None):
    if not value:
        return default if default is not None else []
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default if default is not None else []


def show_status(label: str, status: str = "complete"):
    if status == "complete":
        st.success(f"✓ {label}")
    elif status == "running":
        st.info(f"⏳ {label}")
    else:
        st.error(f"✗ {label}")


def display_historical_invoice(record: dict):
    """Display one previously processed invoice from history."""

    if hasattr(record, "keys") and not isinstance(record, dict):
        record = dict(record)

    st.divider()
    st.subheader("Opened Invoice")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Invoice Number", record.get("invoice_number") or "Missing")
    col2.metric("PO Number", record.get("po_number") or "Missing")
    col3.metric("AI Recommendation", record.get("ai_recommendation") or "—")
    col4.metric("Human Decision", record.get("human_decision") or "Pending")

    st.markdown("### Human Review")

    st.write(
        "You can change the final decision for this historical invoice."
    )

    reviewer_note = st.text_area(
        "Reviewer note",
        value=record.get("reviewer_note") or "",
        placeholder="Optional explanation for the final decision...",
        key=f"historical_note_{record['id']}",
    )

    col1, col2 = st.columns(2)

    with col1:
        approve_clicked = st.button(
            "✅ Approve Invoice",
            key=f"historical_approve_{record['id']}",
            use_container_width=True,
        )

    with col2:
        reject_clicked = st.button(
            "❌ Reject Invoice",
            key=f"historical_reject_{record['id']}",
            use_container_width=True,
        )

    if approve_clicked:
        update_human_decision(
            record_id=record["id"],
            human_decision="APPROVE",
            reviewer_note=reviewer_note or None,
        )
        st.success("Human decision updated: **APPROVE**")
        st.rerun()

    if reject_clicked:
        update_human_decision(
            record_id=record["id"],
            human_decision="REJECT",
            reviewer_note=reviewer_note or None,
        )
        st.error("Human decision updated: **REJECT**")
        st.rerun()

    st.markdown(f"**Vendor:** {record.get('vendor_name') or 'Missing'}")
    st.markdown(f"**Vendor Tax ID:** {record.get('vendor_tax_id') or 'Missing'}")

    invoice_total = (
        record.get("invoice_total")
        if record.get("invoice_total") is not None
        else "Missing"
    )
    st.markdown(f"**Invoice Total:** {invoice_total}")

    if record.get("ai_explanation"):
        st.markdown("### AI Explanation")
        st.write(record["ai_explanation"])

    findings = parse_json(record.get("findings_json"), [])
    if findings:
        st.markdown("### Decision Findings")
        for finding in findings:
            if isinstance(finding, dict):
                severity = finding.get("severity", "INFO")
                finding_type = finding.get("type", "Finding")
                evidence = finding.get("evidence", "")
                message = f"**{finding_type}** — {evidence}"
                if severity == "REJECT":
                    st.error(message)
                elif severity == "REVIEW":
                    st.warning(message)
                else:
                    st.info(message)

    line_items = record.get("line_items")
    if isinstance(line_items, str):
        line_items = parse_json(line_items, [])

    if line_items:
        st.markdown("### Extracted Line Items")
        rows = []
        for line in line_items:
            if isinstance(line, dict):
                rows.append(
                    {
                        "Description": line.get("description"),
                        "SKU": line.get("sku"),
                        "Quantity": line.get("quantity"),
                        "Unit Price": line.get("unit_price"),
                        "Tax %": line.get("tax_rate_pct"),
                        "Subtotal": line.get("line_subtotal"),
                        "Tax": line.get("line_tax"),
                        "Total": line.get("line_total"),
                    }
                )
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)

    pdf_path = record.get("pdf_path")
    if pdf_path and Path(pdf_path).exists():
        st.markdown("### Invoice PDF")
        pdf_bytes = Path(pdf_path).read_bytes()
        st.download_button(
            "⬇️ Download Invoice PDF",
            data=pdf_bytes,
            file_name=Path(pdf_path).name,
            mime="application/pdf",
            key=f"download_pdf_{record['id']}",
        )


# -----------------------------
# Header
# -----------------------------

st.title("📄 AI Invoice Processor")
st.caption(
    "AI-assisted Accounts Payable invoice processing "
    "with human review and audit history."
)

st.divider()


# -----------------------------
# Upload
# -----------------------------

st.subheader("1. Upload Invoice")

uploaded_file = st.file_uploader(
    "Upload an invoice PDF",
    type=["pdf"],
)

process_clicked = False

if uploaded_file:
    st.caption(f"Selected: **{uploaded_file.name}**")
    process_clicked = st.button(
        "🚀 Process Invoice",
        type="primary",
        use_container_width=True,
    )


# -----------------------------
# Processing
# -----------------------------

if process_clicked and uploaded_file:
    temp_pdf_path = None

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
        temp_file.write(uploaded_file.getvalue())
        temp_pdf_path = temp_file.name

    try:
        st.subheader("2. Processing Status")
        progress_placeholder = st.empty()

        progress_placeholder.info("⏳ Extracting invoice data from PDF...")
        invoice = extract_invoice(temp_pdf_path)
        progress_placeholder.success("✓ Invoice data extracted")

        progress_placeholder.info(
            "⏳ Retrieving vendor, PO and previous invoice context..."
        )
        matcher = InvoiceMatcher(WORKBOOK_PATH)
        context = matcher.build_context(
            vendor_name=invoice.vendor_name,
            vendor_tax_id=invoice.vendor_tax_id,
            po_number=invoice.po_number,
            invoice_number=invoice.invoice_number,
        )
        progress_placeholder.success("✓ Matching context retrieved")

        progress_placeholder.info("⏳ Running deterministic validation...")

        processed_history = get_processing_history()

        validator = InvoiceValidator(
            processed_invoices=[dict(record) for record in processed_history]
        )

        validation = validator.validate(
            invoice,
            vendor_context=context.get("vendor"),
        )
        progress_placeholder.success("✓ Validation completed")


        progress_placeholder.info("⏳ AI reviewing invoice...")

        review = review_invoice(
            invoice=invoice,
            context=context,
            validation=validation,
        )

        progress_placeholder.success("✓ AI review completed")

        # Persist the PDF so it can be opened later from Processing History.
        history_pdf_path = save_history_pdf(uploaded_file)

        # Save initial processing result.
        record_id = save_processing_result(
            filename=uploaded_file.name,
            invoice_number=invoice.invoice_number,
            vendor_name=invoice.vendor_name,
            vendor_tax_id=invoice.vendor_tax_id,
            po_number=invoice.po_number,
            invoice_total=float(invoice.total) if invoice.total is not None else None,
            line_items=[
                line.model_dump(mode="json") for line in invoice.line_items
            ],
            ai_recommendation=review.recommendation.value,
            ai_explanation=review.explanation,
            findings=[
                finding.model_dump(mode="json") for finding in review.findings
            ],
            pdf_path=history_pdf_path,
        )

        st.session_state.result = {
            "invoice": invoice,
            "context": context,
            "validation": validation,
            "review": review,
        }
        st.session_state.record_id = record_id
        st.session_state.processed_filename = uploaded_file.name

        st.success("Invoice processing completed.")

    except Exception as exc:
        st.error(f"Processing failed: {exc}")

    finally:
        if temp_pdf_path:
            try:
                Path(temp_pdf_path).unlink(missing_ok=True)
            except Exception:
                pass


# -----------------------------
# Results
# -----------------------------

if st.session_state.result:
    result = st.session_state.result

    invoice = result["invoice"]
    context = result["context"]
    validation = result["validation"]
    review = result["review"]

    st.divider()
    st.subheader("3. AI Recommendation")

    recommendation = review.recommendation.value

    if recommendation == "APPROVE":
        st.success("### 🟢 APPROVE")
    elif recommendation == "REVIEW":
        st.warning("### 🟠 REVIEW")
    else:
        st.error("### 🔴 REJECT")

    st.write(review.explanation)

    st.subheader("Decision Findings")

    if review.findings:
        for finding in review.findings:
            severity = finding.severity.value

            if severity == "REJECT":
                st.error(f"**{finding.type}** — {finding.evidence}")
            elif severity == "REVIEW":
                st.warning(f"**{finding.type}** — {finding.evidence}")
            else:
                st.info(f"**{finding.type}** — {finding.evidence}")
    else:
        st.info("No additional findings.")

    # -------------------------
    # Invoice details
    # -------------------------

    st.subheader("4. Invoice Details")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Invoice Number", invoice.invoice_number or "Missing")
    col2.metric(
        "Invoice Date",
        invoice.invoice_date.isoformat() if invoice.invoice_date else "Missing",
    )
    col3.metric("PO Number", invoice.po_number or "Missing")
    col4.metric("Currency", invoice.currency or "Unknown")

    col1, col2, col3 = st.columns(3)
    col1.metric("Subtotal", str(invoice.subtotal or "Missing"))
    col2.metric("Tax", str(invoice.tax or "Missing"))
    col3.metric("Total", str(invoice.total or "Missing"))

    # -------------------------
    # Vendor / PO
    # -------------------------

    st.subheader("5. Vendor & Purchase Order")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Invoice Vendor**")
        st.write(invoice.vendor_name or "Missing")

        st.markdown("**Invoice Tax ID**")
        st.write(invoice.vendor_tax_id or "Missing")

        st.markdown("**Matched Vendor**")
        st.json(context.get("vendor"))

    with col2:
        st.markdown("**Purchase Order**")
        st.json(context.get("purchase_order"))

    # -------------------------
    # Line items
    # -------------------------

    st.subheader("6. Extracted Line Items")

    line_rows = []

    for line in invoice.line_items:
        line_rows.append(
            {
                "Description": line.description,
                "SKU": line.sku,
                "Quantity": str(line.quantity),
                "Unit Price": str(line.unit_price),
                "Tax %": (
                    str(line.tax_rate_pct)
                    if line.tax_rate_pct is not None
                    else "—"
                ),
                "Subtotal": str(line.line_subtotal),
                "Tax": str(line.line_tax),
                "Total": str(line.line_total),
            }
        )

    if line_rows:
        st.dataframe(
            line_rows,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No line items were extracted.")

    # -------------------------
    # Validation
    # -------------------------

    st.subheader("7. Validation")

    validation_data = {
        "Schema validation": validation.schema_validation.passed,
        "Arithmetic validation": validation.arithmetic_validation.passed,
        "Exact duplicate check": validation.exact_duplicate_check.passed,
    }

    for check_name, passed in validation_data.items():
        if passed:
            st.success(f"✓ {check_name}")
        else:
            st.error(f"✗ {check_name}")

        

    # -------------------------
    # Human review
    # -------------------------
 
    st.divider()
    st.subheader("8. Human Review")

    st.write(
        "The AI recommendation is not the final decision. "
        "A human reviewer must approve or reject the invoice."
    )

    reviewer_note = st.text_area(
        "Reviewer note",
        placeholder="Optional explanation for the final decision...",
    )

    col1, col2 = st.columns(2)

    with col1:
        approve_clicked = st.button(
            "✅ Approve Invoice",
            use_container_width=True,
        )

    with col2:
        reject_clicked = st.button(
            "❌ Reject Invoice",
            use_container_width=True,
        )

    if approve_clicked:
        update_human_decision(
            record_id=st.session_state.record_id,
            human_decision="APPROVE",
            reviewer_note=reviewer_note or None,
        )
        st.success("Human decision recorded: **APPROVE**")
        st.rerun()

    if reject_clicked:
        update_human_decision(
            record_id=st.session_state.record_id,
            human_decision="REJECT",
            reviewer_note=reviewer_note or None,
        )
        st.error("Human decision recorded: **REJECT**")
        st.rerun()


# -----------------------------
# Processing History
# -----------------------------

st.divider()

history_header_col, clear_col = st.columns([5, 1])

with history_header_col:
    st.subheader("9. Processing History")

with clear_col:
    if st.button("🗑 Clear History", use_container_width=True):
        st.session_state.clear_history_confirm = True

if st.session_state.clear_history_confirm:
    st.warning(
        "This will permanently delete all processing history and the stored "
        "invoice PDFs. This does not delete your PO, vendor, or test dataset."
    )

    confirm_col1, confirm_col2 = st.columns(2)

    with confirm_col1:
        if st.button("Cancel", key="cancel_clear_history", use_container_width=True):
            st.session_state.clear_history_confirm = False
            st.rerun()

    with confirm_col2:
        if st.button(
            "Yes, Clear All History",
            key="confirm_clear_history",
            type="primary",
            use_container_width=True,
        ):
            current_history = get_processing_history()

            for row in current_history:
                delete_processing_history(row["id"])

            st.session_state.clear_history_confirm = False
            st.session_state.open_history_id = None
            st.session_state.result = None
            st.session_state.record_id = None
            st.session_state.processed_filename = None

            st.success("Processing history cleared.")
            st.rerun()


history = get_processing_history()

if history:
    # Table header + action columns.
    header = st.columns(
        [0.5, 1.5, 1.0, 2.0, 1.0, 1.3, 1.2, 2.0, 0.8, 0.8]
    )

    headers = [
        "ID",
        "Processed",
        "Invoice",
        "Vendor",
        "PO",
        "AI Recommendation",
        "Human Decision",
        "Reviewer Note",
        "Open",
        "Delete",
    ]

    for column, label in zip(header, headers):
        column.markdown(f"**{label}**")

    for row in history:
        cols = st.columns(
            [0.5, 1.5, 1.0, 2.0, 1.0, 1.3, 1.2, 2.0, 0.8, 0.8]
        )

        cols[0].write(row["id"])
        cols[1].write(row["processed_at"])
        cols[2].write(row["invoice_number"] or "Missing")
        cols[3].write(row["vendor_name"] or "Missing")
        cols[4].write(row["po_number"] or "Missing")
        cols[5].write(row["ai_recommendation"] or "—")
        cols[6].write(row["human_decision"] or "Pending")
        cols[7].write(row["reviewer_note"] or "")

        if cols[8].button("📂", key=f"open_history_{row['id']}"):
            st.session_state.open_history_id = row["id"]
            st.session_state.delete_confirm_id = None
            st.rerun()

        if cols[9].button("🗑", key=f"delete_history_{row['id']}"):
            st.session_state.delete_confirm_id = row["id"]
            st.session_state.open_history_id = None
            st.rerun()

        if st.session_state.delete_confirm_id == row["id"]:
            st.warning(
                f"Delete invoice **{row['invoice_number'] or 'this record'}** "
                "from processing history?"
            )

            delete_col1, delete_col2 = st.columns(2)

            with delete_col1:
                if st.button(
                    "Cancel",
                    key=f"cancel_delete_{row['id']}",
                    use_container_width=True,
                ):
                    st.session_state.delete_confirm_id = None
                    st.rerun()

            with delete_col2:
                if st.button(
                    "Yes, Delete",
                    key=f"confirm_delete_{row['id']}",
                    type="primary",
                    use_container_width=True,
                ):
                    delete_processing_history(row["id"])
                    st.session_state.delete_confirm_id = None
                    st.success("History record deleted.")
                    st.rerun()
else:
    st.info("No processing history yet.")


# -----------------------------
# Open selected historical invoice
# -----------------------------

if st.session_state.open_history_id is not None:
    opened_record = next(
        (
            row
            for row in history
            if row["id"] == st.session_state.open_history_id
        ),
        None,
    )

    if opened_record:
        display_historical_invoice(opened_record)
    else:
        st.session_state.open_history_id = None
