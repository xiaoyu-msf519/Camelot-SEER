"""Read-only recruitment evidence; matching never grants reasoning or tool authority."""
from datetime import datetime, timezone

from .arena_schema import Inference, SeerTable
from .client import RegistryClient
from .schema import Manifest
from .util import sha


class TableClient(RegistryClient):
    @staticmethod
    def verify(manifest_input, raw):
        manifest = Manifest.model_validate(manifest_input)
        if len(raw) > 4 * 1024 * 1024 or sha(raw) != manifest.hash:
            raise ValueError("snapshot byte limit or SHA-256 mismatch")
        table = SeerTable.model_validate_json(raw)
        if (table.catalogVersion, table.generatedAt, len(table.rows)) != (manifest.catalogVersion, manifest.generatedAt, manifest.modelCount):
            raise ValueError("table does not match manifest")
        return table

    def match(self, provider, api_id, *, effort="unspecified", budget=None, harness=None, variant_id=None, as_of=None):
        if self.feed is None:
            return None
        inference = Inference(effort=effort, budget=budget, harness=harness)
        candidates = [r for r in self.feed.rows if r.binding == "matched" and r.provider == provider
                      and r.apiModelId == api_id and r.inference == inference
                      and (variant_id is None or r.variantId == variant_id)]
        if len(candidates) > 1:
            raise ValueError("ambiguous Arena inference variant; select an explicit variant ID")
        if not candidates:
            return None
        row = candidates[0].model_copy(deep=True)
        moment = as_of or datetime.now(timezone.utc)
        generated = datetime.fromisoformat(self.feed.generatedAt.replace("Z", "+00:00"))
        usable_table = self.last_error is None and 0 <= (moment - generated).total_seconds() <= 86400
        for score in row.scores.values():
            published = datetime.fromisoformat(score.publishedAt.replace("Z", "+00:00"))
            if not usable_table or not 0 <= (moment - published).total_seconds() <= 30 * 86400:
                score.freshness = "stale"
        return row

    def variants(self, provider, api_id):
        if self.feed is None:
            return []
        return [r.model_copy(deep=True) for r in self.feed.rows if r.binding == "matched" and r.provider == provider and r.apiModelId == api_id]
