import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from observatory.arena import bind, build_table, infer, parse_parquet
from observatory.arena_schema import SeerTable
from observatory.build import build_feed, publish, public_files
from observatory.collector import arena_download, collect, validate_envelope
from observatory.matching import Mapping
from observatory.sources import Config
from observatory.table_client import TableClient
from observatory.util import load_json, json_bytes

ROOT = Path(__file__).resolve().parent.parent
NOW = "2026-10-13T12:00:00Z"


def inputs():
    config = Config.model_validate(load_json(ROOT / "config/sources.json"))
    bundles = [validate_envelope(load_json(ROOT / "seed" / (s.id + ".json")), s) for s in config.sources]
    feed = build_feed(bundles, Mapping.model_validate(load_json(ROOT / "config/aliases.json")), NOW)
    table = build_table(bundles, feed, load_json(ROOT / "config/arena-aliases.json")["models"])
    feed.catalogVersion = table.catalogVersion
    return config, bundles, feed, table


class ArenaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config, cls.bundles, cls.feed, cls.table = inputs()

    def test_official_export_covers_multiple_dimensions(self):
        self.assertGreaterEqual(sum('chat' in r.scores for r in self.table.rows), 400)
        self.assertGreaterEqual(sum('coding' in r.scores for r in self.table.rows), 400)
        self.assertGreaterEqual(sum('math' in r.scores for r in self.table.rows), 390)
        self.assertGreaterEqual(sum('webdev' in r.scores for r in self.table.rows), 130)
        self.assertEqual(sum('agent' in r.scores for r in self.table.rows), 52)
        self.assertTrue(all(b.source.license == 'CC-BY-4.0' for b in self.bundles if b.arenaRows))

    def test_inference_budget_and_harness_are_independent(self):
        base, config = infer('claude-sonnet-4-20250514-thinking-32k')
        self.assertEqual(base, 'claude-sonnet-4-20250514')
        self.assertEqual((config.effort, config.budget), ('thinking', '32k'))
        base, config = infer('gpt-5.5-xhigh (codex-harness)')
        self.assertEqual(base, 'gpt-5.5')
        self.assertEqual((config.effort, config.harness), ('xhigh', 'codex-harness'))
        self.assertEqual(infer('gpt-5')[1].effort, 'unspecified')
        self.assertEqual(infer('gpt-5 (high)')[1].effort, 'high')
        self.assertEqual(infer('deepseek-v4-pro-high-20260813')[1].effort, 'unknown')

    def test_default_model_cannot_borrow_high_score(self):
        with tempfile.TemporaryDirectory() as temp:
            client = TableClient('https://example.com/manifest.json', Path(temp)/'cache.json')
            client.feed = self.table
            high = client.match('openai', 'gpt-5', effort='high')
            self.assertIsNotNone(high)
            self.assertEqual(high.inference.effort, 'high')
            default = client.match('openai', 'gpt-5')
            self.assertTrue(default is None or default.inference.effort == 'unspecified')
            self.assertIsNone(client.match('openai', 'gpt-5', effort='high', budget='32k'))
            self.assertIsNone(client.match('anthropic', 'gpt-5', effort='high'))

    def test_snapshot_dates_are_not_removed_for_matching(self):
        model, _, state = bind('gpt-4.1-2025-04-14', 'openai', self.feed.models, {})
        self.assertIsNone(model)
        self.assertEqual(state, 'unmatched')
        row = next(r for r in self.table.rows if r.arenaName == 'gpt-4.1-2025-04-14')
        self.assertIn('chat', row.scores)
        self.assertIsNone(row.facts)
        self.assertIsNone(row.apiModelId)

    def test_natural_agent_labels_share_only_identical_bound_settings(self):
        model, config, state = bind('GPT 5.4 (High)', 'openai', self.feed.models, {})
        self.assertEqual(state, 'matched')
        self.assertEqual(model.identity.apiModelId, 'gpt-5.4')
        self.assertEqual(config.effort, 'high')
        ids = [(r.provider, r.apiModelId, json_bytes(r.inference.model_dump())) for r in self.table.rows if r.binding=='matched']
        self.assertEqual(len(ids), len(set(ids)))

    def test_raw_rating_ci_votes_dates_are_preserved(self):
        row = next(r for r in self.table.rows if r.arenaName == 'gpt-4o-2024-08-06')
        source = next(e for b in self.bundles for e in b.arenaRows if e.modelName==row.arenaName and e.category=='chat')
        score = row.scores['chat']
        self.assertEqual((score.value,score.lower,score.upper,score.votes,score.publishedAt),
                         (source.value,source.lower,source.upper,source.votes,source.publishedAt))
        self.assertGreater(score.value, 1000)
        self.assertEqual(score.metric, 'bradley-terry')

    def test_negative_agent_ips_is_not_clamped_or_percent(self):
        scores = [r.scores['agent'] for r in self.table.rows if 'agent' in r.scores]
        self.assertTrue(any(s.value < 0 for s in scores))
        self.assertTrue(all(s.metric=='ips' for s in scores))
        self.assertTrue(all(-1 <= s.value <= 1 for s in scores))

    def test_publication_exposes_only_required_tables(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            publish(self.feed,self.table,path,[],{})
            files=public_files(path)
            self.assertEqual(len(files),4)
            self.assertNotIn('index.html',files)
            self.assertFalse(any(name in files for name in ['report.json','unmatched.json','policy.json']))
            (path/'private-debug.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'unexpected'):
                public_files(path)

    def test_public_schema_rejects_execution_configuration(self):
        raw=self.table.model_dump()
        raw['rows'][0]['toolPermissions']=['dispatch_worker_task']
        with self.assertRaises(ValueError):SeerTable.model_validate(raw)
        raw=self.table.model_dump();raw['apiKey']='not-allowed'
        with self.assertRaises(ValueError):SeerTable.model_validate(raw)

    def test_bad_parquet_and_schema_drift_fail_closed(self):
        source=next(b.source for b in self.bundles if b.source.id=='arena-text')
        with self.assertRaises(ValueError):parse_parquet(b'not-parquet',source,'text_style_control')
        output=io.BytesIO();pq.write_table(pa.table({'model_name':['bad']}),output)
        with self.assertRaises(ValueError):parse_parquet(output.getvalue(),source,'text_style_control')

    def test_duplicate_export_labels_do_not_pick_highest_rating(self):
        bundles=[b.model_copy(deep=True) for b in self.bundles]
        text=next(b for b in bundles if b.source.id=='arena-text')
        original=next(r for r in text.arenaRows if r.modelName=='gpt-4o-2024-08-06' and r.category=='chat')
        duplicate=original.model_copy(deep=True);duplicate.id='duplicate';duplicate.value+=100;duplicate.lower+=100;duplicate.upper+=100
        text.arenaRows.append(duplicate)
        table=build_table(bundles,self.feed,{})
        row=next(r for r in table.rows if r.arenaName=='gpt-4o-2024-08-06')
        self.assertNotIn('chat',row.scores)

    def test_table_rebuild_is_byte_reproducible(self):
        again=build_table(self.bundles,self.feed,load_json(ROOT/'config/arena-aliases.json')['models'])
        self.assertEqual(json_bytes(again.model_dump()),json_bytes(self.table.model_dump()))

    def test_compatibility_ranking_never_uses_high_variants(self):
        from observatory.build import camelot_projection
        ordinary = next(r for r in self.table.rows if r.binding == 'matched' and 'coding' in r.scores and r.inference.effort == 'unspecified')
        high = ordinary.model_copy(deep=True)
        high.variantId = 'a' * 64
        high.inference.effort = 'high'
        high.scores['coding'].value = 4999.
        high.scores['coding'].lower = 4998.
        high.scores['coding'].upper = 5000.
        table = self.table.model_copy(deep=True)
        table.rows = [ordinary]
        reference = camelot_projection(self.feed,table)['profiles']['coder']['ranking']
        table.rows.append(high)
        result = camelot_projection(self.feed,table)['profiles']['coder']['ranking']
        self.assertEqual(reference,result)

    def test_unknown_status_is_visible_but_omitted_from_compatibility_catalog(self):
        from observatory.build import camelot_projection
        unknown = next(row for row in self.table.rows if row.binding == 'matched' and row.modelStatus == 'unknown')
        self.assertIsNotNone(unknown.facts)
        self.assertNotIn(unknown.canonicalId, camelot_projection(self.feed, self.table)['models'])


class ArenaNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_hf_redirect_cannot_escape_to_untrusted_host(self):
        with self.assertRaisesRegex(ValueError,'outside Hugging Face'):
            await arena_download(None,'https://evil.test/payload',100)

    async def test_arena_offline_no_network_and_stale_markers(self):
        config,bundles,feed,table=inputs()
        with patch('observatory.collector.arena_download',side_effect=AssertionError('offline network')):
            result,failures=await collect(ROOT,config,NOW,offline=True)
        feed=build_feed(result,Mapping.model_validate(load_json(ROOT/'config/aliases.json')),NOW)
        table=build_table(result,feed,{})
        self.assertEqual(len(failures),len(config.sources))
        self.assertTrue(all(s.freshness=='stale' for r in table.rows for s in r.scores.values()))


if __name__=='__main__':unittest.main()
