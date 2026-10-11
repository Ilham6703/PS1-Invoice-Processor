from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError


try:
    from .models import (
        ExtractedInvoice,
        ValidationResult,
        ValidationSummary,
    )
except ImportError:
    from models import (
        ExtractedInvoice,
        ValidationResult,
        ValidationSummary,
    )


class ValidationError(Exception):
    """Raised when deterministic invoice validation cannot be completed."""


class InvoiceValidator:
    """
    Small deterministic safety layer.

    This class does NOT decide whether an invoice should be approved.
    It only identifies objective validation problems for the AI review
    and final decision stage.
    """

    def __init__(
        self,
        processed_invoices: list[dict[str, Any]] | None = None,
    ):
        self.processed_invoices = processed_invoices or []

    # ------------------------------------------------------------
    # 1. SCHEMA VALIDATION
    # ------------------------------------------------------------

    @staticmethod
    def validate_schema(
        invoice: ExtractedInvoice,
    ) -> ValidationResult:
        """
        The invoice has already passed Pydantic validation if it reached
        this method. This check exists as an explicit guardrail for the
        processing pipeline.
        """

        try:
            ExtractedInvoice.model_validate(
                invoice.model_dump()
            )

            return ValidationResult(
                name="schema_validation",
                passed=True,
                message="Extracted invoice matches the required schema.",
            )

        except ValidationError as exc:
            return ValidationResult(
                name="schema_validation",
                passed=False,
                message=f"Invoice schema validation failed: {exc}",
            )

    # ------------------------------------------------------------
    # 2. ARITHMETIC VALIDATION
    # ------------------------------------------------------------

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        """Safely convert a value to Decimal."""

        if value is None:
            return None

        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _close_enough(
        first: Decimal,
        second: Decimal,
        tolerance: Decimal = Decimal("1.00"),
    ) -> bool:
        """
        Allow small currency rounding differences.

        The assessment dataset uses ₹1 as the rounding tolerance.
        """

        return abs(first - second) <= tolerance

    def validate_arithmetic(
        self,
        invoice: ExtractedInvoice,
    ) -> ValidationResult:
        """
        Validate basic invoice arithmetic.

        Checks:

        - line subtotal ≈ quantity × unit price
        - line total ≈ line subtotal + line tax
        - subtotal ≈ sum of line subtotals
        - tax ≈ sum of line taxes
        - total ≈ subtotal + tax

        Missing values are not treated as arithmetic failures because
        missing information should be handled by the AI review stage.
        """

        problems: list[str] = []

        # ---- Line-level checks ----

        line_subtotals: list[Decimal] = []
        line_taxes: list[Decimal] = []
        line_totals: list[Decimal] = []

        for index, line in enumerate(invoice.line_items, start=1):

            quantity = self._decimal(line.quantity)
            unit_price = self._decimal(line.unit_price)
            subtotal = self._decimal(line.line_subtotal)
            tax = self._decimal(line.line_tax)
            total = self._decimal(line.line_total)

            if (
                quantity is not None
                and unit_price is not None
                and subtotal is not None
            ):
                expected_subtotal = quantity * unit_price

                if not self._close_enough(
                    expected_subtotal,
                    subtotal,
                ):
                    problems.append(
                        f"Line {index}: quantity × unit price "
                        f"does not match line subtotal."
                    )

            if (
                subtotal is not None
                and tax is not None
                and total is not None
            ):
                expected_total = subtotal + tax

                if not self._close_enough(
                    expected_total,
                    total,
                ):
                    problems.append(
                        f"Line {index}: subtotal + tax "
                        f"does not match line total."
                    )

            if subtotal is not None:
                line_subtotals.append(subtotal)

            if tax is not None:
                line_taxes.append(tax)

            if total is not None:
                line_totals.append(total)

        # ---- Invoice-level checks ----

        invoice_subtotal = self._decimal(invoice.subtotal)
        invoice_tax = self._decimal(invoice.tax)
        invoice_total = self._decimal(invoice.total)

        if invoice_subtotal is not None and line_subtotals:
            calculated_subtotal = sum(
                line_subtotals,
                Decimal("0"),
            )

            if not self._close_enough(
                calculated_subtotal,
                invoice_subtotal,
            ):
                problems.append(
                    "Sum of line subtotals does not match "
                    "invoice subtotal."
                )

        if invoice_tax is not None and line_taxes:
            calculated_tax = sum(
                line_taxes,
                Decimal("0"),
            )

            if not self._close_enough(
                calculated_tax,
                invoice_tax,
            ):
                problems.append(
                    "Sum of line taxes does not match invoice tax."
                )

        if (
            invoice_subtotal is not None
            and invoice_tax is not None
            and invoice_total is not None
        ):
            expected_total = invoice_subtotal + invoice_tax

            if not self._close_enough(
                expected_total,
                invoice_total,
            ):
                problems.append(
                    "Invoice subtotal + tax does not match "
                    "invoice total."
                )

        if problems:
            return ValidationResult(
                name="arithmetic_validation",
                passed=False,
                message="Arithmetic mismatch: " + " ".join(problems),
            )

        return ValidationResult(
            name="arithmetic_validation",
            passed=True,
            message="Invoice arithmetic is internally consistent.",
        )

    # ------------------------------------------------------------
    # 3. EXACT DUPLICATE DETECTION
    # ------------------------------------------------------------

    @staticmethod
    def _normalize(value: Any) -> str:
        """Normalize text for exact duplicate comparison."""

        if value is None:
            return ""

        return " ".join(
            str(value)
            .strip()
            .upper()
            .split()
        )

    def check_exact_duplicate(
        self,
        invoice: ExtractedInvoice,
    ) -> ValidationResult:
        """
        Detect an exact duplicate using:

            vendor identity + normalized invoice number

        File hash is deliberately NOT used because the same invoice can
        be uploaded as a different PDF file.
        """

        invoice_number = self._normalize(
            invoice.invoice_number
        )

        vendor_identity = self._normalize(
            invoice.vendor_tax_id or invoice.vendor_name
        )

        if not invoice_number or not vendor_identity:
            return ValidationResult(
                name="exact_duplicate_check",
                passed=True,
                message=(
                    "Exact duplicate check could not establish a "
                    "complete duplicate key; no duplicate identified."
                ),
            )

        for previous in self.processed_invoices:

            previous_invoice_number = self._normalize(
                previous.get("invoice_number")
            )

            previous_vendor_identity = self._normalize(
                previous.get("vendor_tax_id")
                or previous.get("vendor_name")
            )

            if (
                invoice_number == previous_invoice_number
                and vendor_identity == previous_vendor_identity
            ):
                return ValidationResult(
                    name="exact_duplicate_check",
                    passed=False,
                    message=(
                        "Exact duplicate detected: same vendor identity "
                        "and invoice number already processed."
                    ),
                )

        return ValidationResult(
            name="exact_duplicate_check",
            passed=True,
            message="No exact duplicate was detected.",
        )

    # ------------------------------------------------------------
    # 4. TAX ID VALIDATION
    # ------------------------------------------------------------

    @staticmethod
    def validate_tax_id(
        invoice: ExtractedInvoice,
        vendor_context: dict[str, Any] | None = None,
    ) -> ValidationResult:
        """
        Deterministic Tax ID validation.

        Important:
        - Missing invoice Tax ID is NOT a mismatch.
        - Present matching Tax ID passes.
        - Present conflicting Tax ID fails.
        - If vendor master Tax ID is unavailable, no mismatch is declared.
        """

        invoice_tax_id = InvoiceValidator._normalize(
            invoice.vendor_tax_id
        )

        vendor_tax_id = ""

        if vendor_context:
            vendor_tax_id = InvoiceValidator._normalize(
                vendor_context.get("tax_id")
            )

        # Missing Tax ID is not an error.
        if not invoice_tax_id:
            return ValidationResult(
                name="tax_id_validation",
                passed=True,
                message=(
                    "Invoice Tax ID is missing; this is not treated "
                    "as a Tax ID mismatch."
                ),
            )

        # No vendor master Tax ID available.
        if not vendor_tax_id:
            return ValidationResult(
                name="tax_id_validation",
                passed=True,
                message=(
                    "Vendor master Tax ID is unavailable; "
                    "no Tax ID mismatch was established."
                ),
            )

        # Exact match.
        if invoice_tax_id == vendor_tax_id:
            return ValidationResult(
                name="tax_id_validation",
                passed=True,
                message="Invoice Tax ID matches the vendor master.",
            )

        # Clear mismatch.
        return ValidationResult(
            name="tax_id_validation",
            passed=False,
            message=(
                "Invoice Tax ID does not match the vendor master "
                f"Tax ID. Invoice: {invoice_tax_id}; "
                f"Vendor master: {vendor_tax_id}."
            ),
        )

    # ------------------------------------------------------------
    # RUN ALL GUARDRAILS
    # ------------------------------------------------------------

    def validate(
        self,
        invoice: ExtractedInvoice,
        vendor_context: dict[str, Any] | None = None,
    ) -> ValidationSummary:
        """
        Run all deterministic guardrails.

        vendor_context is optional so the existing application call:

            validator.validate(invoice)

        continues to work without modification.
        """

        schema_result = self.validate_schema(invoice)

        arithmetic_result = self.validate_arithmetic(invoice)

        duplicate_result = self.check_exact_duplicate(invoice)

        tax_id_result = self.validate_tax_id(
            invoice,
            vendor_context=vendor_context,
        )

        return ValidationSummary(
            schema_validation=schema_result,
            arithmetic_validation=arithmetic_result,
            exact_duplicate_check=duplicate_result,
            tax_id_validation=tax_id_result,
        )