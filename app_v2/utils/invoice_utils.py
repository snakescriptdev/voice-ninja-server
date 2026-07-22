"""
invoice_utils.py — on-demand PDF invoice/receipt generation for PaymentModel rows.

Generated at request time from the payment row itself (no pre-generation, no
file storage) — there's nothing to keep in sync and nothing to clean up.
"""

from fpdf import FPDF, XPos, YPos

from app_v2.databases.models import PaymentModel, UnifiedAuthModel

BRAND = (224, 105, 67)       # #E06943 — Voice Ninja brand orange
DARK = (24, 24, 29)          # near-black body text
MUTED = (120, 120, 130)      # secondary/label text
LINE = (230, 230, 234)       # hairlines / borders
FILL = (247, 246, 248)       # light table-header fill
SUCCESS = (22, 163, 74)
FAILED = (220, 38, 38)
PENDING = (202, 138, 4)


def _describe_payment(payment: PaymentModel) -> str:
    if payment.metadata_json and payment.metadata_json.get("coins"):
        return f"Credit Purchase ({payment.metadata_json['coins']:,} credits)"
    return "Credit Purchase"


def _status_color(status: str):
    s = status.lower()
    if "fail" in s or "declin" in s or "error" in s:
        return FAILED
    if "pending" in s or "process" in s:
        return PENDING
    return SUCCESS


class _InvoicePDF(FPDF):
    def footer(self):
        self.set_y(-22)
        self.set_draw_color(*LINE)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(
            0, 5,
            "Voice Ninja  |  support@voiceninja.ai  |  This is a system-generated invoice.",
            align="C",
        )


def generate_invoice_pdf(payment: PaymentModel, user: UnifiedAuthModel | None) -> bytes:
    pdf = _InvoicePDF()
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(auto=True, margin=28)
    pdf.add_page()

    # ---- Header band ----
    pdf.set_fill_color(*BRAND)
    pdf.rect(0, 0, pdf.w, 30, style="F")
    pdf.set_xy(15, 9)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 19)
    pdf.cell(0, 9, "Voice Ninja", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "AI Voice Agents Platform", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    status = payment.status.value if hasattr(payment.status, "value") else str(payment.status)
    status_rgb = _status_color(status)
    is_failed = "fail" in status.lower() or "declin" in status.lower() or "error" in status.lower()
    total_label = "Amount Attempted" if is_failed else "Total Paid"
    footer_note = (
        "This receipt is for your records only - this payment attempt was not "
        "completed and no charge was made."
        if is_failed
        else "Thank you for choosing Voice Ninja. This receipt confirms your "
        "one-time credit purchase - credits never expire."
    )

    # ---- Title + status pill ----
    pdf.set_xy(15, 40)
    pdf.set_font("Helvetica", "B", 17)
    pdf.set_text_color(*DARK)
    pdf.cell(pdf.epw - 45, 9, "INVOICE")

    pdf.set_fill_color(*status_rgb)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(45, 9, status.upper(), align="C", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 6, f"INV-{payment.id:06d}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(8)

    # ---- Billed To  /  Invoice date (two columns) ----
    col_w = pdf.epw / 2
    y0 = pdf.get_y()

    pdf.set_xy(15, y0)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(col_w, 5, "BILLED TO", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(15)
    user_email = (user.email if user else None) or "-"
    billed_name = (user.name if user and user.name else None) or user_email
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*DARK)
    pdf.cell(col_w, 7, billed_name, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if billed_name != user_email:
        pdf.set_x(15)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*MUTED)
        pdf.cell(col_w, 6, user_email, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_xy(15 + col_w, y0)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(col_w, 5, "INVOICE DATE", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(15 + col_w)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*DARK)
    pdf.cell(
        col_w, 7,
        payment.created_at.strftime("%d %b %Y, %H:%M UTC"),
        align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )

    pdf.ln(10)

    # ---- Line item table ----
    desc_w = pdf.epw * 0.68
    amt_w = pdf.epw - desc_w

    pdf.set_x(15)
    pdf.set_fill_color(*FILL)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(desc_w, 9, "  DESCRIPTION", fill=True)
    pdf.cell(amt_w, 9, "AMOUNT  ", align="R", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*DARK)
    pdf.cell(desc_w, 13, f"  {_describe_payment(payment)}")
    pdf.cell(amt_w, 13, f"{payment.currency} {payment.amount:,.2f}  ", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_draw_color(*LINE)
    pdf.set_line_width(0.4)
    pdf.line(15, pdf.get_y(), pdf.w - 15, pdf.get_y())
    pdf.ln(5)

    pdf.set_x(15)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*DARK)
    pdf.cell(desc_w, 9, total_label, align="R")
    pdf.set_text_color(*(FAILED if is_failed else BRAND))
    pdf.cell(amt_w, 9, f"{payment.currency} {payment.amount:,.2f}  ", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(14)

    # ---- Payment details ----
    pdf.set_x(15)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5, "PAYMENT DETAILS", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)

    details = [
        ("Payment ID", payment.provider_payment_id or "-"),
        ("Order ID", payment.provider_order_id or "-"),
        ("Payment Method", "Razorpay"),
    ]
    for label, value in details:
        pdf.set_x(15)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*MUTED)
        pdf.cell(35, 7, label)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*DARK)
        remaining_width = pdf.w - pdf.r_margin - pdf.x
        pdf.multi_cell(remaining_width, 7, str(value), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(6)
    pdf.set_x(15)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(pdf.epw, 5.5, footer_note)

    return bytes(pdf.output())
