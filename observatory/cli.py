import argparse
import asyncio
import json
import sys
from pathlib import Path

from .build import build_feed, publish
from .table_client import TableClient
from .arena import build_table
from .arena_schema import SeerTable
from .collector import collect, save_bundle
from .matching import Mapping, client_match
from .schema import Feed, Manifest
from .sources import Config
from .schema import timestamp
from .util import atomic_write, json_bytes, load_json, now


def main():
    parser = argparse.ArgumentParser(description="Public model evidence collector and static registry publisher")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--offline", action="store_true")
    build.add_argument("--as-of", type=timestamp)
    build.add_argument("--timeout", type=float, default=5.)
    build.add_argument("--output", type=Path)
    build.add_argument("--write-seed", action="store_true", help="Explicitly refresh reviewed, checked-in fallback observations")
    build.add_argument("--sources", help="Comma-separated configured source IDs (e.g. omit noncommercial SWE-bench)")
    commands.add_parser("schema")
    validate = commands.add_parser("validate")
    validate.add_argument("directory", type=Path, help="Directory containing the rich feed manifest.json")
    match = commands.add_parser("match")
    match.add_argument("provider")
    match.add_argument("api_model_id")
    match.add_argument("--directory", type=Path)
    match.add_argument("--effort", default="unspecified")
    match.add_argument("--budget")
    match.add_argument("--harness")
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        if args.command == "build":
            config = Config.model_validate(load_json(root / "config" / "sources.json"))
            if args.sources:
                ids = set(args.sources.split(","))
                if not ids <= {s.id for s in config.sources}:
                    raise ValueError("unknown source selection")
                config.sources = [s for s in config.sources if s.id in ids]
            mapping = Mapping.model_validate(load_json(root / "config" / "aliases.json"))
            generated_at = args.as_of or now()
            bundles, failures = asyncio.run(collect(root, config, generated_at, args.offline, args.timeout))
            feed = build_feed(bundles, mapping, generated_at)
            # Revalidate assignments and all references before emitting any manifest.
            feed = Feed.model_validate(feed.model_dump(mode="json"))
            aliases = load_json(root / "config" / "arena-aliases.json")
            table = build_table(bundles, feed, aliases["models"])
            feed.catalogVersion = table.catalogVersion
            report = publish(feed, table, args.output or root / "public", failures, {b.source.id: b.issues for b in bundles if b.issues})
            atomic_write(root / ".cache" / "build-report.json", json_bytes(report))
            if args.write_seed:
                if args.offline or any(b.source.status != "fresh" for b in bundles) or failures:
                    raise ValueError("seed refresh requires every selected source to pass live validation")
                for bundle in bundles:
                    save_bundle(root / "seed" / (bundle.source.id + ".json"), bundle)
            print(json.dumps(report, ensure_ascii=True, indent=2))
        elif args.command == "schema":
            atomic_write(root / "schema" / "registry.schema.json", json_bytes(Feed.model_json_schema()))
            atomic_write(root / "schema" / "manifest.schema.json", json_bytes(Manifest.model_json_schema()))
            atomic_write(root / "schema" / "seer-table.schema.json", json_bytes(SeerTable.model_json_schema()))
            print("Generated closed JSON Schema (draft 2020-12)")
        else:
            directory = args.directory if args.directory else root / "public" / "api" / "v2"
            manifest = Manifest.model_validate(load_json(directory / "manifest.json"))
            feed = TableClient.verify(manifest.model_dump(), (directory / manifest.snapshot).read_bytes())
            if args.command == "validate":
                print(f"SHA-256, schema and references valid: {len(feed.rows)} Arena variants")
            else:
                client = TableClient("https://example.invalid/manifest.json", root / ".cache" / "unused-match.json")
                client.feed = feed
                row = client.match(args.provider, args.api_model_id, effort=args.effort, budget=args.budget, harness=args.harness)
                if row is None:
                    print("No exact inference variant; keep the client's local model policy.")
                    return 2
                print(json.dumps(row.model_dump(), ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
