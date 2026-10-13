from pathlib import Path
from html import escape

from .matching import Mapping, source_match
from .schema import Capabilities, Facts, Feed, Identity, Manifest, Model, Provenance
from .scoring import POLICY_VERSION, policy_document, score_model
from .sources import Bundle
from .util import atomic_write, json_bytes, sha


def build_feed(bundles: list[Bundle], mapping: Mapping, generated_at: str) -> Feed:
    models, evidence = {}, {}
    source_list = sorted((bundle.source for bundle in bundles), key=lambda x: x.id)
    for bundle in bundles:
        for row in bundle.facts:
            key = row.identity.canonicalId
            if key in models:
                raise ValueError("multiple fact owners for model " + key)
            identity = row.identity.model_copy(deep=True)
            identity.aliases = mapping.clientAliases.get(key, [])
            models[key] = Model(identity=identity, facts=row.facts.model_copy(deep=True), evidenceIds=[], dimensions={}, provenance=row.facts.provenance)
    # A reviewed label can identify an old snapshot no longer listed by models.dev.
    # Its specifications stay unknown, rather than borrowing a floating model's facts.
    for bundle in bundles:
        labels = {e.sourceModelLabel for e in bundle.evidence}
        for label, key in mapping.sourceLabels.get(bundle.source.id, {}).items():
            if label not in labels or key in models:
                continue
            provider, api_id = key.split("/", 1)
            provenance = Provenance(sourceIds=[bundle.source.id], fetchedAt=bundle.source.fetchedAt)
            identity = Identity(canonicalId=key, provider=provider, apiModelId=api_id,
                                aliases=mapping.clientAliases.get(key, []), displayName=api_id, status="unknown")
            models[key] = Model(identity=identity, facts=Facts(contextWindow=None, maxOutputTokens=None,
                                capabilities=Capabilities(tools=None, vision=None, reasoning=None, structuredOutput=None, streaming=None),
                                pricing=[], provenance=provenance), evidenceIds=[], dimensions={}, provenance=provenance)
    identities = [m.identity for m in models.values()]
    for bundle in bundles:
        for observation in bundle.evidence:
            item = observation.model_copy(deep=True)
            item.modelId, item.mappingStatus = source_match(identities, mapping, item.sourceId, item.sourceModelLabel)
            if item.id in evidence:
                raise ValueError("duplicate evidence across source bundles")
            evidence[item.id] = item
            if item.modelId:
                model = models[item.modelId]
                model.evidenceIds.append(item.id)
                model.provenance = Provenance(sourceIds=sorted(set(model.provenance.sourceIds) | {item.sourceId}),
                                              fetchedAt=max(model.provenance.fetchedAt, item.observedAt))
    for model in models.values():
        model.evidenceIds.sort()
        score_model(model, evidence, {s.id: s for s in source_list}, generated_at)
        if model.identity.status in {"unknown", "deprecated", "retired"}:
            for dimension in model.dimensions.values():
                dimension.recruitmentReady = False
    if not models:
        raise ValueError("no identified models; refusing empty registry")
    version_input = {"bundles": [b.model_dump(mode="json") for b in sorted(bundles, key=lambda b: b.source.id)],
                     "mapping": mapping.model_dump(), "policy": policy_document(), "generatedAt": generated_at}
    return Feed(schemaVersion=1, catalogVersion="v1-" + sha(json_bytes(version_input))[:24], generatedAt=generated_at,
                scoringPolicyVersion=POLICY_VERSION, mappingVersion=mapping.version, sources=source_list, models=models, evidence=evidence)


def camelot_projection(feed: Feed):
    models = {}
    for key, model in feed.models.items():
        if model.facts.contextWindow is None or model.identity.status == "unknown":
            continue
        identity = model.identity.model_dump()
        del identity["displayName"]
        observations = []
        for evidence_id in model.evidenceIds:
            item = feed.evidence[evidence_id]
            if item.evaluatedAt is None:
                continue
            observations.append({"id": item.id, "benchmark": item.benchmark, "benchmarkVersion": item.benchmarkVersion,
                                 "metric": item.metric.name, "score": item.metric.value, "evaluatedAt": item.evaluatedAt,
                                 "environment": {"agent": item.environment.agent, "harness": item.environment.harness,
                                                 "harnessVersion": item.environment.harnessVersion,
                                                 "reasoningEffort": item.environment.reasoningEffort, "promptSha256": None},
                                 "sourceId": item.sourceId, "confidence": "medium"})
        models[key] = {"identity": identity, "facts": model.facts.model_dump(), "evidence": observations, "provenance": model.provenance.model_dump()}
    if not models:
        raise ValueError("no models satisfy Camelot v1's non-null facts contract")
    # No profile is silently substituted for Camelot's local role/recruitment policy.
    return {"schemaVersion": 1, "catalogVersion": feed.catalogVersion, "generatedAt": feed.generatedAt,
            "sources": [{"id": s.id, "kind": "aggregate" if s.id == "models-dev" else "benchmark", "uri": s.url,
                         "version": s.contentSha256, "fetchedAt": s.fetchedAt} for s in feed.sources],
            "models": models, "profiles": {}}


def publish_snapshot(directory: Path, payload, generated_at, version):
    raw = json_bytes(payload)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("snapshot exceeds Camelot client 4MiB limit")
    digest = sha(raw)
    manifest = Manifest(schemaVersion=1, catalogVersion=version, generatedAt=generated_at,
                        modelCount=len(payload["models"]), snapshot=f"snapshots/{digest}.json", hashAlgorithm="sha256", hash=digest)
    atomic_write(directory / manifest.snapshot, raw)
    atomic_write(directory / "manifest.json", json_bytes(manifest.model_dump()))
    # Keep the latest seven immutable versions in a publication. Raw caches are separate.
    files = sorted((directory / "snapshots").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[7:]:
        if path.name != digest + ".json":
            path.unlink()
    return manifest


def publish(feed: Feed, output: Path, failures, source_issues=None):
    rich = publish_snapshot(output / "api" / "v1", feed.model_dump(mode="json"), feed.generatedAt, feed.catalogVersion)
    compat = publish_snapshot(output / "api" / "camelot-v1", camelot_projection(feed), feed.generatedAt, feed.catalogVersion)
    schema_dir = output / "schema"
    atomic_write(schema_dir / "registry.schema.json", json_bytes(Feed.model_json_schema()))
    atomic_write(schema_dir / "manifest.schema.json", json_bytes(Manifest.model_json_schema()))
    atomic_write(output / "policy.json", json_bytes(policy_document()))
    unmatched = [{"sourceId": e.sourceId, "label": e.sourceModelLabel, "status": e.mappingStatus, "evidenceId": e.id}
                 for e in feed.evidence.values() if e.modelId is None]
    atomic_write(output / "unmatched.json", json_bytes(sorted(unmatched, key=lambda e: (e["sourceId"], e["label"], e["evidenceId"]))))
    report = {"catalogVersion": feed.catalogVersion, "generatedAt": feed.generatedAt, "models": len(feed.models),
              "observations": len(feed.evidence), "matchedObservations": sum(e.modelId is not None for e in feed.evidence.values()),
              "sourceStatus": {s.id: s.status for s in feed.sources}, "failures": failures,
              "sourceWarnings": source_issues or {},
              "richSnapshotBytes": len(json_bytes(feed.model_dump(mode="json"))), "camelotModels": compat.modelCount,
              "richHash": rich.hash, "camelotHash": compat.hash}
    atomic_write(output / "report.json", json_bytes(report))
    index = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Model Observatory</title></head><body>
<h1>Model Observatory</h1>
<p>{escape(feed.catalogVersion)} | {escape(feed.generatedAt)}</p>
<p>{len(feed.models)} models | {len(feed.evidence)} observations | {report["matchedObservations"]} matched observations</p>
<ul>
<li><a href="api/v1/manifest.json">Registry manifest</a></li>
<li><a href="api/camelot-v1/manifest.json">Camelot v1 projection manifest</a></li>
<li><a href="schema/registry.schema.json">Registry JSON Schema</a></li>
<li><a href="schema/manifest.schema.json">Manifest JSON Schema</a></li>
<li><a href="policy.json">Dimension scoring policy</a></li>
<li><a href="report.json">Source health and validation report</a></li>
<li><a href="unmatched.json">Unmatched observations</a></li>
</ul></body></html>
'''
    atomic_write(output / "index.html", index.encode("utf-8"))
    return report
