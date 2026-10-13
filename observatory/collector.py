"""Bounded HTTPS collection with independently validated, atomic source caches."""

import asyncio
from pathlib import Path
from urllib.parse import urlsplit, urljoin

import aiohttp
import yaml

from .sources import Bundle, Config, SourceConfig, parse
from .util import atomic_write, json_bytes, load_json, sha


async def bounded_get(session, url, max_bytes, etag=None):
    headers = {"If-None-Match": etag} if etag else {}
    async with session.get(url, headers=headers, allow_redirects=False) as response:
        if response.status == 304:
            return None, response.headers.get("ETag")
        response.raise_for_status()
        if response.content_length and response.content_length > max_bytes:
            raise ValueError("source exceeds byte limit")
        chunks, total = [], 0
        async for chunk in response.content.iter_chunked(65536):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("source exceeds byte limit")
            chunks.append(chunk)
        return b"".join(chunks), response.headers.get("ETag")


async def arena_download(session, url, max_bytes, etag=None):
    """Follow only HF's public artifact redirects; the session deadline covers every hop."""
    for _ in range(4):
        target = urlsplit(url)
        if target.scheme != "https" or target.username or target.password or not (
            target.hostname == "huggingface.co" or target.hostname.endswith(".hf.co")
        ):
            raise ValueError("Arena artifact redirect outside Hugging Face")
        async with session.get(url, headers={"If-None-Match": etag} if etag else {}, allow_redirects=False) as response:
            if response.status in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.headers["Location"])
                etag = None
                continue
            if response.status == 304:
                return None, response.headers.get("ETag")
            response.raise_for_status()
            if response.content_length and response.content_length > max_bytes:
                raise ValueError("Arena source exceeds byte limit")
            result = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                result.extend(chunk)
                if len(result) > max_bytes:
                    raise ValueError("Arena source exceeds byte limit")
            return bytes(result), response.headers.get("ETag")
    raise ValueError("too many Arena artifact redirects")


def validate_envelope(envelope, config):
    if set(envelope) != {"schemaVersion", "bundle", "hash"} or envelope["schemaVersion"] != 1:
        raise ValueError("invalid cache envelope")
    if sha(json_bytes(envelope["bundle"])) != envelope["hash"]:
        raise ValueError("cache SHA-256 mismatch")
    bundle = Bundle.model_validate(envelope["bundle"])
    if bundle.source.id != config.id or bundle.source.url != config.url:
        raise ValueError("cache publisher mismatch")
    return bundle


def read_previous(root, config):
    path = root / ".cache" / (config.id + ".json")
    if path.exists():
        try:
            return validate_envelope(load_json(path), config), "cached"
        except (ValueError, KeyError, TypeError):
            pass
    seed = root / "seed" / (config.id + ".json")
    if seed.exists():
        return validate_envelope(load_json(seed), config), "seed"
    return None, "seed"


def save_bundle(path: Path, bundle: Bundle):
    data = bundle.model_dump(mode="json")
    atomic_write(path, json_bytes({"schemaVersion": 1, "bundle": data, "hash": sha(json_bytes(data))}))


def preserve_price_history(bundle, previous):
    if not previous:
        return
    older = {row.identity.canonicalId: row for row in previous.facts}
    for row in bundle.facts:
        old = older.get(row.identity.canonicalId)
        if not old or not row.facts.pricing:
            continue
        current = row.facts.pricing[-1]
        history = list(old.facts.pricing)
        if history and (history[-1].input, history[-1].output, history[-1].cachedInput) == (current.input, current.output, current.cachedInput):
            row.facts.pricing = history
        else:
            row.facts.pricing = [*history[-63:], current]


async def collect(root: Path, config: Config, observed: str, offline=False, timeout=5.):
    if not 0 < timeout <= 5:
        raise ValueError("source timeout must be in (0, 5] seconds")
    if len({s.id for s in config.sources}) != len(config.sources):
        raise ValueError("duplicate configured source")
    failures = []

    async def one(session, item: SourceConfig):
        previous, origin = read_previous(root, item)
        try:
            if offline:
                raise RuntimeError("offline mode")
            if urlsplit(item.url).scheme != "https":
                raise ValueError("collector requires HTTPS")
            fetcher = arena_download if item.parser == "arena-parquet" else bounded_get
            async with asyncio.timeout(timeout):
                raw, etag = await fetcher(session, item.url, item.maxBytes, previous.source.upstreamVersion if previous else None)
            if raw is None:
                if previous is None:
                    raise ValueError("304 without a cached source")
                bundle = previous.model_copy(deep=True)
                bundle.source.checkedAt = observed
                bundle.source.status = "fresh"
                bundle.source.error = None
            else:
                bundle = parse(item, raw, observed, config.providers, etag)
                preserve_price_history(bundle, previous)
                atomic_write(root / ".cache" / "raw" / (sha(raw) + ".bin"), raw)
            save_bundle(root / ".cache" / (item.id + ".json"), bundle)
            return bundle
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, KeyError, RuntimeError, yaml.YAMLError) as error:
            message = "offline mode" if offline else type(error).__name__ + ": " + str(error)[:250]
            failures.append({"sourceId": item.id, "error": message, "fallback": origin if previous else "unavailable"})
            if previous:
                previous.source.status = origin
                previous.source.checkedAt = observed
                previous.source.error = message
                return previous
            return None

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), headers={"User-Agent": "Camelot-Model-Observatory/0.1 (+public-data-only)"}, trust_env=True) as session:
        results = await asyncio.gather(*(one(session, item) for item in config.sources))
    bundles = [result for result in results if result]
    if not bundles:
        raise ValueError("no validated source, cache or seed; refusing empty publication")
    active_hashes = {bundle.source.contentSha256 for bundle in bundles}
    raw_files = sorted((root / ".cache" / "raw").glob("*.bin"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in raw_files[20:]:
        if old.stem not in active_hashes:
            old.unlink()
    return bundles, sorted(failures, key=lambda x: x["sourceId"])
