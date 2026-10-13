"""Fixed scales, documented dimension weights, no universal model score."""

import math
from datetime import datetime

from .schema import Assessment, Component, Evidence, Model, Source

POLICY_VERSION = "dimensions-1.0.0"
# Each tuple is (component key, metric name, source, weight, required harness).
POLICY = {
    "coding": [("aider", "pass_rate_2", "aider-polyglot", .5, None),
               ("humaneval-plus", "humaneval+", "evalplus", .25, None),
               ("mbpp-plus", "mbpp+", "evalplus", .25, None)],
    "chat": [("arena-hard", "win_rate", "arena-hard", 1., None)],
    "agent": [("swe-mini", "resolved", "swe-bench", .7, "mini-SWE-agent"),
              ("bfcl-multi-turn", "Multi Turn Acc", "bfcl", .3, None)],
    "tools": [("bfcl-static", "Non-Live AST Acc", "bfcl", .5, None),
              ("bfcl-live", "Live Acc", "bfcl", .5, None)],
    "latency": [("bfcl-latency", "Latency Mean (s)", "bfcl", 1., None)],
    "reasoning": [], "longContext": [],
}

NOTES = {
    "coding": "Aider two-attempt editing 50%, HumanEval+ 25%, MBPP+ 25%. Available weights are renormalized; compare coverage first.",
    "chat": "Historical Arena-Hard v0.1 machine-judge win rate; not human Chatbot Arena Elo or general chat quality.",
    "agent": "70% mini-SWE-agent Verified (reference harness), 30% BFCL V4 multi-turn. Other agent systems remain evidence only.",
    "tools": "50% BFCL V4 non-live AST, 50% live tool calling. Standard FC mode only.",
    "latency": "100/(1+BFCL mean seconds/5). BFCL harness latency only, not cross-provider API latency.",
    "context": "Declared capacity: 100*clamp(log2(tokens/4096)/8,0,1). 4k=0, 1M=100; not measured long-context quality.",
    "price": "USD cost C=input+0.25*output per million tokens; score=100/(1+C/1USD). Higher means cheaper. Excludes cache, discounts, reasoning extras and regional/tier pricing.",
    "reasoning": "Unknown: no verified reasoning benchmark adapter in this release.",
    "longContext": "Unknown: context capacity and BFCL multi-turn context are not a RULER/LongBench measurement.",
}


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assessment(components, dates, sources, generated_at, dimension):
    coverage = round(sum(c.weight for c in components), 4)
    score = round(sum(c.score * c.weight for c in components) / coverage, 2) if coverage else None
    freshness = "unknown"
    if components and len(dates) == len(components) and all(dates):
        age_limit = 30 if dimension in {"context", "price"} else 180
        expired = any(not 0 <= (instant(generated_at) - instant(value)).total_seconds() <= age_limit * 86400 for value in dates)
        source_stale = any(sources[s].status != "fresh" for c in components for s in c.sourceIds)
        freshness = "stale" if expired or source_stale else "fresh"
    return Assessment(score=score, coverage=coverage, freshness=freshness,
                      newestEvidenceAt=max((x for x in dates if x), default=None),
                      recruitmentReady=score is not None and coverage >= .5 and freshness == "fresh",
                      components=components, note=NOTES[dimension])


def score_model(model: Model, evidence: dict[str, Evidence], sources: dict[str, Source], generated_at: str):
    items = [evidence[key] for key in model.evidenceIds]
    dimensions = {}
    for dimension, rules in POLICY.items():
        components, dates = [], []
        for key, metric, source, weight, harness in rules:
            candidates = [e for e in items if e.sourceId == source and e.metric.name == metric
                          and e.environment.reasoningEffort is None
                          and (harness is None or e.environment.harness == harness)
                          and (source != "bfcl" or e.environment.promptMode == "FC")]
            if not candidates:
                continue
            # Latest reported run, then stable evidence ID. Never select the highest score.
            selected = max(candidates, key=lambda e: (e.reportedAt or "", e.id))
            value = selected.metric.value
            normalized = 100 / (1 + value / 5) if selected.metric.unit == "seconds" else value
            components.append(Component(key=key, score=round(normalized, 4), weight=weight,
                                        evidenceIds=[selected.id], sourceIds=[source]))
            dates.append(selected.reportedAt)
        dimensions[dimension] = assessment(components, dates, sources, generated_at, dimension)

    fact_sources = model.facts.provenance.sourceIds
    fact_date = min(sources[s].checkedAt for s in fact_sources)
    capacity = model.facts.contextWindow
    components = []
    if capacity:
        value = 100 * max(0, min(1, math.log2(capacity / 4096) / 8))
        components = [Component(key="capacity", score=round(value, 4), weight=1., evidenceIds=[], sourceIds=fact_sources)]
    dimensions["context"] = assessment(components, [fact_date] if components else [], sources, generated_at, "context")
    pricing = model.facts.pricing
    components = []
    if pricing:
        latest = max(enumerate(pricing), key=lambda pair: (pair[1].observedAt, pair[0]))[1]
        cost = latest.input + .25 * latest.output
        components = [Component(key="usd-1m-in-250k-out", score=round(100 / (1 + cost), 4), weight=1., evidenceIds=[], sourceIds=latest.provenance.sourceIds)]
    dimensions["price"] = assessment(components, [fact_date] if components else [], sources, generated_at, "price")
    model.dimensions = dimensions


def policy_document():
    return {"version": POLICY_VERSION, "scale": "0..100, higher is better", "universalScore": False,
            "dimensions": {key: {"note": value, "weights": [{"component": r[0], "metric": r[1], "source": r[2], "weight": r[3], "harness": r[4]} for r in POLICY.get(key, [])]} for key, value in NOTES.items()},
            "unknown": "null, never zero", "benchmarkMaxAgeDays": 180, "factsMaxAgeDays": 30,
            "defaultVariant": "No explicit reasoning effort; BFCL FC only. Preserve every other variant in evidence.",
            "selection": "Newest reported date then evidence ID, never maximum score. Unknown-date benchmark cannot be recruitmentReady."}
