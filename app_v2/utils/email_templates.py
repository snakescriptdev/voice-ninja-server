"""
Shared visual theme for every transactional email the platform sends.

Every email is built by wrapping small content pieces (heading, paragraph,
button, info table, callout box...) with `render_email(...)`, so all emails
share one header/footer, one font stack, and one color palette instead of
each `send_*_email` function hand-rolling its own inline HTML (which is how
the old emails ended up with drifting colors/styles, and — being HTML-only,
with no plain-text alternative or display name — a higher spam score).

Colors mirror the web app's brand palette (see voice-ninja-client's
globals.css: --bg-dark, --text-accent, and the header gradient), collapsed
into a single accent since CSS gradients aren't reliable across email
clients (Outlook desktop in particular).
"""

from datetime import datetime, timezone

BRAND_NAME = "Voice Ninja"

COLOR_PAGE_BG = "#f3f4f6"
COLOR_CARD_BG = "#ffffff"
COLOR_HEADER_BG = "#0a0a0f"
COLOR_TEXT_PRIMARY = "#111827"
COLOR_TEXT_SECONDARY = "#6b7280"
COLOR_BORDER = "#e5e7eb"
COLOR_ACCENT = "#e06943"

FONT_STACK = "'Segoe UI', Helvetica, Arial, sans-serif"

# Content-ID the logo is attached under (see email_service.send_email_async,
# which attaches app_v2/static/images/voice_ninja_logo.png inline on every
# send) — referenced here as `cid:{LOGO_CID}` rather than a hosted/remote
# image URL so the mark still renders with images "blocked" (the common
# default for remote images) and doesn't depend on the API being reachable.
LOGO_CID = "voice-ninja-logo"
# Source PNG is 240x141 (aspect ~1.70); keep the same ratio at display size.
LOGO_WIDTH = 34
LOGO_HEIGHT = 20
# The mark's gradient (orange -> deep purple/blue) loses contrast directly on
# the near-black header — its darker stops are barely distinguishable from
# COLOR_HEADER_BG. A light rounded "badge" behind the icon guarantees it
# pops regardless of exact header darkness, rather than depending on getting
# the header color just right.
LOGO_BADGE_BG = "#ffffff"

# Semantic tones used by callout()/button() — background+border for callouts,
# solid fill for buttons. Keeping these distinct (unlike the header/footer,
# which never change) is intentional: a payment failure should still *read*
# as red, an amber low-balance warning as amber, etc. — only the container
# chrome around them needed to become consistent.
_TONES = {
    "accent": {"solid": COLOR_ACCENT, "bg": "#fdf1ec", "border": COLOR_ACCENT, "text": "#9a3412"},
    "success": {"solid": "#16a34a", "bg": "#f0fdf4", "border": "#16a34a", "text": "#166534"},
    "danger": {"solid": "#dc2626", "bg": "#fef2f2", "border": "#dc2626", "text": "#991b1b"},
    "warning": {"solid": "#d97706", "bg": "#fffbeb", "border": "#d97706", "text": "#92400e"},
    "info": {"solid": "#2563eb", "bg": "#eff6ff", "border": "#2563eb", "text": "#1e40af"},
}


def heading(text: str) -> str:
    return f'<h2 style="margin:0 0 16px;font-size:22px;line-height:1.3;font-weight:700;color:{COLOR_TEXT_PRIMARY};">{text}</h2>'


def paragraph(text: str, muted: bool = False) -> str:
    color = COLOR_TEXT_SECONDARY if muted else COLOR_TEXT_PRIMARY
    return f'<p style="margin:0 0 16px;color:{color};">{text}</p>'


def button(text: str, url: str, tone: str = "accent") -> str:
    solid = _TONES[tone]["solid"]
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 20px;">'
        f'<tr><td style="border-radius:999px;background:{solid};">'
        f'<a href="{url}" style="display:inline-block;padding:12px 28px;font-weight:700;'
        f'font-size:15px;color:#ffffff;text-decoration:none;border-radius:999px;">{text}</a>'
        f"</td></tr></table>"
    )


def callout(body_html: str, tone: str = "info") -> str:
    t = _TONES[tone]
    return (
        f'<div style="padding:16px 20px;background:{t["bg"]};border-radius:8px;'
        f'border-left:4px solid {t["border"]};margin:0 0 20px;color:{t["text"]};">'
        f"{body_html}</div>"
    )


def info_table(rows: list[tuple[str, str]]) -> str:
    trs = []
    for i, (label, value) in enumerate(rows):
        bg = "#f9fafb" if i % 2 == 0 else COLOR_CARD_BG
        trs.append(
            f'<tr style="background:{bg};"><td style="padding:10px 14px;color:{COLOR_TEXT_SECONDARY};'
            f'font-size:14px;border-radius:6px 0 0 6px;">{label}</td>'
            f'<td style="padding:10px 14px;color:{COLOR_TEXT_PRIMARY};font-size:14px;font-weight:700;'
            f'text-align:right;border-radius:0 6px 6px 0;">{value}</td></tr>'
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:separate;border-spacing:0 2px;margin:0 0 20px;">{"".join(trs)}</table>'
    )


def render_email(body_html: str, preheader: str = "") -> str:
    """Wraps `body_html` in the shared header/card/footer shell."""
    year = datetime.now(timezone.utc).year
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
</head>
<body style="margin:0;padding:0;background:{COLOR_PAGE_BG};font-family:{FONT_STACK};">
<span style="display:none;font-size:1px;color:{COLOR_PAGE_BG};line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">{preheader}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{COLOR_PAGE_BG};padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:{COLOR_CARD_BG};border-radius:12px;overflow:hidden;border:1px solid {COLOR_BORDER};">
<tr>
<td style="background:{COLOR_HEADER_BG};padding:18px 32px;">
<table role="presentation" cellpadding="0" cellspacing="0"><tr>
<td style="padding-right:12px;" valign="middle">
<table role="presentation" cellpadding="0" cellspacing="0" style="background:{LOGO_BADGE_BG};border-radius:8px;">
<tr><td style="padding:7px 9px;" valign="middle">
<img src="cid:{LOGO_CID}" width="{LOGO_WIDTH}" height="{LOGO_HEIGHT}" alt="Voice Ninja" style="display:block;border:0;outline:none;"/>
</td></tr>
</table>
</td>
<td valign="middle">
<span style="font-size:19px;font-weight:800;letter-spacing:0.2px;color:#ffffff;">
<span style="color:{COLOR_ACCENT};">Voice</span>&nbsp;Ninja
</span>
</td>
</tr></table>
</td>
</tr>
<tr>
<td style="padding:36px 32px;font-size:15px;line-height:1.6;color:{COLOR_TEXT_PRIMARY};">
{body_html}
</td>
</tr>
<tr>
<td style="padding:18px 32px;border-top:1px solid {COLOR_BORDER};background:#fafafa;">
<p style="margin:0;font-size:12px;color:{COLOR_TEXT_SECONDARY};">&copy; {year} {BRAND_NAME}. All rights reserved.</p>
<p style="margin:6px 0 0;font-size:12px;color:{COLOR_TEXT_SECONDARY};">This is an automated message — please don't reply directly to this email.</p>
</td>
</tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def signoff(extra: str = "") -> str:
    """Standard closing line, optionally preceded by an extra note paragraph."""
    extra_html = paragraph(extra, muted=True) if extra else ""
    return f'{extra_html}<p style="margin:0;color:{COLOR_TEXT_SECONDARY};">Thanks,<br/>{BRAND_NAME} Team</p>'
