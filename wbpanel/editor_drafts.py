"""A draft holds an optimistic base and a change set with a value-free summary."""

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True)
class Changeset:
    _files: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "_files", MappingProxyType(
            {Path(path): data for path, data in self._files.items()}))

    def summary(self, old=None, new=None):
        out = {"writes": sum(data is not None for data in self._files.values()),
               "deletes": sum(data is None for data in self._files.values())}
        if old is not None and new is not None:
            for name, before, after in (
                ("panels", old.panels, new.panels),
                ("elements", old.get("elements") or {}, new.get("elements") or {}),
                ("instances", _instances(old), _instances(new)),
            ):
                out[name] = {
                    "added": len(after.keys() - before.keys()),
                    "removed": len(before.keys() - after.keys()),
                    "changed": sum(before[key] != after[key] for key in before.keys() & after.keys()),
                }
            out["schema_changed"] = old.get("schema") != new.get("schema")
        return out


def _instances(config):
    return {stable_id: (origin.logical_path, config.documents.data_at(origin.logical_path))
            for (kind, stable_id), origin in config.origins.items() if kind == "tile"}


@dataclass
class Draft:
    base_revision: str
    _base_hashes: dict = field(repr=False)
    changeset: Changeset = field(default_factory=Changeset)
    _restore: bool = field(default=False, repr=False)
