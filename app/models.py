from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ============================================================
# ENUMS
# ============================================================

class Decision(str, Enum):
    APPROVE = "APPROVE"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


class Severity(str, Enum):
    INFO = "INFO"
    REVIEW = "REVIEW"
    REJECT = "REJECT"


# ============================================================
# INVOICE EXTRACTION
# ============================================================

class InvoiceLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    sku: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    tax_rate_pct: Optional[Decimal] = None
    line_subtotal: Optional[Decimal] = None
    line_tax: Optional[Decimal] = None
    line_total: Optional[Decimal] = None


class ExtractedInvoice(BaseModel):
    """
    Structured representation produced by the Vision LLM.
    Missing information should remain None rather than being invented.
    """

    model_config = ConfigDict(extra="forbid")

    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None

    vendor_name: Optional[str] = None
    vendor_tax_id: Optional[str] = None

    po_number: Optional[str] = None
    currency: Optional[str] = None

    line_items: list[InvoiceLineItem] = Field(default_factory=list)

    subtotal: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    total: Optional[Decimal] = None

    extraction_notes: list[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip().upper()


# ============================================================
# VALIDATION
# ============================================================

class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    message: str


class ValidationSummary(BaseModel):
    """
    Results from the small deterministic Python guardrails.

    The first three checks are performed directly by InvoiceValidator.

    tax_id_validation is also deterministic and is based only on the
    extracted invoice information. It is kept separate from the AI
    decision so that a missing Tax ID is never confused with a mismatch.
    """

    model_config = ConfigDict(extra="forbid")

    schema_validation: ValidationResult
    arithmetic_validation: ValidationResult
    exact_duplicate_check: ValidationResult
    tax_id_validation: ValidationResult

    @property
    def has_failure(self) -> bool:
        return not all(
            [
                self.schema_validation.passed,
                self.arithmetic_validation.passed,
                self.exact_duplicate_check.passed,
                self.tax_id_validation.passed,
            ]
        )


# ============================================================
# AI FINDINGS
# ============================================================

class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    severity: Severity
    evidence: str = Field(min_length=1)


class AIReview(BaseModel):
    """
    Structured output expected from the Review LLM.
    """

    model_config = ConfigDict(extra="forbid")

    recommendation: Decision
    findings: list[Finding] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


# ============================================================
# FINAL HUMAN DECISION
# ============================================================

class HumanDecision(BaseModel):
    """
    Human reviewer action after seeing the AI recommendation.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Optional[Decision] = None
    reviewer_note: Optional[str] = None
    decided_at: Optional[datetime] = None


# ============================================================
# PROCESSING RESULT
# ============================================================

class InvoiceProcessingResult(BaseModel):
    """
    Complete result displayed by the UI and stored in history.
    """

    model_config = ConfigDict(extra="forbid")

    invoice_filename: str

    extracted_invoice: ExtractedInvoice

    validation: ValidationSummary

    ai_review: AIReview

    human_decision: HumanDecision = Field(
        default_factory=HumanDecision
    )

    processed_at: datetime = Field(
        default_factory=datetime.now
    )

    processing_status: str = "COMPLETED"

    @property
    def final_decision(self) -> Optional[Decision]:
        """
        Human decision takes precedence once provided.
        Otherwise show the AI recommendation.
        """
        if self.human_decision.decision is not None:
            return self.human_decision.decision

        return self.ai_review.recommendation