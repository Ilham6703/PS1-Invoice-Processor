from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

try:
    from .database import get_previous_approved_invoices
except ImportError:
    from database import get_previous_approved_invoices
class MatchingError(Exception):
    """Raised when invoice context cannot be retrieved safely."""


class InvoiceMatcher:
    """
    Retrieves PO, PO-line, vendor, and prior approved invoice context
    from the case-study Excel dataset.
    """

    def __init__(self, workbook_path: str | Path):
        self.workbook_path = Path(workbook_path)

        if not self.workbook_path.exists():
            raise MatchingError(
                f"Dataset not found: {self.workbook_path}"
            )

        try:
            self.pos = pd.read_excel(
                self.workbook_path,
                sheet_name="POs",
            )

            self.po_lines = pd.read_excel(
                self.workbook_path,
                sheet_name="PO_Lines",
            )

            self.vendors = pd.read_excel(
                self.workbook_path,
                sheet_name="Vendors",
            )

            self.test_matrix = pd.read_excel(
                self.workbook_path,
                sheet_name="Test_Matrix",
            )

        except Exception as exc:
            raise MatchingError(
                f"Could not load the dataset: {exc}"
            ) from exc

        self._validate_dataset()

    def _validate_dataset(self) -> None:
        """Check that the required workbook columns exist."""

        required_columns = {
            "POs": {
                "po_number",
                "vendor_id",
                "vendor_name",
                "currency",
                "tolerance_pct",
            },
            "PO_Lines": {
                "po_number",
                "line_id",
                "description",
                "sku",
                "quantity",
                "unit_price",
                "tax_rate_pct",
                "line_subtotal",
                "line_tax",
                "line_total",
            },
            "Vendors": {
                "vendor_id",
                "vendor_name",
                "tax_id",
            },
        }

        sheets = {
            "POs": self.pos,
            "PO_Lines": self.po_lines,
            "Vendors": self.vendors,
        }

        for sheet_name, required in required_columns.items():
            missing = required - set(sheets[sheet_name].columns)

            if missing:
                raise MatchingError(
                    f"{sheet_name} is missing columns: "
                    f"{sorted(missing)}"
                )

    @staticmethod
    def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
        """Convert DataFrame rows into JSON-friendly dictionaries."""

        records = df.to_dict(orient="records")

        cleaned = []

        for record in records:
            cleaned_record = {}

            for key, value in record.items():
                if pd.isna(value):
                    cleaned_record[key] = None
                else:
                    # Convert NumPy/Pandas scalar values to normal
                    # Python values where possible.
                    if hasattr(value, "item"):
                        try:
                            value = value.item()
                        except (ValueError, TypeError):
                            pass

                    cleaned_record[key] = value

            cleaned.append(cleaned_record)

        return cleaned

    def find_vendor(
        self,
        vendor_name: str | None = None,
        vendor_tax_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Find the vendor using the strongest available identity.

        Exact tax-ID match is preferred. Vendor-name matching is
        case-insensitive and whitespace-normalized.
        """

        if vendor_tax_id:
            tax_id = str(vendor_tax_id).strip().upper()

            matches = self.vendors[
                self.vendors["tax_id"]
                .astype(str)
                .str.strip()
                .str.upper()
                == tax_id
            ]

            if not matches.empty:
                return self._records(matches)[0]

        if vendor_name:
            normalized_name = " ".join(
                str(vendor_name).strip().lower().split()
            )

            names = (
                self.vendors["vendor_name"]
                .astype(str)
                .map(
                    lambda value: " ".join(
                        value.strip().lower().split()
                    )
                )
            )

            matches = self.vendors[names == normalized_name]

            if not matches.empty:
                return self._records(matches)[0]

        return None

    def find_po(
        self,
        po_number: str | None,
    ) -> dict[str, Any] | None:
        """Retrieve a PO by its exact PO number."""

        if not po_number:
            return None

        normalized_po = str(po_number).strip().upper()

        matches = self.pos[
            self.pos["po_number"]
            .astype(str)
            .str.strip()
            .str.upper()
            == normalized_po
        ]

        if matches.empty:
            return None

        return self._records(matches)[0]

    def find_po_lines(
        self,
        po_number: str | None,
    ) -> list[dict[str, Any]]:
        """Retrieve all line items belonging to a PO."""

        if not po_number:
            return []

        normalized_po = str(po_number).strip().upper()

        matches = self.po_lines[
            self.po_lines["po_number"]
            .astype(str)
            .str.strip()
            .str.upper()
            == normalized_po
        ]

        return self._records(matches)

    def find_previous_approved_invoices(
        self,
        vendor_name: str | None = None,
        po_number: str | None = None,
        current_invoice_number: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve previously approved invoices represented in the
        Test_Matrix.

        For the case-study dataset, invoice numbers are used to
        preserve processing order. Only approved invoices with a
        lower invoice number than the current invoice are treated
        as previous invoices.

        In the full application, runtime SQLite history can later
        provide the actual processing history.
        """

        if self.test_matrix.empty:
            return []

        df = self.test_matrix.copy()

        # Only approved invoices.
        if "expected_decision" in df.columns:
            df = df[
                df["expected_decision"]
                .astype(str)
                .str.upper()
                == "APPROVE"
            ]

        # Match the same PO.
        if po_number and "expected_po" in df.columns:
            normalized_po = str(po_number).strip().upper()

            df = df[
                df["expected_po"]
                .fillna("")
                .astype(str)
                .str.strip()
                .str.upper()
                == normalized_po
            ]

        # Exclude the current invoice and any later invoice.
        if current_invoice_number and "invoice_number" in df.columns:

            current_number = str(
                current_invoice_number
            ).strip().upper()

            def invoice_sequence(value: Any) -> int:
                text = str(value).strip().upper()

                try:
                    return int(text.replace("INV-", ""))
                except ValueError:
                    return -1

            current_sequence = invoice_sequence(current_number)

            df = df[
                df["invoice_number"].apply(invoice_sequence)
                < current_sequence
            ]

        return self._records(df)
        

    def build_context(
        self,
        vendor_name: str | None = None,
        vendor_tax_id: str | None = None,
        po_number: str | None = None,
        invoice_number: str | None = None,
    ) -> dict[str, Any]:
        """
        Build all structured business context required by the
        Review LLM.
        """

        vendor = self.find_vendor(
            vendor_name=vendor_name,
            vendor_tax_id=vendor_tax_id,
        )

        po = self.find_po(po_number)

        po_lines = self.find_po_lines(po_number)

        previous_invoices = self.find_previous_approved_invoices(
            vendor_name=vendor_name,
            po_number=po_number,
            current_invoice_number=invoice_number,
        )

        # Add actual runtime-approved invoices when available.
        runtime_previous = get_previous_approved_invoices(
            vendor_name=vendor_name,
            po_number=po_number,
            current_invoice_number=invoice_number,
        )

        # Avoid duplicate records.
        # Prefer actual runtime-approved invoices over Test_Matrix metadata.
        runtime_by_number = {
            str(item.get("invoice_number", "")).strip().upper(): item
            for item in runtime_previous
            if item.get("invoice_number")
        }

        merged_previous = []

        for item in previous_invoices:
            number = str(item.get("invoice_number", "")).strip().upper()

            if number in runtime_by_number:
                # Use the real processed invoice from SQLite.
                merged_previous.append(runtime_by_number[number])
            else:
                # Keep Test_Matrix metadata when no runtime invoice exists.
                merged_previous.append(item)

        previous_invoices = merged_previous

        return {
            "vendor": vendor,
            "purchase_order": po,
            "purchase_order_lines": po_lines,
            "previous_approved_invoices": previous_invoices,
        }