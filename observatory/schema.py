"""Closed, versioned data contracts. Unknown is distinct from false or zero."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires an explicit timezone")
    return value


Timestamp = Annotated[str, Field(max_length=64, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$", json_schema_extra={"format": "date-time"}), AfterValidator(timestamp)]
Id = Annotated[str, Field(min_length=1, max_length=160)]
Canonical = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9.-]*/[a-zA-Z0-9][a-zA-Z0-9._-]*$")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
PositiveInt = Annotated[int, Field(gt=0)]
Nonnegative = Annotated[float, Field(ge=0)]
Score = Annotated[float, Field(ge=0, le=100)]
Dimension = Literal["coding", "chat", "agent", "tools", "reasoning", "context", "longContext", "price", "latency"]


class Data(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Source(Data):
    id: Id
    url: str
    attribution: str
    license: str
    contentSha256: Hash
    fetchedAt: Timestamp
    checkedAt: Timestamp
    upstreamVersion: str | None
    status: Literal["fresh", "cached", "seed"]
    error: str | None


class Provenance(Data):
    sourceIds: Annotated[list[Id], Field(min_length=1)]
    fetchedAt: Timestamp


class Identity(Data):
    canonicalId: Canonical
    provider: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")]
    apiModelId: Id
    aliases: list[Id]
    displayName: Id
    status: Literal["unknown", "preview", "active", "deprecated", "retired"]


class Capabilities(Data):
    tools: bool | None
    vision: bool | None
    reasoning: bool | None
    structuredOutput: bool | None
    streaming: bool | None


class Price(Data):
    priceVersion: Id
    currency: Literal["USD"]
    unit: Literal["per-million-tokens"]
    input: Nonnegative
    output: Nonnegative
    cachedInput: Nonnegative | None
    observedAt: Timestamp
    effectiveFrom: Timestamp | None
    effectiveUntil: Timestamp | None
    provenance: Provenance

    @model_validator(mode="after")
    def interval(self):
        if self.effectiveUntil is not None and (
            self.effectiveFrom is None or datetime.fromisoformat(self.effectiveUntil.replace("Z", "+00:00")) <= datetime.fromisoformat(self.effectiveFrom.replace("Z", "+00:00"))
        ):
            raise ValueError("invalid effective price interval")
        return self


class Facts(Data):
    contextWindow: PositiveInt | None
    maxOutputTokens: PositiveInt | None
    capabilities: Capabilities
    pricing: list[Price]
    provenance: Provenance

    @model_validator(mode="after")
    def context(self):
        if self.contextWindow and self.maxOutputTokens and self.maxOutputTokens > self.contextWindow:
            raise ValueError("output capacity exceeds context")
        return self


class Environment(Data):
    agent: str | None
    harness: str
    harnessVersion: str | None
    reasoningEffort: str | None
    promptMode: str | None
    testCases: PositiveInt | None
    checked: bool | None


class Metric(Data):
    name: Id
    value: float
    unit: Literal["percent", "seconds"]
    direction: Literal["higher", "lower"]
    minimum: float
    maximum: float | None
    confidenceLow: float | None
    confidenceHigh: float | None

    @model_validator(mode="after")
    def bounds(self):
        if self.value < self.minimum or (self.maximum is not None and self.value > self.maximum):
            raise ValueError("metric outside its declared scale")
        if self.confidenceLow is not None and self.confidenceHigh is not None:
            if not self.confidenceLow <= self.value <= self.confidenceHigh:
                raise ValueError("invalid confidence interval")
        return self


class Evidence(Data):
    id: Id
    sourceId: Id
    sourceModelLabel: Id
    benchmark: Id
    benchmarkVersion: str | None
    metric: Metric
    environment: Environment
    evaluatedAt: Timestamp | None
    reportedAt: Timestamp | None
    observedAt: Timestamp
    dateMeaning: Literal["run-date", "leaderboard-date", "unknown"]
    rowKey: str
    modelId: Canonical | None
    mappingStatus: Literal["matched", "unmatched", "ambiguous"]


class Component(Data):
    key: str
    score: Score
    weight: Annotated[float, Field(gt=0, le=1)]
    evidenceIds: list[Id]
    sourceIds: list[Id]


class Assessment(Data):
    score: Score | None
    coverage: Annotated[float, Field(ge=0, le=1)]
    freshness: Literal["fresh", "stale", "unknown"]
    newestEvidenceAt: Timestamp | None
    recruitmentReady: bool
    components: list[Component]
    note: str


class Model(Data):
    identity: Identity
    facts: Facts
    evidenceIds: list[Id]
    dimensions: dict[Dimension, Assessment]
    provenance: Provenance


class Feed(Data):
    schemaVersion: Literal[1]
    catalogVersion: Id
    generatedAt: Timestamp
    scoringPolicyVersion: Id
    mappingVersion: Id
    sources: list[Source]
    models: dict[Canonical, Model]
    evidence: dict[Id, Evidence]

    @model_validator(mode="after")
    def references(self):
        sources = {s.id for s in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError("duplicate source ID")
        aliases = set()
        for key, model in self.models.items():
            i = model.identity
            if key != i.canonicalId or not key.startswith(i.provider + "/"):
                raise ValueError("inconsistent canonical identity")
            for alias in [i.apiModelId, *i.aliases]:
                scoped = (i.provider, alias)
                if scoped in aliases:
                    raise ValueError("ambiguous provider alias")
                aliases.add(scoped)
            refs = [model.provenance, model.facts.provenance, *[p.provenance for p in model.facts.pricing]]
            if any(not set(p.sourceIds) <= sources for p in refs):
                raise ValueError("unknown provenance source")
            for evidence_id in model.evidenceIds:
                if evidence_id not in self.evidence or self.evidence[evidence_id].modelId != key:
                    raise ValueError("invalid model evidence reference")
            for assessment in model.dimensions.values():
                for component in assessment.components:
                    if not set(component.sourceIds) <= sources:
                        raise ValueError("unknown component source")
                    if not set(component.evidenceIds) <= set(model.evidenceIds):
                        raise ValueError("component references another model")
        for key, item in self.evidence.items():
            if key != item.id or item.sourceId not in sources:
                raise ValueError("invalid evidence key or source")
            if (item.modelId is not None) != (item.mappingStatus == "matched"):
                raise ValueError("inconsistent evidence mapping")
            if item.modelId is not None and (
                item.modelId not in self.models or key not in self.models[item.modelId].evidenceIds
            ):
                raise ValueError("orphan mapped evidence")
        return self


class Manifest(Data):
    schemaVersion: Literal[1]
    catalogVersion: Id
    generatedAt: Timestamp
    modelCount: Annotated[int, Field(ge=1, le=10000)]
    snapshot: Annotated[str, Field(max_length=256, pattern=r"^[A-Za-z0-9._/-]+\.json$")]
    hashAlgorithm: Literal["sha256"]
    hash: Hash

    @model_validator(mode="after")
    def relative_path(self):
        if any(part in {"", ".", ".."} for part in self.snapshot.split("/")):
            raise ValueError("unsafe snapshot path")
        return self
