"""Arena observations and the public table: an explicit row per inference variant."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .schema import Data, Hash, Id, Timestamp, Facts, Source, Canonical

Effort = Literal["unspecified", "none", "low", "medium", "high", "xhigh", "max", "thinking", "unknown"]
Category = Literal["chat", "coding", "math", "instructionFollowing", "multiTurn", "creativeWriting",
                   "vision", "webdev", "agent", "agentTaskOutcome", "agentSteerability", "agentToolHallucination", "agentBashReliability"]


class Inference(Data):
    effort: Effort
    budget: str | None
    harness: str | None


class ArenaObservation(Data):
    id: Id
    sourceId: Id
    modelName: Id
    organization: str
    category: Category
    metric: Literal["bradley-terry", "ips"]
    value: float
    lower: float
    upper: float
    rank: Annotated[int, Field(ge=1)]
    votes: Annotated[int, Field(ge=0)] | None
    observations: Annotated[int, Field(ge=0)] | None
    sessions: Annotated[int, Field(ge=0)] | None
    publishedAt: Timestamp

    @model_validator(mode="after")
    def interval(self):
        if not self.lower <= self.value <= self.upper:
            raise ValueError("Arena confidence interval does not contain value")
        if self.metric == "bradley-terry" and not 0 <= self.value <= 5000:
            raise ValueError("unexpected Arena rating scale")
        if self.metric == "ips" and not -1 <= self.value <= 1:
            raise ValueError("unexpected Arena IPS scale")
        return self


class ArenaScore(Data):
    sourceId: Id
    modelName: Id
    metric: Literal["bradley-terry", "ips"]
    value: float
    lower: float
    upper: float
    rank: Annotated[int, Field(ge=1)]
    votes: Annotated[int, Field(ge=0)] | None
    observations: Annotated[int, Field(ge=0)] | None
    sessions: Annotated[int, Field(ge=0)] | None
    publishedAt: Timestamp
    freshness: Literal["fresh", "stale"]

    @model_validator(mode="after")
    def bounds(self):
        if not self.lower <= self.value <= self.upper:
            raise ValueError("invalid Arena confidence interval")
        if self.metric == "ips" and not -1 <= self.value <= 1:
            raise ValueError("invalid Arena IPS value")
        if self.metric == "bradley-terry" and not 0 <= self.value <= 5000:
            raise ValueError("invalid Arena rating")
        return self


class TableRow(Data):
    variantId: Hash
    arenaName: Id
    provider: str | None
    canonicalId: Canonical | None
    apiModelId: Id | None
    binding: Literal["matched", "unmatched", "ambiguous"]
    inference: Inference
    facts: Facts | None
    scores: dict[Category, ArenaScore]

    @model_validator(mode="after")
    def identity(self):
        matched = self.binding == "matched"
        if matched != (self.canonicalId is not None and self.apiModelId is not None and self.provider is not None):
            raise ValueError("inconsistent Arena model binding")
        if not matched and self.facts is not None:
            raise ValueError("unmatched variant cannot borrow another model's facts")
        if matched and not self.canonicalId.startswith(self.provider + "/"):
            raise ValueError("canonical identity and provider disagree")
        return self


class SeerTable(Data):
    schemaVersion: Literal[2]
    catalogVersion: Id
    generatedAt: Timestamp
    policyVersion: Literal["arena-variants-1"]
    sources: list[Source]
    rows: list[TableRow]

    @model_validator(mode="after")
    def references(self):
        ids = {source.id for source in self.sources}
        if len(ids) != len(self.sources) or len({row.variantId for row in self.rows}) != len(self.rows):
            raise ValueError("duplicate table source or variant")
        for row in self.rows:
            if any(score.sourceId not in ids for score in row.scores.values()):
                raise ValueError("unknown Arena score source")
            if row.facts:
                for provenance in [row.facts.provenance, *[p.provenance for p in row.facts.pricing]]:
                    if not set(provenance.sourceIds) <= ids:
                        raise ValueError("unknown facts source")
        return self
