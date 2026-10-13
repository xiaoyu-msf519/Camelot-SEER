"""Only exact provider IDs and reviewed aliases can join model records."""

import re

from .schema import Data, Identity


class Mapping(Data):
    version: str
    clientAliases: dict[str, list[str]]
    sourceLabels: dict[str, dict[str, str]]


def client_match(identities: list[Identity], provider: str, api_id: str) -> str | None:
    matches = {i.canonicalId for i in identities if i.provider == provider and api_id in [i.apiModelId, *i.aliases]}
    if len(matches) > 1:
        raise ValueError("ambiguous local model identity")
    return next(iter(matches), None)


def source_match(identities: list[Identity], mapping: Mapping, source: str, label: str):
    explicit = mapping.sourceLabels.get(source, {}).get(label)
    if explicit:
        if any(i.canonicalId == explicit for i in identities):
            return explicit, "matched"
        return None, "unmatched"
    matches = {i.canonicalId for i in identities if label in [i.apiModelId, i.canonicalId]}
    if not matches and source == "bfcl":
        # BFCL capitalizes API names and appends its documented calling mode.
        # Exact case-insensitive equality only; dates and every ID token stay intact.
        base = re.sub(r" \((?:FC|Prompt)[^)]*\)$", "", label).casefold()
        matches = {i.canonicalId for i in identities if i.apiModelId.casefold() == base}
    if len(matches) == 1:
        return next(iter(matches)), "matched"
    return None, "ambiguous" if len(matches) > 1 else "unmatched"
