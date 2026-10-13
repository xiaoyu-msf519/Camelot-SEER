import asyncio
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web

from observatory.build import build_feed, camelot_projection, publish_snapshot
from observatory.client import RegistryClient
from observatory.collector import bounded_get, collect, preserve_price_history, read_previous, validate_envelope
from observatory.matching import Mapping, client_match, source_match
from observatory.schema import Feed, Manifest
from observatory.scoring import score_model
from observatory.sources import Config, parse
from observatory.util import json_bytes, load_json, sha

ROOT = Path(__file__).resolve().parent.parent
AS_OF = "2026-10-13T06:00:00Z"


def seed_inputs():
    config = Config.model_validate(load_json(ROOT / "config/sources.json"))
    mapping = Mapping.model_validate(load_json(ROOT / "config/aliases.json"))
    bundles = [validate_envelope(load_json(ROOT / "seed" / (s.id + ".json")), s) for s in config.sources]
    return config, mapping, bundles


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config, cls.mapping, cls.bundles = seed_inputs()
        cls.feed = build_feed(cls.bundles, cls.mapping, AS_OF)

    def test_reproducible_feed_and_manifest(self):
        second = build_feed(self.bundles, self.mapping, AS_OF)
        self.assertEqual(json_bytes(self.feed.model_dump()), json_bytes(second.model_dump()))
        with tempfile.TemporaryDirectory() as temp:
            payload = self.feed.model_dump()
            first = publish_snapshot(Path(temp), payload, AS_OF, self.feed.catalogVersion)
            again = publish_snapshot(Path(temp), payload, AS_OF, self.feed.catalogVersion)
            self.assertEqual(first, again)
            RegistryClient.verify(first.model_dump(), (Path(temp) / first.snapshot).read_bytes())

    def test_tampered_snapshot_and_wrong_counts_rejected(self):
        raw = json_bytes(self.feed.model_dump())
        manifest = Manifest(schemaVersion=1, catalogVersion=self.feed.catalogVersion, generatedAt=AS_OF,
                            modelCount=len(self.feed.models), snapshot="snapshots/test.json", hashAlgorithm="sha256", hash=sha(raw))
        with self.assertRaises(ValueError):
            RegistryClient.verify(manifest.model_dump(), raw + b" ")
        wrong = manifest.model_dump() | {"modelCount": manifest.modelCount + 1}
        with self.assertRaises(ValueError):
            RegistryClient.verify(wrong, raw)

    def test_remote_execution_authority_cannot_enter_schema(self):
        for field in ("apiKey", "endpointOverride", "toolPermissions", "filesystemPermissions", "adapter", "code"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                Feed.model_validate(self.feed.model_dump() | {field: "malicious"})
        malicious = self.feed.model_dump()
        malicious["models"]["openai/gpt-4o"]["facts"]["capabilities"]["dispatch_worker_task"] = True
        with self.assertRaises(ValueError):
            Feed.model_validate(malicious)

    def test_reference_firewall(self):
        data = self.feed.model_dump()
        data["models"]["openai/gpt-4o"]["facts"]["provenance"]["sourceIds"] = ["made-up-source"]
        with self.assertRaises(ValueError):
            Feed.model_validate(data)

    def test_unknown_is_not_zero(self):
        model = self.feed.models["openai/gpt-4o"]
        self.assertIsNone(model.dimensions["reasoning"].score)
        self.assertIsNone(model.dimensions["longContext"].score)
        self.assertEqual(model.dimensions["reasoning"].coverage, 0)
        self.assertFalse(model.dimensions["reasoning"].recruitmentReady)

    def test_context_capacity_not_benchmark_quality(self):
        model = self.feed.models["openai/gpt-4o"]
        self.assertIsNotNone(model.dimensions["context"].score)
        self.assertIsNone(model.dimensions["longContext"].score)
        self.assertEqual(model.dimensions["context"].components[0].evidenceIds, [])

    def test_prices_have_observation_and_unknown_effective_dates(self):
        price = self.feed.models["openai/gpt-4o"].facts.pricing[-1]
        self.assertEqual(price.currency, "USD")
        self.assertIsNone(price.effectiveFrom)
        self.assertIsNotNone(price.priceVersion)
        self.assertIsNotNone(price.observedAt)

    def test_price_changes_preserve_observation_history(self):
        bundle = next(b for b in self.bundles if b.source.id == "models-dev")
        changed = bundle.model_copy(deep=True)
        row = next(r for r in changed.facts if r.identity.apiModelId == "gpt-4o")
        original = row.facts.pricing[-1].model_copy(deep=True)
        row.facts.pricing[-1].input = original.input + 1.
        row.facts.pricing[-1].observedAt = AS_OF
        preserve_price_history(changed, bundle)
        self.assertEqual(len(row.facts.pricing), 2)
        self.assertEqual(row.facts.pricing[0], original)
        self.assertGreater(row.facts.pricing[-1].input, original.input)
        repeated = changed.model_copy(deep=True)
        repeated.facts = [r.model_copy(deep=True) for r in changed.facts]
        target = next(r for r in repeated.facts if r.identity.apiModelId == "gpt-4o")
        target.facts.pricing = [target.facts.pricing[-1]]
        preserve_price_history(repeated, changed)
        self.assertEqual(len(target.facts.pricing), 2)

    def test_missing_price_is_unknown_and_declared_zero_is_free(self):
        model = self.feed.models["openai/gpt-4o"].model_copy(deep=True)
        for p in model.facts.pricing:
            p.input, p.output = 0., 0.
        sources = {s.id: s for s in self.feed.sources}
        score_model(model, self.feed.evidence, sources, AS_OF)
        self.assertEqual(model.dimensions["price"].score, 100.)
        model.facts.pricing = []
        score_model(model, self.feed.evidence, sources, AS_OF)
        self.assertIsNone(model.dimensions["price"].score)

    def test_client_rechecks_expired_and_unrecognized_policy(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as temp:
            client = RegistryClient("https://example.com/manifest.json", Path(temp) / "cache.json")
            client.feed = self.feed.model_copy(deep=True)
            checked_at = datetime.fromisoformat(AS_OF.replace("Z", "+00:00"))
            model = client.match("openai", "gpt-4o", as_of=checked_at)
            self.assertTrue(model.dimensions["price"].recruitmentReady)
            expired = client.match("openai", "gpt-4o", as_of=datetime(2027, 1, 1, tzinfo=timezone.utc))
            self.assertFalse(expired.dimensions["price"].recruitmentReady)
            self.assertEqual(expired.dimensions["price"].freshness, "stale")
            client.feed.scoringPolicyVersion = "remote-unknown-policy"
            model = client.match("openai", "gpt-4o", as_of=checked_at)
            self.assertFalse(model.dimensions["price"].recruitmentReady)

    def test_identity_exact_and_snapshot_distinct(self):
        identities = [m.identity for m in self.feed.models.values()]
        self.assertEqual(client_match(identities, "openai", "gpt-4o"), "openai/gpt-4o")
        self.assertEqual(client_match(identities, "openai", "gpt-4o-2024-08-06"), "openai/gpt-4o-2024-08-06")
        self.assertIsNone(client_match(identities, "anthropic", "gpt-4o"))
        self.assertIsNone(client_match(identities, "openai", "GPT-4o"))
        self.assertIsNone(client_match(identities, "openai", "gpt-4o-unknown-date"))

    def test_ambiguous_source_identity_is_not_assigned(self):
        first = self.feed.models["openai/gpt-4o"].identity
        second = first.model_copy(update={"canonicalId": "another/gpt-4o", "provider": "another"})
        key, status = source_match([first, second], self.mapping, "arena-hard", "gpt-4o")
        self.assertIsNone(key)
        self.assertEqual(status, "ambiguous")
        second = first.model_copy(update={"canonicalId": "openai/other"})
        with self.assertRaises(ValueError):
            client_match([first, second], "openai", "gpt-4o")

    def test_compatibility_does_not_invent_eval_dates_or_facts(self):
        projected = camelot_projection(self.feed)
        self.assertEqual(set(projected["profiles"]), {"coder", "architect", "reviewer"})
        for profile in projected["profiles"].values():
            self.assertEqual(profile["basis"], "bootstrap")
            self.assertEqual(profile["hardRequirements"], {})
            self.assertTrue(profile["ranking"])
            self.assertEqual(len(set(profile["ranking"])), len(profile["ranking"]))
            self.assertTrue(set(profile["ranking"]) <= set(projected["models"]))
            self.assertFalse(any(projected["models"][key]["identity"]["status"] in {"retired", "deprecated"} for key in profile["ranking"]))
        for key, model in projected["models"].items():
            self.assertGreater(model["facts"]["contextWindow"], 0)
            self.assertNotIn("displayName", model["identity"])
            for item in model["evidence"]:
                original = self.feed.evidence[item["id"]]
                self.assertEqual(item["evaluatedAt"], original.evaluatedAt)
                self.assertEqual(original.dateMeaning, "run-date")
        unknown_fact_models = {k for k, v in self.feed.models.items() if v.facts.contextWindow is None}
        self.assertTrue(unknown_fact_models)
        self.assertFalse(unknown_fact_models & set(projected["models"]))

    def test_latest_run_selected_not_highest_or_high_effort(self):
        model = self.feed.models["openai/gpt-4o-2024-08-06"].model_copy(deep=True)
        original = next(e for e in self.feed.evidence.values() if e.modelId == model.identity.canonicalId and e.metric.name == "pass_rate_2")
        old = original.model_copy(deep=True)
        old.id, old.reportedAt, old.metric.value = "old-high", "2025-01-01T00:00:00Z", 99.
        recent = original.model_copy(deep=True)
        recent.id, recent.reportedAt, recent.metric.value = "new-low", "2026-10-01T00:00:00Z", 20.
        high = original.model_copy(deep=True)
        high.id, high.reportedAt, high.metric.value = "high-effort", "2026-10-02T00:00:00Z", 100.
        high.environment.reasoningEffort = "high"
        model.evidenceIds = [old.id, recent.id, high.id]
        score_model(model, {e.id: e for e in [old, recent, high]}, {s.id: s for s in self.feed.sources}, AS_OF)
        coding = model.dimensions["coding"]
        self.assertEqual(coding.score, 20.)
        self.assertEqual(coding.coverage, .5)
        self.assertEqual(coding.components[0].evidenceIds, [recent.id])

    def test_harness_boundary_for_agent_score(self):
        model = self.feed.models["openai/gpt-4o-2024-08-06"].model_copy(deep=True)
        original = next(e for e in self.feed.evidence.values() if e.sourceId == "swe-bench")
        foreign = original.model_copy(deep=True)
        foreign.modelId = model.identity.canonicalId
        foreign.environment.harness = "uncontrolled-agent"
        model.evidenceIds = [foreign.id]
        score_model(model, {foreign.id: foreign}, {s.id: s for s in self.feed.sources}, AS_OF)
        self.assertIsNone(model.dimensions["agent"].score)

    def test_source_drift_and_malicious_yaml_rejected(self):
        bfcl = next(c for c in self.config.sources if c.id == "bfcl")
        with self.assertRaises(ValueError):
            parse(bfcl, b"Model,Score\ngpt-4o,99\n", AS_OF, self.config.providers)
        aider = next(c for c in self.config.sources if c.id == "aider-polyglot")
        import yaml
        with self.assertRaises(yaml.YAMLError):
            parse(aider, b"!!python/object/apply:os.system ['echo malicious']", AS_OF, self.config.providers)

    def test_invalid_facts_quarantined_without_losing_valid_rows(self):
        source = next(c for c in self.config.sources if c.id == "models-dev")
        raw = json_bytes({"openai": {"models": {
            "good": {"limit": {"context": 128000, "output": 1000}},
            "bad": {"limit": {"context": 1000, "output": 2000}},
        }}})
        bundle = parse(source, raw, AS_OF, ["openai"])
        self.assertEqual([f.identity.apiModelId for f in bundle.facts], ["good"])
        self.assertEqual(len(bundle.issues), 1)
        self.assertIn("openai/bad", bundle.issues[0])

    def test_cache_corruption_returns_seed_and_publisher_bound(self):
        source = self.config.sources[0]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(ROOT / "seed", root / "seed")
            (root / ".cache").mkdir()
            (root / ".cache" / (source.id + ".json")).write_text('{"bad":true}', encoding="utf-8")
            bundle, origin = read_previous(root, source)
            self.assertEqual(origin, "seed")
            changed = source.model_copy(update={"url": "https://example.com/other"})
            with self.assertRaises(ValueError):
                validate_envelope(load_json(root / "seed" / (source.id + ".json")), changed)

    def test_manifest_path_escape_rejected(self):
        template = {"schemaVersion": 1, "catalogVersion": "v1", "generatedAt": AS_OF, "modelCount": 1,
                    "hashAlgorithm": "sha256", "hash": "a" * 64}
        for path in ("../bad.json", "/bad.json", "https://evil.test/data.json", "x//a.json", "x/./a.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                Manifest.model_validate(template | {"snapshot": path})


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config, self.mapping, self.bundles = seed_inputs()
        self.feed = build_feed(self.bundles, self.mapping, AS_OF)
        self.raw = json_bytes(self.feed.model_dump())
        self.manifest = Manifest(schemaVersion=1, catalogVersion=self.feed.catalogVersion, generatedAt=AS_OF,
                                 modelCount=len(self.feed.models), snapshot="snapshot.json", hashAlgorithm="sha256", hash=sha(self.raw))
        self.mode = "ok"
        app = web.Application()
        app.router.add_get("/manifest.json", self.manifest_handler)
        app.router.add_get("/snapshot.json", self.snapshot_handler)
        app.router.add_get("/slow", self.slow_handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.origin = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.runner.cleanup()
        self.temp.cleanup()

    async def manifest_handler(self, request):
        if self.mode == "offline":
            raise web.HTTPServiceUnavailable()
        return web.Response(body=json_bytes(self.manifest.model_dump()), content_type="application/json")

    async def snapshot_handler(self, request):
        return web.Response(body=self.raw + (b" " if self.mode == "tampered" else b""), content_type="application/json")

    async def slow_handler(self, request):
        response = web.StreamResponse()
        await response.prepare(request)
        try:
            for _ in range(20):
                await response.write(b" ")
                await asyncio.sleep(.025)
        except (ConnectionError, RuntimeError):
            pass
        return response

    async def test_client_reconnect_offline_cache_and_exact_match(self):
        client = RegistryClient(self.origin + "/manifest.json", self.root / "client.json")
        self.assertIsNotNone(await client.refresh())
        self.assertEqual(client.match("openai", "gpt-4o").identity.canonicalId, "openai/gpt-4o")
        first_hash = sha(json_bytes(client.feed.model_dump()))
        self.mode = "tampered"
        await client.refresh()
        self.assertIn("SHA-256", client.last_error)
        self.assertEqual(first_hash, sha(json_bytes(client.feed.model_dump())))
        reopened = RegistryClient(self.origin + "/manifest.json", self.root / "client.json")
        self.mode = "offline"
        self.assertIsNotNone(await reopened.refresh())
        self.assertIsNotNone(reopened.last_error)
        different = RegistryClient(self.origin + "/other.json", self.root / "client.json")
        self.assertIsNone(different.feed)

    async def test_collection_timeout_total_with_trickle_and_seed_fallback(self):
        shutil.copytree(ROOT / "seed", self.root / "seed")
        config = self.config.model_copy(deep=True)
        config.sources = config.sources[:1]
        original = bounded_get

        async def trickle(session, url, maximum, etag=None):
            return await original(session, self.origin + "/slow", maximum)

        started = time.monotonic()
        with patch("observatory.collector.bounded_get", side_effect=trickle):
            bundles, failures = await collect(self.root, config, AS_OF, timeout=.12)
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(bundles[0].source.status, "seed")
        self.assertIn("TimeoutError", failures[0]["error"])

    async def test_offline_bootstrap_without_network(self):
        shutil.copytree(ROOT / "seed", self.root / "seed")
        with patch("observatory.collector.bounded_get", side_effect=AssertionError("offline must not request network")):
            bundles, failures = await collect(self.root, self.config, AS_OF, offline=True)
        feed = build_feed(bundles, self.mapping, AS_OF)
        self.assertGreater(len(feed.models), 200)
        self.assertEqual(len(failures), 6)
        self.assertTrue(all(s.status == "seed" for s in feed.sources))
        self.assertFalse(any(a.recruitmentReady for m in feed.models.values() for a in m.dimensions.values()))

    async def test_no_cache_no_seed_no_empty_publication(self):
        with self.assertRaisesRegex(ValueError, "refusing empty"):
            await collect(self.root, self.config, AS_OF, offline=True)

    async def test_remote_schema_failure_retains_previous_source(self):
        shutil.copytree(ROOT / "seed", self.root / "seed")
        config = self.config.model_copy(deep=True)
        config.sources = config.sources[:1]

        async def invalid(*args, **kwargs):
            return b'{"untrusted": "empty source"}', None

        with patch("observatory.collector.bounded_get", side_effect=invalid):
            bundles, failures = await collect(self.root, config, AS_OF)
        self.assertTrue(bundles[0].facts)
        self.assertEqual(failures[0]["fallback"], "seed")

    async def test_response_byte_limit(self):
        import aiohttp
        async with aiohttp.ClientSession() as session:
            with self.assertRaisesRegex(ValueError, "byte limit"):
                await bounded_get(session, self.origin + "/snapshot.json", 10)


if __name__ == "__main__":
    unittest.main()
