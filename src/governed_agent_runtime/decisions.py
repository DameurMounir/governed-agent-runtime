"""Decision semantics used by the governed runtime."""

from enum import StrEnum


class Decision(StrEnum):
    """Outcome categories with deliberately different operational meanings."""

    # Public decision label; this is not a credential.
    PASS = "PASS"  # nosec B105
    BLOCKED = "BLOCKED"
    FAIL = "FAIL"


class EffectClass(StrEnum):
    """Declared external-effect risk for a workflow step."""

    READ_ONLY = "READ_ONLY"
    REVERSIBLE = "REVERSIBLE"
    IRREVERSIBLE = "IRREVERSIBLE"
