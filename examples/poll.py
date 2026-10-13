"""Read-only matching example. Local executable provider admission stays in Camelot."""

import argparse
import asyncio
import json
import random
from pathlib import Path

from observatory.client import RegistryClient


async def run(args):
    client = RegistryClient(args.manifest_url, args.cache)
    while True:
        await client.refresh()
        model = client.match(args.provider, args.api_model_id)
        print(json.dumps({"matched": model.identity.canonicalId if model else None,
                          "refreshError": client.last_error,
                          "dimensions": {key: {"score": value.score, "coverage": value.coverage,
                                          "freshness": value.freshness, "recruitmentReady": value.recruitmentReady}
                                         for key, value in model.dimensions.items()} if model else {}}, ensure_ascii=True, indent=2), flush=True)
        if args.once:
            return 1 if client.last_error else 2 if model is None else 0
        await asyncio.sleep(args.interval + random.uniform(0, args.interval * .1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest_url")
    parser.add_argument("provider")
    parser.add_argument("api_model_id")
    parser.add_argument("--cache", type=Path, default=Path(".cache/client.json"))
    parser.add_argument("--interval", type=float, default=21600)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval < 60:
        parser.error("poll interval must be at least 60 seconds")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
