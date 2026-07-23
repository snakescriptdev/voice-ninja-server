"""
invoice_files.py — plain, directly-navigable invoice PDF URL.

Deliberately NOT behind Authorization-header auth (a browser window.open()/
<a href> navigation can't attach custom headers) — instead looked up by the
payment's own `invoice_reference`, an opaque, high-entropy (80-bit) string
generated once at payment-creation time (see invoice_utils.generate_invoice_reference).
That's what actually secures this URL: nobody can guess or enumerate another
user's reference, so no separate auth token is needed on top of it.
"""

from fastapi import APIRouter, HTTPException, Response
from fastapi_sqlalchemy import db

from app_v2.databases.models import PaymentModel, UnifiedAuthModel
from app_v2.utils.invoice_utils import generate_invoice_pdf

router = APIRouter(prefix="/api/v2/invoices", tags=["Invoices"])


@router.get("/{invoice_reference}.pdf")
def get_invoice_file(invoice_reference: str):
    # The .pdf suffix in the URL itself (not just the Content-Disposition
    # header) is what makes some browsers' PDF-viewer "download" buttons
    # reliably save with the right extension/behavior.
    payment = db.session.query(PaymentModel).filter(
        PaymentModel.invoice_reference == invoice_reference
    ).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Invoice not found")

    billed_to = db.session.query(UnifiedAuthModel).filter(
        UnifiedAuthModel.id == payment.user_id
    ).first()
    pdf_bytes = generate_invoice_pdf(payment, billed_to)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{payment.invoice_reference}.pdf"'},
    )
