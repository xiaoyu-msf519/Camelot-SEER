"""Read-only matching example. Local executable provider admission stays in Camelot."""

import argparse
import asyncio
import json
import random
from pathlib import Path

from observatory.table_client import TableClient


async def run(args):
    client = TableClient(args.manifest_url, args.cache)
    while True:
        await client.refresh()
        model = client.match(args.provider, args.api_model_id, effort=args.effort, budget=args.budget, harness=args.harness)
        print(json.dumps({"matched": model.canonicalId if model else None,
                          "refreshError": client.last_error,
                          "inference": model.inference.model_dump() if model else None,
                          "scores": {key: value.model_dump() for key, value in model.scores.items()} if model else {}}, ensure_ascii=True, indent=2), flush=True)
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
    parser.add_argument("--effort", default="unspecified")
    parser.add_argument("--budget")
    parser.add_argument("--harness")
    args = parser.parse_args()
    if args.interval < 60:
        parser.error("poll interval must be at least 60 seconds")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
