from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request

def format_field_name(field: str) -> str:
    """Convert snake_case to readable format."""
    if isinstance(field, int):
        return str(field)
    return field.replace("_", " ").capitalize()


def get_readable_message(field: str, msg: str) -> str:
    field_name = format_field_name(field)

    # ✅ Remove the "Value error, " prefix Pydantic adds around custom
    # validator messages. Only strip this specific prefix — not any comma —
    # since native Pydantic messages can legitimately contain commas
    # (e.g. "Input should be a valid integer, got a number with a fractional part").
    if msg.lower().startswith("value error,"):
        msg = msg.split(",", 1)[-1].strip()

    msg_lower = msg.lower()

    if "field required" in msg_lower:
        return f"{field_name} is required"

    if "none is not an allowed value" in msg_lower:
        return f"{field_name} cannot be empty"

    if "fractional part" in msg_lower:
        return f"{field_name} must be a whole number, not a decimal"

    if "value is not a valid integer" in msg_lower or msg_lower.startswith("input should be a valid integer"):
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