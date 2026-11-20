import re

_LOCAL = re.compile(r"^[17]\d{8}$")


def normalise_kenyan_phone(value: str) -> str | None:
    """0712345678 / 712345678 / +254712345678 / 254712345678 -> 254712345678, else None."""
    digits = re.sub(r"\D", "", value)
    if digits.startswith("254"):
        digits = digits[3:]
    elif digits.startswith("0"):
        digits = digits[1:]
    return f"254{digits}" if _LOCAL.match(digits) else None
