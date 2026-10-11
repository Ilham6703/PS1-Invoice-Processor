import json
import os
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv
from openai import OpenAI


try:
    from .prompts.decision_prompt import DECISION_SYSTEM_PROMPT
    from .models import (
        AIReview,
        Decision,
        ExtractedInvoice,
        Finding,
        Severity,
        ValidationSummary,
    )
except ImportError:
    from prompts.decision_prompt import DECISION_SYSTEM_PROMPT
    from models import (
        AIReview,
        Decision,
        ExtractedInvoice,
        Finding,
        Severity,
        ValidationSummary,
    )


load_dotenv()


class DecisionError(Exception):
    """Raised when AI decision processing fails."""


def _decimal(value):
    """Safely convert a value to Decimal."""

    if value is None:
        return None

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _build_tolerance_check(
    invoice: ExtractedInvoice,
    context: dict,
) -> dict:
    """
    Deterministically evaluate invoice total against the PO tolerance.

    Important:
    A PO can be invoiced in multiple partial/split invoices.

    Therefore, when the invoice total is below the full PO total,
    the full-PO tolerance comparison is NOT used. The AI evaluates
    partial/split billing using PO lines and previous invoice context.

    For invoices above the PO total, the PO tolerance is evaluated
    deterministically.
    """

    po = context.get("purchase_order") or {}

    invoice_total = _decimal(invoice.total)
    po_total = _decimal(po.get("po_total"))
    tolerance_pct = _decimal(po.get("tolerance_pct"))

    if invoice_total is None:
        return {
            "status": "NOT_CHECKED",
            "message": "Invoice total is unavailable.",
        }

    if po_total is None:
        return {
            "status": "NOT_CHECKED",
            "message": "PO total is unavailable.",
        }

    if tolerance_pct is None:
        return {
            "status": "NOT_CHECKED",
            "message": "PO tolerance is unavailable.",
        }

    if po_total == 0:
        return {
            "status": "NOT_CHECKED",
            "message": "PO total is zero; tolerance cannot be evaluated.",
        }

    # --------------------------------------------------------
    # Partial / split invoice
    # --------------------------------------------------------
    #
    # A lower invoice total does not mean an error because
    # the PO may be billed across multiple invoices.
    #
    if invoice_total < po_total:
        return {
            "status": "NOT_APPLICABLE_PARTIAL_INVOICE",
            "invoice_total": str(invoice_total),
            "po_total": str(po_total),
            "tolerance_pct": str(tolerance_pct),
            "message": (
                "Invoice total is below the full PO total. "
                "Full-PO tolerance comparison is not applied because "
                "the invoice may represent partial or split billing."
            ),
        }

    # --------------------------------------------------------
    # Invoice equal to or greater than PO total
    # --------------------------------------------------------

    variance_pct = (
        abs(invoice_total - po_total)
        / abs(po_total)
        * Decimal("100")
    )

    within_tolerance = variance_pct <= tolerance_pct

    return {
        "status": "CHECKED",
        "invoice_total": str(invoice_total),
        "po_total": str(po_total),
        "tolerance_pct": str(tolerance_pct),
        "variance_pct": str(
            variance_pct.quantize(Decimal("0.01"))
        ),
        "within_tolerance": within_tolerance,
        "message": (
            "Invoice amount is within the PO tolerance."
            if within_tolerance
            else "Invoice amount is outside the PO tolerance."
        ),
    }


def review_invoice(
    invoice: ExtractedInvoice,
    context: dict,
    validation: ValidationSummary,
) -> AIReview:

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise DecisionError(
            "OPENAI_API_KEY is not configured."
        )

    client = OpenAI(api_key=api_key)

    tolerance_check = _build_tolerance_check(
        invoice,
        context,
    )

    evidence = {
        "invoice": invoice.model_dump(mode="json"),
        "matching_context": context,
        "validation": validation.model_dump(mode="json"),
        "deterministic_financial_check": {
            "tolerance": tolerance_check,
        },
    }

    user_prompt = f"""
Review the following invoice using ONLY the supplied evidence.

{json.dumps(evidence, indent=2, default=str)}

Return the required structured JSON decision.
"""

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            instructions=DECISION_SYSTEM_PROMPT,
            input=[
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "invoice_ai_review",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "recommendation": {
                                "type": "string",
                                "enum": [
                                    "APPROVE",
                                    "REVIEW",
                                    "REJECT",
                                ],
                            },
                            "findings": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {
                                            "type": "string"
                                        },
                                        "severity": {
                                            "type": "string",
                                            "enum": [
                                                "INFO",
                                                "REVIEW",
                                                "REJECT",
                                            ],
                                        },
                                        "evidence": {
                                            "type": "string"
                                        },
                                    },
                                    "required": [
                                        "type",
                                        "severity",
                                        "evidence",
                                    ],
                                    "additionalProperties": False,
                                },
                            },
                            "explanation": {
                                "type": "string"
                            },
                        },
                        "required": [
                            "recommendation",
                            "findings",
                            "explanation",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
        )

        if not response.output_text:
            raise DecisionError(
                "AI returned an empty response."
            )

        review = AIReview.model_validate_json(
            response.output_text
        )

    except Exception as exc:
        raise DecisionError(
            f"AI review failed: {exc}"
        ) from exc


    # Missing Tax ID is not a mismatch.
    if not invoice.vendor_tax_id:
        review.findings = [
            finding
            for finding in review.findings
            if finding.type != "TAX_ID_MISMATCH"
        ]

    # ========================================================
    # DETERMINISTIC OVERRIDES
    # ========================================================

    # --------------------------------------------------------
    # Exact duplicate
    # --------------------------------------------------------

    if validation.exact_duplicate_check.passed is False:
        review.recommendation = Decision.REJECT

        review.findings = [
            finding
            for finding in review.findings
            if finding.type != "EXACT_DUPLICATE"
        ]

        review.findings.append(
            Finding(
                type="EXACT_DUPLICATE",
                severity=Severity.REJECT,
                evidence=(
                    "Python validation identified this invoice "
                    "as an exact duplicate."
                ),
            )
        )

    # --------------------------------------------------------
    # Tax ID mismatch
    # --------------------------------------------------------

    if validation.tax_id_validation.passed is False:
        review.recommendation = Decision.REJECT

        review.findings = [
            finding
            for finding in review.findings
            if finding.type != "TAX_ID_MISMATCH"
        ]

        review.findings.append(
            Finding(
                type="TAX_ID_MISMATCH",
                severity=Severity.REJECT,
                evidence=validation.tax_id_validation.message,
            )
        )

    # --------------------------------------------------------
    # Schema / arithmetic failures
    # --------------------------------------------------------

    if (
        validation.schema_validation.passed is False
        or validation.arithmetic_validation.passed is False
    ):
        if review.recommendation == Decision.APPROVE:
            review.recommendation = Decision.REVIEW

    # --------------------------------------------------------
    # Deterministic tolerance check
    # --------------------------------------------------------

    if tolerance_check["status"] == "CHECKED":

        # Within tolerance = remove any hallucinated
        # AMOUNT_OUTSIDE_TOLERANCE finding.
        if tolerance_check["within_tolerance"] is True:

            review.findings = [
                finding
                for finding in review.findings
                if finding.type != "AMOUNT_OUTSIDE_TOLERANCE"
            ]

        else:
            # Outside tolerance is a REVIEW condition unless
            # a stronger REJECT condition already exists.
            if review.recommendation == Decision.APPROVE:
                review.recommendation = Decision.REVIEW

            review.findings = [
                finding
                for finding in review.findings
                if finding.type != "AMOUNT_OUTSIDE_TOLERANCE"
            ]

            review.findings.append(
                Finding(
                    type="AMOUNT_OUTSIDE_TOLERANCE",
                    severity=Severity.REVIEW,
                    evidence=(
                        "Invoice amount is outside the PO tolerance. "
                        f"Invoice total: "
                        f"{tolerance_check['invoice_total']}; "
                        f"PO total: "
                        f"{tolerance_check['po_total']}; "
                        f"variance: "
                        f"{tolerance_check['variance_pct']}%; "
                        f"allowed tolerance: "
                        f"{tolerance_check['tolerance_pct']}%."
                    ),
                )
            )


    # --------------------------------------------------------
    # Previous approved invoice over-billing
    # --------------------------------------------------------

    previous_invoices = context.get("previous_approved_invoices", [])
    po = context.get("purchase_order") or {}

    current_total = _decimal(invoice.total)
    po_total = _decimal(po.get("po_total"))

    if current_total is not None and po_total is not None:
        for previous in previous_invoices:
            previous_total = _decimal(
                previous.get("invoice_total")
            )

            previous_po = str(
                previous.get("po_number", "")
            ).strip().upper()

            current_po = str(
                invoice.po_number or ""
            ).strip().upper()

            if (
                previous_total is not None
                and previous_po == current_po
                and previous_total + current_total > po_total
            ):
                review.recommendation = Decision.REVIEW

                review.findings = [
                    finding
                    for finding in review.findings
                    if finding.type != "OVER_BILLING"
                ]

                review.findings.append(
                    Finding(
                        type="OVER_BILLING",
                        severity=Severity.REVIEW,
                        evidence=(
                            f"Previous approved invoice "
                            f"{previous.get('invoice_number')} already billed "
                            f"{previous_total}; current invoice bills "
                            f"{current_total}. Combined billed amount is "
                            f"{previous_total + current_total}, exceeding "
                            f"the PO total of {po_total}."
                        ),
                    )
                )
                break

    # --------------------------------------------------------
    # Final deterministic priority
    # --------------------------------------------------------

    if validation.exact_duplicate_check.passed is False:
        review.recommendation = Decision.REJECT

    elif validation.tax_id_validation.passed is False:
        review.recommendation = Decision.REJECT

    elif (
        validation.schema_validation.passed is False
        or validation.arithmetic_validation.passed is False
    ):
        if review.recommendation == Decision.APPROVE:
            review.recommendation = Decision.REVIEW

    return review