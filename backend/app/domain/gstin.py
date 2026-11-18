"""GSTIN format and state-prefix validation. No checksum in R1 (07 section 2: a checksum written from memory is risky).

State codes come from a specialist page, not the official portal (UNVERIFIED U7).
"""

from __future__ import annotations

import random
import re
import string

from app.domain.errors import FieldError, ValidationFailed

# VERIFY: official GST portal list (see .build/UNVERIFIED.md U7).
STATE_CODES: dict[str, str] = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman & Diu", "26": "Dadra & Nagar Haveli", "27": "Maharashtra", "28": "Andhra Pradesh (old)",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman & Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}  # fmt: skip

# 2 state digits, 5 letters, 4 digits, 1 letter (PAN-style), 1 entity digit/letter, literal Z, 1 check character.
_GSTIN = re.compile(r"^(\d{2})([A-Z]{5}\d{4}[A-Z])([1-9A-Z])Z([0-9A-Z])$")


def validate_state_code(code: str) -> str:
    if code not in STATE_CODES:
        raise ValidationFailed(
            "unknown state code", errors=[FieldError("state_code", "not a GST state code")]
        )
    return code


def validate_gstin(gstin: str, state_code: str) -> str:
    match = _GSTIN.match(gstin)
    if match is None:
        raise ValidationFailed(
            "invalid GSTIN format", errors=[FieldError("gstin", "invalid format")]
        )
    if match.group(1) not in STATE_CODES:
        raise ValidationFailed(
            "invalid GSTIN state", errors=[FieldError("gstin", "unknown state prefix")]
        )
    if match.group(1) != state_code:
        raise ValidationFailed(
            "GSTIN state mismatch",
            errors=[FieldError("gstin", "state prefix does not match state")],
        )
    return gstin


def make_test_gstin(state_code: str, seed: int) -> str:
    """A syntactically valid, SYNTHETIC GSTIN for tests and seed data. Never a real taxpayer's number.

    The check character is arbitrary because R1 does not validate it. The PAN part uses the letters
    'TEST' + one more so it is recognisably fake.
    """
    rng = random.Random(seed)  # noqa: S311 - deterministic test data, not security
    pan = (
        "TEST"
        + rng.choice(string.ascii_uppercase)
        + "".join(rng.choice(string.digits) for _ in range(4))
    )
    pan += rng.choice(string.ascii_uppercase)
    entity = rng.choice("123456789")
    check = rng.choice(string.digits + string.ascii_uppercase)
    return f"{validate_state_code(state_code)}{pan}{entity}Z{check}"
