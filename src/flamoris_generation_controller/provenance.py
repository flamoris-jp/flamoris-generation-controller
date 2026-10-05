"""Verified non-secret provenance values; never authorization grants."""

from pydantic import BaseModel, ConfigDict, Field


class ExternalProvenance(BaseModel):
    """Immutable provenance captured by trusted ingress, never public tool arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    issuer: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    subject: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def provenance_metadata(value: ExternalProvenance | None) -> dict:
    return {"external_provenance": value.model_dump()} if value is not None else {}


def archived_provenance(record: dict) -> dict:
    """Legacy absence is anonymous; malformed new provenance cannot become anonymous."""
    if "external_provenance" not in record:
        return {}
    try:
        value = ExternalProvenance.model_validate(record["external_provenance"])
    except (ValueError, TypeError):
        raise ValueError("Invalid archived external provenance") from None
    return provenance_metadata(value)
