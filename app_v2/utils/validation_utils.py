"""Shared field-validation helpers for Pydantic schemas.

Currently home to the entity "name" validation rules shared by AgentModel,
WidgetModel, and WebAgentPageModel's name fields (agent_name / widget_name /
web_agent_name). Each schema still declares its own `@field_validator` (this
codebase does not use a shared validator mixin — see agent_schema.py,
widget_schema.py, web_agent_schema.py, twilio_connector_schema.py, profile.py
for the established per-schema `field_validator` pattern), but the actual
rule implementation lives here once so it isn't duplicated three times.
"""

import re
from typing import Optional

NAME_MIN_LENGTH = 3
NAME_MAX_LENGTH = 50

# Purely-numeric names (e.g. "12345") are rejected outright.
_NUMERIC_ONLY_RE = re.compile(r"^\d+$")

# Letters, numbers, whitespace, hyphens, underscores, and apostrophes only.
_ALLOWED_CHARS_RE = re.compile(r"^[a-zA-Z0-9\s\-_']+$")

# At least one letter must be present - a name made up solely of digits,
# spaces, hyphens, underscores, and/or apostrophes (in any combination, e.g.
# "----", "___", "''''", "12-34") isn't a meaningful name.
_HAS_LETTER_RE = re.compile(r"[a-zA-Z]")

_CHARSET_ERROR_MESSAGE = (
    f"Name must be {NAME_MIN_LENGTH}-{NAME_MAX_LENGTH} characters and can only contain letters, "
    "numbers, spaces, hyphens, underscores, and apostrophes."
)

_NO_LETTER_ERROR_MESSAGE = (
    "Name must contain at least one letter - it cannot be made up of only "
    "spaces, numbers, hyphens, underscores, and/or apostrophes."
)


def _check_entity_name(value: str) -> str:
    v = value.strip()
    if not v:
        raise ValueError("Name cannot be empty or only spaces.")
    # Numeric-only check runs first so "12345" gets a specific error instead
    # of the generic charset/length one.
    if _NUMERIC_ONLY_RE.match(v):
        raise ValueError("Name cannot be only numbers.")
    if len(v) < NAME_MIN_LENGTH or len(v) > NAME_MAX_LENGTH:
        raise ValueError(_CHARSET_ERROR_MESSAGE)
    if not _ALLOWED_CHARS_RE.match(v):
        raise ValueError(_CHARSET_ERROR_MESSAGE)
    # Runs last: only reached once we know v is the right length and made up
    # entirely of allowed characters, so a failure here means it's some
    # letter-less combination of the "filler" characters (digits/spaces/
    # hyphens/underscores/apostrophes) - e.g. "----", "12 34", "'''".
    if not _HAS_LETTER_RE.search(v):
        raise ValueError(_NO_LETTER_ERROR_MESSAGE)
    return v


def validate_entity_name(value: str) -> str:
    """Field validator for a required name field (e.g. *Create schemas)."""
    if value is None:
        raise ValueError("Name is required.")
    return _check_entity_name(value)


def validate_entity_name_optional(value: Optional[str]) -> Optional[str]:
    """Field validator for an optional name field (e.g. *Update schemas)."""
    if value is None:
        return None
    return _check_entity_name(value)


_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

_HEX_COLOR_ERROR_MESSAGE = (
    "bg_color must be a valid hex color code, e.g. #0B0B0F or #FFF."
)


def validate_hex_color(value: str) -> str:
    """Field validator for a required hex color field (e.g. *Create schemas)."""
    if value is None or not _HEX_COLOR_RE.match(value):
        raise ValueError(_HEX_COLOR_ERROR_MESSAGE)
    return value


def validate_hex_color_optional(value: Optional[str]) -> Optional[str]:
    """Field validator for an optional hex color field (e.g. *Update schemas)."""
    if value is None:
        return None
    return validate_hex_color(value)
