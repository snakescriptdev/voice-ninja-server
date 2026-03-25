from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request

def format_field_name(field: str) -> str:
    """Convert snake_case to readable format."""
    return field.replace("_", " ").capitalize()


def get_readable_message(field: str, msg: str) -> str:
    field_name = format_field_name(field)

    # ✅ Remove unwanted prefixes like "Value error, "
    if "," in msg:
        msg = msg.split(",", 1)[-1].strip()

    msg_lower = msg.lower()

    if "field required" in msg_lower:
        return f"{field_name} is required"

    if "none is not an allowed value" in msg_lower:
        return f"{field_name} cannot be empty"

    if "value is not a valid integer" in msg_lower:
        return f"{field_name} must be a number"

    if "value is not a valid string" in msg_lower:
        return f"{field_name} must be a valid text"

    if "ensure this value has at least" in msg_lower:
        return msg  # keep detailed message

    if "ensure this value has at most" in msg_lower:
        return msg

    if "value is not a valid email" in msg_lower:
        return "Enter a valid email address"

    # Avoid duplication
    if field_name.lower() in msg_lower:
        return msg

    return f"{field_name} {msg}"