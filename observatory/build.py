from pathlib import Path
from html import escape

from .matching import Mapping, source_match
from .schema import Capabilities, Facts, Feed, Identity, Manifest, Model, Provenance
from .scoring import POLICY_VERSION, policy_document, score_model
from .sources import Bundle
from .util import atomic_write, json_bytes, sha

PROJECTION_POLICY = {"version": "camelot-profiles-1", "dimensions": {
    "coder": "coding", "architect": "reasoning", "reviewer": "coding"},
    "fallback": "stable canonical ID order; unknown/stale scores excluded from score ordering"}


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
                     "mapping": mapping.model_dump(), "policy": policy_document(),
                     "projectionPolicy": PROJECTION_POLICY, "generatedAt": generated_at}
    return Feed(schemaVersion=1, catalogVersion="v1-" + sha(json_bytes(version_input))[:24], generatedAt=generated_at,
                scoringPolicyVersion=POLICY_VERSION, mappingVersion=mapping.version, sources=source_list, models=models, evidence=evidence)


def camelot_projection(feed: Feed, table=None):
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
        facts = model.facts.model_dump()
        facts["pricing"] = facts["pricing"][-1:]
        models[key] = {"identity": identity, "facts": facts, "evidence": observations, "provenance": model.provenance.model_dump()}
    if not models:
        raise ValueError("no models satisfy Camelot v1's non-null facts contract")
    profiles = {}
    for profile_id, dimension in PROJECTION_POLICY["dimensions"].items():
        candidates = [key for key, model in models.items() if model["identity"]["status"] in {"active", "preview"}]
        def rank_key(key):
            assessment = feed.models[key].dimensions[dimension]
            score = assessment.score if assessment.recruitmentReady else None
            return (score is None, -(assessment.coverage if score is not None else 0), -(score or 0), key)
        ranking = sorted(candidates, key=rank_key)
        if table is not None:
            arena_dimension = {"coder": "coding", "reviewer": "coding", "architect": "math"}[profile_id]
            eligible_rows = [row for row in table.rows if row.canonicalId in candidates
                             and row.inference.effort == "unspecified" and row.inference.budget is None
                             and row.inference.harness is None and arena_dimension in row.scores
                             and row.scores[arena_dimension].freshness == "fresh"
                             and (row.scores[arena_dimension].votes or 0) >= 100]
            ranked = [row.canonicalId for row in sorted(eligible_rows, key=lambda row: (-row.scores[arena_dimension].value, row.variantId))]
            ranking = list(dict.fromkeys(ranked + ranking))
        if not ranking:
            continue
        source_ids = sorted({source for key in ranking for source in feed.models[key].provenance.sourceIds})
        profiles[profile_id] = {"id": profile_id, "ranking": ranking,
                                "policyVersion": "arena-unspecified-bootstrap-1" if table is not None else PROJECTION_POLICY["version"], "basis": "bootstrap",
                                "evidenceIds": [], "hardRequirements": {},
                                "provenance": {"sourceIds": source_ids, "fetchedAt": feed.generatedAt}}
    # Rankings remain advisory: ModelRegistry retains local Seed Profile floors,
    # and ProviderHub intersects them with deployment-owned adapters and credentials.
    return {"schemaVersion": 1, "catalogVersion": feed.catalogVersion, "generatedAt": feed.generatedAt,
            "sources": [{"id": s.id, "kind": "aggregate" if s.id == "models-dev" else "benchmark", "uri": s.url,
                         "version": s.contentSha256, "fetchedAt": s.fetchedAt} for s in feed.sources],
            "models": models, "profiles": profiles}


def publish_snapshot(directory: Path, payload, generated_at, version):
    raw = json_bytes(payload)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("snapshot exceeds Camelot client 4MiB limit")
    digest = sha(raw)
    manifest = Manifest(schemaVersion=1, catalogVersion=version, generatedAt=generated_at,
                        modelCount=len(payload.get("models", payload.get("rows", []))), snapshot=f"snapshots/{digest}.json", hashAlgorithm="sha256", hash=digest)
    atomic_write(directory / manifest.snapshot, raw)
    atomic_write(directory / "manifest.json", json_bytes(manifest.model_dump()))
    # Keep the latest seven immutable versions in a publication. Raw caches are separate.
    files = sorted((directory / "snapshots").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[7:]:
        if path.name != digest + ".json":
            path.unlink()
    return manifest


def public_files(output: Path):
    """Reject accidental output disclosure before the Pages artifact is uploaded."""
    allowed = {"api/v2/manifest.json", "api/camelot-v1/manifest.json"}
    for entry in output.rglob("*"):
        if entry.is_symlink():
            raise ValueError("publication cannot contain symbolic links")
    for folder in ["api/v2/snapshots", "api/camelot-v1/snapshots"]:
        for file in (output / folder).glob("*.json"):
            if len(file.stem) != 64 or any(c not in "0123456789abcdef" for c in file.stem):
                raise ValueError("invalid public snapshot name")
            allowed.add(file.relative_to(output).as_posix())
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    if actual != allowed:
        raise ValueError("publication contains unexpected or missing files: " + str(sorted(actual ^ allowed)))
    return sorted(actual)


def publish(feed: Feed, table, output: Path, failures, source_issues=None):
    obsolete_page = output / "index.html"
    if obsolete_page.exists() and "<title>Camelot-SEER</title>" in obsolete_page.read_text(encoding="utf-8"):
        obsolete_page.unlink()
    rich = publish_snapshot(output / "api" / "v2", table.model_dump(mode="json"), feed.generatedAt, feed.catalogVersion)
    compat = publish_snapshot(output / "api" / "camelot-v1", camelot_projection(feed, table), feed.generatedAt, feed.catalogVersion)
    public_files(output)
    return {"catalogVersion": feed.catalogVersion, "generatedAt": feed.generatedAt,
            "variants": len(table.rows), "matchedVariants": sum(r.binding == "matched" for r in table.rows),
            "coverage": {category: sum(category in r.scores for r in table.rows)
                         for category in sorted({c for r in table.rows for c in r.scores})},
            "sourceStatus": {s.id: s.status for s in feed.sources}, "failures": failures,
            "sourceWarnings": source_issues or {}, "tableSnapshotBytes": len(json_bytes(table.model_dump(mode="json"))),
            "camelotModels": compat.modelCount, "tableHash": rich.hash, "camelotHash": compat.hash}
