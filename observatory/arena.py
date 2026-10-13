"""Official CC-BY-4.0 Parquet exports; names and inference settings stay separate."""
import io
import math
import re
from collections import defaultdict
from datetime import datetime

import pyarrow.parquet as pq

from .arena_schema import ArenaObservation, ArenaScore, Inference, SeerTable, TableRow
from .schema import Model, Source
from .util import json_bytes, sha

TEXT_CATEGORIES = {"overall": "chat", "coding": "coding", "math": "math",
                   "instruction_following": "instructionFollowing", "multi_turn": "multiTurn",
                   "creative_writing": "creativeWriting"}
SUBSETS = {"text_style_control", "vision_style_control", "webdev", "agent",
           "agent_task_outcome_explicit", "agent_steerability", "agent_tool_hallucination",
           "agent_bash_reliability_no_prereq"}
PROVIDERS = {"openai": "openai", "anthropic": "anthropic", "google": "google",
             "deepseek": "deepseek", "alibaba": "alibaba", "qwen": "alibaba",
             "mistral": "mistral", "xai": "xai", "x.ai": "xai", "spacexai": "xai",
             "moonshot": "moonshotai", "moonshotai": "moonshotai", "minimax": "minimax",
             "z.ai": "zai", "zhipu": "zai", "zai": "zai"}


def count(value):
    if value is None:
        return None
    if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0 or not float(value).is_integer():
        raise ValueError("invalid Arena sample count")
    return int(value)


def parse_parquet(raw: bytes, source: Source, subset: str | None):
    if subset not in SUBSETS or len(raw) > 2000000 or not raw.startswith(b"PAR1"):
        raise ValueError("unsupported or oversized Arena Parquet export")
    parquet = pq.ParquetFile(io.BytesIO(raw))
    if parquet.metadata.num_rows > 20000 or sum(parquet.metadata.row_group(i).total_byte_size for i in range(parquet.metadata.num_row_groups)) > 16000000:
        raise ValueError("Arena export exceeds decoded bounds")
    agent = subset.startswith("agent")
    required = ["model_name", "organization", "rank", "category", "leaderboard_publish_date"]
    required += ["score", "score_ci_lower", "score_ci_upper", "observation_count", "session_count"] if agent else ["rating", "rating_lower", "rating_upper", "vote_count"]
    if not set(required) <= set(parquet.schema.names):
        raise ValueError("Arena export columns changed")
    rows = parquet.read(columns=required).to_pylist()
    result = []
    for position, row in enumerate(rows):
        if subset == "text_style_control" and row["category"] not in TEXT_CATEGORIES:
            continue
        if subset in {"vision_style_control", "webdev"} and row["category"] != "overall":
            continue
        category = TEXT_CATEGORIES[row["category"]] if subset == "text_style_control" else {
            "vision_style_control": "vision", "webdev": "webdev", "agent": "agent",
            "agent_task_outcome_explicit": "agentTaskOutcome", "agent_steerability": "agentSteerability",
            "agent_tool_hallucination": "agentToolHallucination", "agent_bash_reliability_no_prereq": "agentBashReliability",
        }[subset]
        published = row["leaderboard_publish_date"]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
            raise ValueError("invalid Arena publication date")
        result.append(ArenaObservation.model_validate({
            "id": "ar-" + sha(json_bytes([source.id, position, row]))[:32], "sourceId": source.id,
            "modelName": row["model_name"], "organization": row["organization"], "category": category,
            "metric": "ips" if agent else "bradley-terry", "value": row["score" if agent else "rating"],
            "lower": row["score_ci_lower" if agent else "rating_lower"], "upper": row["score_ci_upper" if agent else "rating_upper"],
            "rank": row["rank"], "votes": None if agent else count(row["vote_count"]),
            "observations": count(row["observation_count"]) if agent else None,
            "sessions": count(row["session_count"]) if agent else None, "publishedAt": published + "T00:00:00Z",
        }))
    if not result:
        raise ValueError("Arena export has no expected category rows")
    return result


def infer(name: str):
    """Parse documented display suffixes; never infer numeric token budgets from k labels."""
    base = name.strip()
    harness = None
    match = re.search(r" \(([^)]+-harness)\)$", base, re.I)
    if match:
        harness, base = match[1].lower(), base[:match.start()]
    inference = Inference(effort="unspecified", budget=None, harness=harness)
    match = re.search(r"(?:-| \()(thinking[- ]\d+k|thinking[- ]minimal|non[- ]reasoning|non[- ]thinking|xhigh|high|medium|low|max)(?:[- ](\d+k))?\)?$", base, re.I)
    if match:
        mode = match[1].lower().replace(" ", "-")
        inference.effort = "none" if mode.startswith("non-") else "thinking" if mode.startswith("thinking-") else mode
        inference.budget = mode[len("thinking-"):] if mode.startswith("thinking-") else match[2]
        base = base[:match.start()]
    elif re.search(r"(?:thinking|non-thinking|high|medium|xhigh|max)", base, re.I):
        # An unrecognized decorated label is never treated as the serving default.
        # Native API IDs are admitted separately by bind's exact-ID check.
        inference.effort = "unknown"
    return base, inference


def bind(name, provider, models: dict[str, Model], aliases):
    base, inference = infer(name)
    exact = [m for m in models.values() if m.identity.provider == provider and m.identity.apiModelId == name]
    # Some API IDs themselves contain reasoning words; exact IDs take precedence.
    if len(exact) == 1:
        return exact[0], Inference(effort="unspecified", budget=None, harness=None), "matched"
    display_id = re.sub(r"\s+", "-", base).casefold()
    reviewed = aliases.get(f"{provider}/{display_id}")
    matches = [m for m in models.values() if m.identity.provider == provider and (
        m.identity.canonicalId == reviewed if reviewed else m.identity.apiModelId.casefold() == display_id)]
    if len(matches) == 1:
        return matches[0], inference, "matched"
    return None, inference, "ambiguous" if len(matches) > 1 else "unmatched"


def build_table(bundles, feed, aliases):
    groups = defaultdict(list)
    bindings = {}
    for bundle in bundles:
        for item in bundle.arenaRows:
            provider = PROVIDERS.get(item.organization.lower().replace(" ", ""))
            model, inference, binding = bind(item.modelName, provider, feed.models, aliases)
            key = json_bytes([model.identity.canonicalId if model else [provider, item.modelName], inference.model_dump()])
            groups[key].append(item)
            bindings[key] = (provider, model, inference, binding)
    rows = []
    sources = {s.id: s for s in feed.sources}
    for key, observations in sorted(groups.items()):
        provider, model, inference, binding = bindings[key]
        name = min((item.modelName for item in observations), key=lambda label: (" " in label, label))
        by_category = defaultdict(list)
        for item in observations:
            by_category[item.category].append(item)
        scores = {}
        for category, candidates in by_category.items():
            latest = max(item.publishedAt for item in candidates)
            current = [item for item in candidates if item.publishedAt == latest]
            if len(current) != 1:
                # The export cannot disambiguate duplicate contender labels.
                # No score is selected from conflicting rows by rank or magnitude.
                continue
            item = current[0]
            age = datetime.fromisoformat(feed.generatedAt.replace("Z", "+00:00")) - datetime.fromisoformat(item.publishedAt.replace("Z", "+00:00"))
            fresh = 0 <= age.total_seconds() <= 30 * 86400 and sources[item.sourceId].status == "fresh"
            scores[category] = ArenaScore(sourceId=item.sourceId, modelName=item.modelName, metric=item.metric, value=item.value,
                lower=item.lower, upper=item.upper, rank=item.rank, votes=item.votes, observations=item.observations,
                sessions=item.sessions, publishedAt=item.publishedAt, freshness="fresh" if fresh else "stale")
        facts = model.facts.model_copy(deep=True) if model else None
        if facts:
            facts.pricing = facts.pricing[-1:]
        rows.append(TableRow(variantId=sha(key), arenaName=name,
            provider=provider, canonicalId=model.identity.canonicalId if model else None,
            apiModelId=model.identity.apiModelId if model else None, binding=binding, inference=inference,
            facts=facts, scores=scores))
    if not rows:
        raise ValueError("no Arena rows; refusing empty public table")
    payload = {"schemaVersion": 2, "catalogVersion": "pending", "generatedAt": feed.generatedAt,
               "policyVersion": "arena-variants-1", "sources": [s.model_dump() | {"error": None} for s in feed.sources],
               "rows": [r.model_dump() for r in rows]}
    payload["catalogVersion"] = "v2-" + sha(json_bytes(payload))[:24]
    return SeerTable.model_validate(payload)
