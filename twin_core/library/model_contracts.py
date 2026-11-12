"""Additive model representations on existing reusable component entries."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component: str = Field(min_length=1, max_length=200)
    model_id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)


class ModelArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(strict=True, ge=0)
    licence: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        if (
            not value
            or value.startswith("/")
            or "\\" in value
            or any(p in {"", "..", "."} for p in value.split("/"))
        ):
            raise ValueError("artifact path must be relative and remain inside the package")
        return value

    @field_validator("url")
    @classmethod
    def public_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("use a public HTTPS source URL without credentials or query tokens")
        return value


class ModelRepresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    model_id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    kind: Literal["cad", "physics", "emulation", "module"]
    engine: str = Field(min_length=1)
    engine_versions: list[str] = Field(default_factory=list, max_length=50)
    entrypoint: str
    usage: str = Field(min_length=1, max_length=16000)
    limitations: list[str] = Field(default_factory=list, max_length=100)
    artifacts: list[ModelArtifact] = Field(min_length=1, max_length=1000)
    dependencies: list[ModelDependency] = Field(default_factory=list, max_length=100)
    status: Literal["active", "deprecated", "withdrawn"] = "active"
    #: Which of the file's axes points up (2026-11-12): makers' files differ (Seeed's XIAO
    #: is Y up, Waveshare's display Z up). The studio turns it to +Y. None means "z".
    up_axis: Literal["x", "y", "z", "-x", "-y", "-z"] | None = None
    status_reason: str = ""

    @model_validator(mode="after")
    def valid_entrypoint(self) -> ModelRepresentation:
        paths = [a.path for a in self.artifacts]
        if len(paths) != len(set(paths)) or self.entrypoint not in paths:
            raise ValueError("entrypoint must name one unique artifact path")
        if self.status != "active" and not self.status_reason.strip():
            raise ValueError("inactive models require a reason")
        return self


def validate_models(raw: object) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 50:
        raise ValueError("models must be a list of at most 50 representations")
    models = [ModelRepresentation.model_validate(value) for value in raw]
    keys = [(m.model_id, m.version) for m in models]
    if len(set(keys)) != len(keys):
        raise ValueError("model id/version pairs must be unique")
    out = [m.model_dump(mode="json") for m in models]
    for dumped in out:
        if dumped.get("up_axis") is None:
            # Models published before up_axis keep their exact content (they are immutable).
            dumped.pop("up_axis", None)
    return out
