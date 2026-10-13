"""Parse public structured exports without executing downloaded code."""

import csv
import io
import json
import re
from typing import Literal

import yaml
from pydantic import Field

from .schema import Data, Evidence, Facts, Identity, Source
from .arena_schema import ArenaObservation
from .util import json_bytes, sha


class SourceConfig(Data):
    id: str
    parser: Literal["models-dev", "aider", "evalplus", "arena", "bfcl", "swe", "arena-parquet"]
    url: str
    license: str
    attribution: str
    maxBytes: int = Field(ge=1, le=12000000)
    subset: str | None = None


class Config(Data):
    version: str
    providers: list[str]
    sources: list[SourceConfig]


class FactRow(Data):
    identity: Identity
    facts: Facts


class Bundle(Data):
    source: Source
    facts: list[FactRow]
    evidence: list[Evidence]
    issues: list[str] = Field(default_factory=list)
    arenaRows: list[ArenaObservation] = Field(default_factory=list)


def canonical(provider: str, api_id: str) -> str:
    if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", api_id):
        return f"{provider}/{api_id}"
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", api_id).strip("-.")[:100] or "model"
    return f"{provider}/{slug}-{sha(api_id.encode())[:12]}"


def date(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text + "T00:00:00Z"
    raise ValueError("unsupported source date")


def numeric(value) -> float | None:
    if value in (None, "", "-", "N/A", "NaN"):
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not a benchmark score")
    return float(str(value).strip().rstrip("%"))


def environment(**overrides):
    return dict(agent=None, harness="unknown", harnessVersion=None, reasoningEffort=None,
                promptMode=None, testCases=None, checked=None) | overrides


def effort(label: str) -> str | None:
    tail = re.search(r"\(([^)]+)\)$", label)
    if not tail:
        return None
    text = tail[1].lower()
    if text in {"fc", "prompt", "default", "no thinking", "no think"}:
        return None
    return text


def observation(source, label, benchmark, version, metric, value, row_key, env,
                reported=None, meaning="unknown", unit="percent", low=None, high=None):
    if value is None:
        return None
    identity = [source.id, benchmark, version, label, metric, row_key, env]
    return Evidence.model_validate({
        "id": "ev-" + sha(json_bytes(identity))[:32], "sourceId": source.id,
        "sourceModelLabel": label, "benchmark": benchmark, "benchmarkVersion": version,
        "metric": {"name": metric, "value": value, "unit": unit,
                   "direction": "lower" if unit == "seconds" else "higher", "minimum": 0.0,
                   "maximum": 100.0 if unit == "percent" else None,
                   "confidenceLow": low, "confidenceHigh": high},
        "environment": env, "evaluatedAt": reported if meaning == "run-date" else None,
        "reportedAt": reported, "observedAt": source.fetchedAt, "dateMeaning": meaning,
        "rowKey": row_key, "modelId": None, "mappingStatus": "unmatched",
    })


def parse_facts(raw, source, providers):
    root = json.loads(raw)
    if not isinstance(root, dict):
        raise ValueError("models.dev must be an object")
    result, issues = [], []
    for provider in providers:
        models = root.get(provider, {}).get("models", {})
        for api_id, model in sorted(models.items()):
            modalities = model.get("modalities", {})
            if modalities.get("output") and "text" not in modalities["output"]:
                continue
            provenance = {"sourceIds": [source.id], "fetchedAt": source.fetchedAt}
            limit = model.get("limit", {})
            costs = model.get("cost", {})
            pricing = []
            if costs.get("input") is not None and costs.get("output") is not None:
                pricing.append({"priceVersion": "models-dev-" + source.contentSha256[:16],
                                "currency": "USD", "unit": "per-million-tokens",
                                "input": costs["input"], "output": costs["output"],
                                "cachedInput": costs.get("cache_read"), "observedAt": source.fetchedAt,
                                "effectiveFrom": None, "effectiveUntil": None, "provenance": provenance})
            status = model.get("status", "active")
            if status not in {"active", "preview", "deprecated", "retired"}:
                status = "unknown"
            record = {
                "identity": {"canonicalId": canonical(provider, api_id), "provider": provider,
                             "apiModelId": api_id, "aliases": [], "displayName": model.get("name", api_id),
                             "status": status},
                "facts": {"contextWindow": limit.get("context") or None,
                          "maxOutputTokens": limit.get("output") or None,
                          "capabilities": {"tools": model.get("tool_call"),
                                           "vision": "image" in modalities["input"] if "input" in modalities else None,
                                           "reasoning": model.get("reasoning"),
                                           "structuredOutput": model.get("structured_output"), "streaming": None},
                          "pricing": pricing, "provenance": provenance},
            }
            try:
                result.append(FactRow.model_validate(record))
            except ValueError:
                issues.append(f"Quarantined invalid facts: {provider}/{api_id}; see source raw SHA-256 archive")
    return result, issues


def parse_aider(raw, source):
    rows = yaml.safe_load(raw)
    if not isinstance(rows, list):
        raise ValueError("Aider export must be a list")
    evidence = []
    for row in rows:
        label = row["model"]
        env = environment(agent="aider", harness="aider-polyglot", harnessVersion=str(row.get("versions")) if row.get("versions") else None,
                          reasoningEffort=effort(label), promptMode=row.get("edit_format"), testCases=row.get("test_cases"))
        for metric in ("pass_rate_1", "pass_rate_2"):
            item = observation(source, label, "Aider Polyglot", "225-exercises", metric,
                               numeric(row.get(metric)), row["dirname"], env, date(row.get("date")), "run-date")
            if item:
                evidence.append(item)
    return evidence


def parse_evalplus(raw, source):
    root = json.loads(raw)
    evidence = []
    for label, row in root.items():
        for metric in ("humaneval+", "mbpp+"):
            item = observation(source, label, "EvalPlus", None, metric,
                               numeric(row.get("pass@1", {}).get(metric)), label,
                               environment(harness="EvalPlus", promptMode="instruct" if row.get("prompted") else "completion"))
            if item:
                evidence.append(item)
    return evidence


def csv_rows(raw, required):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if not reader.fieldnames or not set(required) <= set(reader.fieldnames):
        raise ValueError("leaderboard headers changed: " + ", ".join(required))
    return list(reader)


def parse_arena(raw, source):
    evidence = []
    for row in csv_rows(raw, ["model", "score", "date"]):
        item = observation(source, row["model"], "Arena-Hard-Auto", "v0.1", "win_rate",
                           numeric(row["score"]), row["model"], environment(harness="Arena-Hard-Auto", harnessVersion="v0.1", promptMode="LLM-judge"),
                           date(row["date"]), "leaderboard-date", low=numeric(row.get("rating_q025")), high=numeric(row.get("rating_q975")))
        if item:
            evidence.append(item)
    return evidence


def parse_bfcl(raw, source):
    evidence = []
    columns = ["Overall Acc", "Non-Live AST Acc", "Live Acc", "Multi Turn Acc", "Multi Turn Long Context", "Latency Mean (s)"]
    for row in csv_rows(raw, ["Model", *columns]):
        label = row["Model"]
        mode = "FC" if "(FC" in label else "Prompt" if "(Prompt" in label else "unknown"
        env = environment(harness="BFCL", harnessVersion="v4", reasoningEffort=effort(label), promptMode=mode)
        for metric in columns:
            item = observation(source, label, "BFCL", "v4", metric, numeric(row.get(metric)), label, env,
                               unit="seconds" if metric == "Latency Mean (s)" else "percent")
            if item:
                evidence.append(item)
    return evidence


def parse_swe(raw, source):
    boards = json.loads(raw)["leaderboards"]
    verified = [board for board in boards if board["name"] == "Verified"]
    if len(verified) != 1:
        raise ValueError("SWE-bench Verified board missing or duplicated")
    evidence = []
    for row in verified[0]["results"]:
        labels = [tag[len("Model: "):].strip() for tag in row.get("tags", []) if tag.startswith("Model: ")]
        # Multi-model submissions must never be attributed to a single model.
        label = labels[0] if len(labels) == 1 else row["name"]
        agent = row.get("agent") or "unknown"
        env = environment(agent=agent, harness=agent, harnessVersion=row.get("mini-swe-agent_version"),
                          reasoningEffort=row.get("reasoning_effort"), checked=row.get("checked") if isinstance(row.get("checked"), bool) else None)
        item = observation(source, label, "SWE-bench Verified", "verified-500", "resolved",
                           numeric(row.get("resolved")), row["folder"], env, date(row.get("date")), "leaderboard-date")
        if item:
            evidence.append(item)
    return evidence


PARSERS = {"aider": parse_aider, "evalplus": parse_evalplus, "arena": parse_arena, "bfcl": parse_bfcl, "swe": parse_swe}


def parse(config: SourceConfig, raw: bytes, observed: str, providers: list[str], upstream_version=None) -> Bundle:
    source = Source.model_validate({"id": config.id, "url": config.url, "attribution": config.attribution,
                                   "license": config.license, "contentSha256": sha(raw), "fetchedAt": observed,
                                   "checkedAt": observed, "upstreamVersion": upstream_version,
                                   "status": "fresh", "error": None})
    facts, issues = parse_facts(raw, source, providers) if config.parser == "models-dev" else ([], [])
    evidence = PARSERS[config.parser](raw, source) if config.parser in PARSERS else []
    arena_rows = []
    if config.parser == "arena-parquet":
        from .arena import parse_parquet
        arena_rows = parse_parquet(raw, source, config.subset)
    if not facts and not evidence and not arena_rows:
        raise ValueError("empty source export rejected")
    if len({item.id for item in evidence}) != len(evidence):
        raise ValueError("duplicate source observation")
    return Bundle(source=source, facts=facts, evidence=evidence, issues=issues, arenaRows=arena_rows)
