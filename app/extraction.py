from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pymupdf  # PyMuPDF
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

try:
    from .models import ExtractedInvoice
    from .prompts.extraction_prompt import EXTRACTION_SYSTEM_PROMPT
except ImportError:
    from models import ExtractedInvoice
    from prompts.extraction_prompt import EXTRACTION_SYSTEM_PROMPT

load_dotenv()


class InvoiceExtractionError(Exception):
    """Raised when invoice extraction cannot be completed safely."""


def _get_openai_client() -> OpenAI:
    """Create the OpenAI client using the environment API key."""

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise InvoiceExtractionError(
            "OPENAI_API_KEY is not configured."
        )

    return OpenAI(api_key=api_key)


def _render_pdf_pages(pdf_path: str | Path) -> list[bytes]:
    """
    Render every PDF page into PNG bytes.

    Vision processing works from the rendered page images, which also
    handles scanned/image-based invoices.
    """

    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise InvoiceExtractionError(
            f"Invoice file not found: {pdf_path}"
        )

    if pdf_path.suffix.lower() != ".pdf":
        raise InvoiceExtractionError(
            "Only PDF invoice files are supported."
        )

    try:
        document = pymupdf.open(pdf_path)

        if len(document) == 0:
            document.close()
            raise InvoiceExtractionError(
                "The invoice PDF contains no pages."
            )

        pages: list[bytes] = []

        for page in document:
            # 150 DPI is sufficient for invoice text while keeping
            # image size reasonable.
            matrix = pymupdf.Matrix(150 / 72, 150 / 72)
            pixmap = page.get_pixmap(
                matrix=matrix,
                alpha=False
            )

            pages.append(pixmap.tobytes("png"))

        document.close()

        return pages

    except InvoiceExtractionError:
        raise

    except Exception as exc:
        raise InvoiceExtractionError(
            f"Could not read the invoice PDF: {exc}"
        ) from exc


def _image_to_data_url(image_bytes: bytes) -> str:
    """Convert PNG bytes into a data URL for the OpenAI API."""

    encoded = base64.b64encode(image_bytes).decode("utf-8")

    return f"data:image/png;base64,{encoded}"


def _extract_json(response) -> dict:
    """
    Extract JSON from the model response.

    Structured output is requested from the API, but this function still
    validates the returned content before using it.
    """

    try:
        text = response.output_text.strip()

        if not text:
            raise InvoiceExtractionError(
                "The Vision model returned an empty response."
            )

        return json.loads(text)

    except json.JSONDecodeError as exc:
        raise InvoiceExtractionError(
            "The Vision model returned invalid JSON."
        ) from exc


def extract_invoice(pdf_path: str | Path) -> ExtractedInvoice:
    """
    Extract structured invoice information from a PDF.

    Returns:
        ExtractedInvoice

    Raises:
        InvoiceExtractionError
    """

    pages = _render_pdf_pages(pdf_path)

    client = _get_openai_client()

    content = [
        {
            "type": "input_text",
            "text": (
                "Extract the invoice information from all provided "
                "pages. Treat all pages as belonging to the same invoice."
            ),
        }
    ]

    for page_number, image_bytes in enumerate(pages, start=1):
        content.append(
            {
                "type": "input_text",
                "text": f"Invoice page {page_number}",
            }
        )

        content.append(
            {
                "type": "input_image",
                "image_url": _image_to_data_url(image_bytes),
            }
        )

    try:
        response = client.responses.create(
            model="gpt-5-mini",
            instructions=EXTRACTION_SYSTEM_PROMPT,
            input=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "invoice_extraction",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "invoice_number": {
                                "type": ["string", "null"]
                            },
                            "invoice_date": {
                                "type": ["string", "null"]
                            },
                            "vendor_name": {
                                "type": ["string", "null"]
                            },
                            "vendor_tax_id": {
                                "type": ["string", "null"]
                            },
                            "po_number": {
                                "type": ["string", "null"]
                            },
                            "currency": {
                                "type": ["string", "null"]
                            },
                            "line_items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "description": {
                                            "type": "string"
                                        },
                                        "sku": {
                                            "type": ["string", "null"]
                                        },
                                        "quantity": {
                                            "type": ["number", "null"]
                                        },
                                        "unit_price": {
                                            "type": ["number", "null"]
                                        },
                                        "tax_rate_pct": {
                                            "type": ["number", "null"]
                                        },
                                        "line_subtotal": {
                                            "type": ["number", "null"]
                                        },
                                        "line_tax": {
                                            "type": ["number", "null"]
                                        },
                                        "line_total": {
                                            "type": ["number", "null"]
                                        },
                                    },
                                    "required": [
                                        "description",
                                        "sku",
                                        "quantity",
                                        "unit_price",
                                        "tax_rate_pct",
                                        "line_subtotal",
                                        "line_tax",
                                        "line_total",
                                    ],
                                },
                            },
                            "subtotal": {
                                "type": ["number", "null"]
                            },
                            "tax": {
                                "type": ["number", "null"]
                            },
                            "total": {
                                "type": ["number", "null"]
                            },
                            "extraction_notes": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                },
                            },
                        },
                        "required": [
                            "invoice_number",
                            "invoice_date",
                            "vendor_name",
                            "vendor_tax_id",
                            "po_number",
                            "currency",
                            "line_items",
                            "subtotal",
                            "tax",
                            "total",
                            "extraction_notes",
                        ],
                    },
                }
            },
        )

        raw_data = _extract_json(response)

    except InvoiceExtractionError:
        raise

    except Exception as exc:
        raise InvoiceExtractionError(
            f"OpenAI invoice extraction failed: {exc}"
        ) from exc

    try:
        return ExtractedInvoice.model_validate(raw_data)

    except ValidationError as exc:
        raise InvoiceExtractionError(
            "Extracted invoice data failed schema validation."
        ) from exc