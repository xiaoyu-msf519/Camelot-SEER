"""Example read-only client: integrity, schema, publisher binding, exact matching."""

import asyncio
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import aiohttp

from .collector import bounded_get
from .matching import client_match
from .schema import Feed, Manifest
from .util import atomic_write, json_bytes, load_json, sha


class RegistryClient:
    def __init__(self, manifest_url: str, cache_path: Path):
        url = urlsplit(manifest_url)
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("client requires HTTPS (loopback HTTP allowed for tests)")
        if url.username or url.password or url.fragment:
            raise ValueError("publisher credentials/fragments are not allowed")
        self.manifest_url, self.cache_path = manifest_url, cache_path
        self.feed = None
        self.last_error = None
        try:
            cached = load_json(cache_path)
            if cached["publisher"] != sha(manifest_url.encode()):
                raise ValueError("cache belongs to another publisher")
            self.feed = self.verify(cached["manifest"], cached["snapshot"].encode("utf-8"))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    @staticmethod
    def verify(manifest_input, raw):
        manifest = Manifest.model_validate(manifest_input)
        if len(raw) > 4 * 1024 * 1024 or sha(raw) != manifest.hash:
            raise ValueError("snapshot byte limit or SHA-256 mismatch")
        feed = Feed.model_validate_json(raw)
        if (feed.catalogVersion, feed.generatedAt, len(feed.models)) != (manifest.catalogVersion, manifest.generatedAt, manifest.modelCount):
            raise ValueError("snapshot does not match manifest")
        return feed

    async def refresh(self):
        try:
            async with asyncio.timeout(4):
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4), trust_env=False) as session:
                    manifest_raw, _ = await bounded_get(session, self.manifest_url, 16384)
                    manifest = Manifest.model_validate_json(manifest_raw)
                    target = urljoin(self.manifest_url, manifest.snapshot)
                    origin = urlsplit(self.manifest_url)
                    destination = urlsplit(target)
                    if (origin.scheme, origin.netloc) != (destination.scheme, destination.netloc):
                        raise ValueError("cross-origin snapshot")
                    snapshot, _ = await bounded_get(session, target, 4 * 1024 * 1024)
                    feed = self.verify(manifest.model_dump(), snapshot)
            atomic_write(self.cache_path, json_bytes({"publisher": sha(self.manifest_url.encode()),
                         "manifest": manifest.model_dump(), "snapshot": snapshot.decode("utf-8")}))
            self.feed, self.last_error = feed, None
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
            self.last_error = type(error).__name__ + ": " + str(error)[:250]
        return self.feed

    def match(self, provider: str, api_id: str, *, as_of: datetime | None = None):
        if self.feed is None:
            return None
        key = client_match([m.identity for m in self.feed.models.values()], provider, api_id)
        if key is None:
            return None
        model = self.feed.models[key].model_copy(deep=True)
        instant = as_of or datetime.now(timezone.utc)
        generated = datetime.fromisoformat(self.feed.generatedAt.replace("Z", "+00:00"))
        catalog_age = (instant - generated).total_seconds()
        from .scoring import POLICY_VERSION
        for dimension, value in model.dimensions.items():
            evidence_date = datetime.fromisoformat(value.newestEvidenceAt.replace("Z", "+00:00")) if value.newestEvidenceAt else None
            max_age = 30 if dimension in {"context", "price"} else 180
            age = (instant - evidence_date).total_seconds() if evidence_date else None
            expired = not (0 <= catalog_age <= 86400) or age is None or not (0 <= age <= max_age * 86400)
            value.recruitmentReady = (value.score is not None and value.coverage >= .5 and value.freshness == "fresh"
                                      and not expired and self.last_error is None and self.feed.scoringPolicyVersion == POLICY_VERSION
                                      and model.identity.status not in {"unknown", "deprecated", "retired"})
            if expired and value.freshness == "fresh":
                value.freshness = "stale"
        return model
