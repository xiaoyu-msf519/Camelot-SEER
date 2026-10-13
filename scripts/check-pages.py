"""Post-deployment smoke: both published feeds must agree and pass integrity checks."""
import argparse
import asyncio
import json
from pathlib import Path

import aiohttp

from observatory.build import camelot_projection
from observatory.client import RegistryClient
from observatory.collector import bounded_get
from observatory.schema import Manifest
from observatory.util import sha


async def check(origin):
    origin = origin.rstrip("/") + "/"
    client = RegistryClient(origin + "api/v1/manifest.json", Path(".cache/pages-smoke.json"))
    feed = await client.refresh()
    if client.last_error or feed is None:
        raise ValueError("Published rich feed unavailable: " + str(client.last_error))
    async with asyncio.timeout(4):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
            manifest_raw, _ = await bounded_get(session, origin + "api/camelot-v1/manifest.json", 16384)
            manifest = Manifest.model_validate_json(manifest_raw)
            snapshot, _ = await bounded_get(session, origin + "api/camelot-v1/" + manifest.snapshot, 4 * 1024 * 1024)
    if sha(snapshot) != manifest.hash:
        raise ValueError("Published compatibility snapshot hash mismatch")
    expected = camelot_projection(feed)
    actual = json.loads(snapshot)
    if actual != expected or manifest.catalogVersion != feed.catalogVersion or manifest.modelCount != len(actual["models"]):
        raise ValueError("Published rich feed and Camelot projection disagree")
    matched = client.match("openai", "gpt-4o")
    if matched is None:
        raise ValueError("Expected registered model cannot be matched")
    print(json.dumps({"status": "passed", "origin": origin, "catalogVersion": feed.catalogVersion,
                      "richModels": len(feed.models), "camelotModels": manifest.modelCount,
                      "matched": matched.identity.canonicalId, "sha256Verified": True,
                      "projectionMatchesRichFeed": True, "profiles": list(actual["profiles"])}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("origin", nargs="?", default="https://xiaoyu-msf519.github.io/Camelot-SEER/")
    args = parser.parse_args()
    asyncio.run(check(args.origin))


if __name__ == "__main__":
    main()
