"""Post-deployment smoke: both published feeds must agree and pass integrity checks."""
import argparse
import asyncio
import json
from pathlib import Path

import aiohttp

from observatory.table_client import TableClient
from observatory.collector import bounded_get
from observatory.schema import Manifest
from observatory.util import sha


async def check(origin):
    origin = origin.rstrip("/") + "/"
    client = TableClient(origin + "api/v2/manifest.json", Path(".cache/pages-smoke-v2.json"))
    feed = await client.refresh()
    if client.last_error or feed is None:
        raise ValueError("Published Arena table unavailable: " + str(client.last_error))
    async with asyncio.timeout(4):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
            manifest_raw, _ = await bounded_get(session, origin + "api/camelot-v1/manifest.json", 16384)
            manifest = Manifest.model_validate_json(manifest_raw)
            snapshot, _ = await bounded_get(session, origin + "api/camelot-v1/" + manifest.snapshot, 4 * 1024 * 1024)
    if sha(snapshot) != manifest.hash:
        raise ValueError("Published compatibility snapshot hash mismatch")
    actual = json.loads(snapshot)
    if manifest.catalogVersion != feed.catalogVersion or manifest.generatedAt != feed.generatedAt or manifest.modelCount != len(actual["models"]):
        raise ValueError("Published Arena table and Camelot projection disagree")
    omitted = set()
    for row in feed.rows:
        if row.binding == 'matched':
            local = actual['models'].get(row.canonicalId)
            eligible = row.modelStatus != 'unknown' and row.facts is not None and row.facts.contextWindow is not None
            if not eligible:
                if local is not None:
                    raise ValueError("Ineligible Arena identity leaked into compatibility table")
                omitted.add(row.canonicalId)
                continue
            if not local or local['identity']['apiModelId'] != row.apiModelId:
                raise ValueError("Arena binding disagrees with Camelot compatibility table")
    matched = client.match("openai", "gpt-4o-2024-08-06")
    if matched is None:
        raise ValueError("Expected registered model cannot be matched")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
        for path in ["index.html", "report.json", "unmatched.json", "policy.json", "schema/registry.schema.json", "api/v1/manifest.json"]:
            async with session.get(origin + path + "?seer-v2=" + feed.catalogVersion, allow_redirects=False) as response:
                if response.status != 404:
                    raise ValueError("Unexpected public endpoint: " + path + " HTTP " + str(response.status))
    print(json.dumps({"status": "passed", "origin": origin, "catalogVersion": feed.catalogVersion,
                      "variants": len(feed.rows), "camelotModels": manifest.modelCount,
                      "matched": matched.canonicalId, "sha256Verified": True,
                      "bindingsAgree": True, "compatibilityOmissions": sorted(omitted),
                      "internalEndpointsRemoved": True, "profiles": list(actual["profiles"])}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("origin", nargs="?", default="https://xiaoyu-msf519.github.io/Camelot-SEER/")
    args = parser.parse_args()
    asyncio.run(check(args.origin))


if __name__ == "__main__":
    main()
