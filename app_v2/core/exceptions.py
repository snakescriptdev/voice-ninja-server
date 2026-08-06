from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request

# Field names accepted as a lookup id (e.g. `voice`/`ai_model`/`language`
# take the numeric `id` from their respective GET /api/v2/public/... item,
# not a raw count) — anything ending in "_id" is caught generically below,
# these don't follow that naming convention so they're listed explicitly.
ID_FIELD_NAMES = {"voice", "ai_model", "language"}

# format_field_name() would otherwise render this "Ai model".
_FIELD_NAME_OVERRIDES = {"ai_model": "AI Model"}


def format_field_name(field: str) -> str:
    """Convert snake_case to readable format."""
    if isinstance(field, int):
        return str(field)
    if field in _FIELD_NAME_OVERRIDES:
        return _FIELD_NAME_OVERRIDES[field]
    return field.replace("_", " ").capitalize()


def _is_id_field(field) -> bool:
    return isinstance(field, str) and (field in ID_FIELD_NAMES or field.endswith("_id"))


def get_readable_message(field: str, msg: str) -> str:
    field_name = format_field_name(field)

    # ✅ Remove the "Value error, " prefix Pydantic adds around custom
    # validator messages. Only strip this specific prefix — not any comma —
    # since native Pydantic messages can legitimately contain commas
    # (e.g. "Input should be a valid integer, got a number with a fractional part").
    if msg.lower().startswith("value error,"):
        msg = msg.split(",", 1)[-1].strip()

    msg_lower = msg.lower()

    # Pydantic v2's `extra="forbid"` error. Uses the raw field name (not
    # format_field_name's snake_case-to-words rendering) since this is
    # echoing back exactly what the caller sent, not a known field of ours.
    if "extra inputs are not permitted" in msg_lower:
        return f"The request contains an unsupported field: '{field}'"

    if "field required" in msg_lower:
        return f"{field_name} is required"

    if "none is not an allowed value" in msg_lower:
        return f"{field_name} cannot be empty"

    if "fractional part" in msg_lower:
        return f"{field_name} must be a whole number, not a decimal"

    if "value is not a valid integer" in msg_lower or msg_lower.startswith("input should be a valid integer"):
        if _is_id_field(field):
            # Avoid "Invalid Twilio connector id ID" for fields already
            # ending in "_id" — the appended "ID" covers that on its own.
            label = field_name[:-3] if field_name.lower().endswith(" id") else field_name
            return f"Invalid {label} ID"
        return f"{field_name} must be a number"

    if "value is not a valid string" in msg_lower:
        return f"{field_name} must be a valid text"

    if "ensure this value has at least" in msg_lower:
        return msg  # keep detailed message

    if "ensure this value has at most" in msg_lower:
        return msg

    # Pydantic v2 min/max length errors, e.g. "String should have at least 3 characters"
    if msg_lower.startswith("string should have at least"):
        min_len = "".join(c for c in msg if c.isdigit())
        return f"{field_name} is too short. Please enter at least {min_len} letters"

    if msg_lower.startswith("string should have at most"):
        max_len = "".join(c for c in msg if c.isdigit())
        return f"{field_name} is too long. Please enter at most {max_len} letters"

    # Pydantic v2 numeric range errors, e.g. "Input should be greater than 0"
    if msg_lower.startswith("input should be"):
        return f"{field_name} must be {msg[len('Input should be'):].strip()}"

    if "value is not a valid email" in msg_lower:
        return "Enter a valid email address"

    # Avoid duplication
    if field_name.lower() in msg_lower:
        return msg

    return f"{field_name} {msg}"