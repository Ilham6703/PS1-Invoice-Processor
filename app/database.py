from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_PATH = Path("data/invoice_processor.db")
HISTORY_PDF_DIR = Path("outputs/processed_invoices")


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    _initialize_database(connection)

    return connection


def _initialize_database(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS processing_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            processed_at TEXT NOT NULL,
            filename TEXT NOT NULL,
            invoice_number TEXT,
            vendor_name TEXT,
            po_number TEXT,
            ai_recommendation TEXT NOT NULL,
            ai_explanation TEXT,
            findings_json TEXT,
            human_decision TEXT,
            reviewer_note TEXT,
            vendor_tax_id TEXT,
            invoice_total REAL,
            line_items_json TEXT,
            pdf_path TEXT
        )
        """
    )

    # Migration for an existing database created by the old version.
    existing_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(processing_history)"
        ).fetchall()
    }

    migrations = {
        "vendor_tax_id":
            "ALTER TABLE processing_history ADD COLUMN vendor_tax_id TEXT",

        "invoice_total":
            "ALTER TABLE processing_history ADD COLUMN invoice_total REAL",

        "line_items_json":
            "ALTER TABLE processing_history ADD COLUMN line_items_json TEXT",

        "pdf_path":
            "ALTER TABLE processing_history ADD COLUMN pdf_path TEXT",
    }

    for column, statement in migrations.items():
        if column not in existing_columns:
            connection.execute(statement)

    connection.commit()


def save_processing_result(
    filename: str,
    invoice_number: str | None,
    vendor_name: str | None,
    po_number: str | None,
    ai_recommendation: str,
    ai_explanation: str,
    findings: list[dict[str, Any]],
    human_decision: str | None = None,
    reviewer_note: str | None = None,
    vendor_tax_id: str | None = None,
    invoice_total: float | None = None,
    line_items: list[dict[str, Any]] | None = None,
    pdf_path: str | None = None,
) -> int:

    connection = get_connection()

    cursor = connection.execute(
        """
        INSERT INTO processing_history (
            processed_at,
            filename,
            invoice_number,
            vendor_name,
            po_number,
            ai_recommendation,
            ai_explanation,
            findings_json,
            human_decision,
            reviewer_note,
            vendor_tax_id,
            invoice_total,
            line_items_json,
            pdf_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            filename,
            invoice_number,
            vendor_name,
            po_number,
            ai_recommendation,
            ai_explanation,
            json.dumps(findings, default=str),
            human_decision,
            reviewer_note,
            vendor_tax_id,
            invoice_total,
            json.dumps(line_items or [], default=str),
            pdf_path,
        ),
    )

    connection.commit()

    record_id = int(cursor.lastrowid)

    connection.close()

    return record_id


def update_human_decision(
    record_id: int,
    human_decision: str,
    reviewer_note: str | None = None,
) -> None:

    connection = get_connection()

    connection.execute(
        """
        UPDATE processing_history
        SET human_decision = ?, reviewer_note = ?
        WHERE id = ?
        """,
        (
            human_decision,
            reviewer_note,
            record_id,
        ),
    )

    connection.commit()
    connection.close()


def get_processing_history() -> list[sqlite3.Row]:

    connection = get_connection()

    rows = connection.execute(
        """
        SELECT *
        FROM processing_history
        ORDER BY id DESC
        """
    ).fetchall()

    connection.close()

    return rows


def get_previous_approved_invoices(
    vendor_name: str | None = None,
    po_number: str | None = None,
    current_invoice_number: str | None = None,
) -> list[dict[str, Any]]:

    connection = get_connection()

    query = """
        SELECT
            id,
            processed_at,
            filename,
            invoice_number,
            vendor_name,
            po_number,
            vendor_tax_id,
            invoice_total,
            line_items_json,
            pdf_path,
            ai_recommendation,
            human_decision,
            reviewer_note
        FROM processing_history
        WHERE human_decision = 'APPROVE'
    """

    params: list[Any] = []

    if vendor_name:
        query += """
            AND LOWER(TRIM(vendor_name))
                = LOWER(TRIM(?))
        """
        params.append(vendor_name)

    if po_number:
        query += " AND po_number = ?"
        params.append(po_number)

    if current_invoice_number:
        query += " AND invoice_number != ?"
        params.append(current_invoice_number)

    query += " ORDER BY id ASC"

    rows = connection.execute(
        query,
        params,
    ).fetchall()

    connection.close()

    results = []

    for row in rows:
        item = dict(row)

        try:
            item["line_items"] = json.loads(
                item.get("line_items_json") or "[]"
            )
        except json.JSONDecodeError:
            item["line_items"] = []

        results.append(item)

    return results


def delete_processing_history(record_id: int) -> bool:

    connection = get_connection()

    row = connection.execute(
        """
        SELECT pdf_path
        FROM processing_history
        WHERE id = ?
        """,
        (record_id,),
    ).fetchone()

    if row is None:
        connection.close()
        return False

    pdf_path = row["pdf_path"]

    connection.execute(
        """
        DELETE FROM processing_history
        WHERE id = ?
        """,
        (record_id,),
    )

    connection.commit()
    connection.close()

    # Delete the stored PDF only if it belongs to our
    # application's processed-invoice directory.
    if pdf_path:

        try:
            stored_file = Path(pdf_path).resolve()
            allowed_dir = HISTORY_PDF_DIR.resolve()

            stored_file.relative_to(allowed_dir)

            if stored_file.is_file():
                stored_file.unlink()

        except (OSError, ValueError):
            pass

    return True