"""The source catalog: which data folders feed QABuddy and how each is chunked."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import yaml

from .settings import Settings

MAX_DATA_FILE_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class Chunking:
    max_tokens: int
    overlap_tokens: int = 0
    min_tokens: int = 0


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    folder: Path
    loader: str
    phase: int
    chunking: Chunking
    # Extra folders read as part of this source, with the id prefix used for their files.
    extra_dirs: tuple[tuple[Path, str], ...] = ()

    @property
    def active(self) -> bool:
        return self.phase <= 1


@dataclass(frozen=True)
class Catalog:
    sources: tuple[Source, ...]
    chunking: dict[str, Chunking]
    ignore_dirs: frozenset[str]
    ignore_files: frozenset[str]
    ignore_prefixes: tuple[str, ...]
    max_file_bytes: int

    def get(self, key: str) -> Source:
        for source in self.sources:
            if source.key == key:
                return source
        raise KeyError(f"Unknown source '{key}'. Known: {', '.join(s.key for s in self.sources)}")

    def files(self, source: Source, data_dir: Path) -> Iterator[tuple[Path, str]]:
        """Yield (absolute path, document id) for every indexable file of a source."""
        roots = [(source.folder, None)] + [(d, prefix) for d, prefix in source.extra_dirs]
        # The size cap keeps generated or minified files out of code repositories;
        # spreadsheets, PDFs and logs are legitimately large.
        limit = self.max_file_bytes if source.loader == "code" else MAX_DATA_FILE_BYTES
        for root, prefix in roots:
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if d not in self.ignore_dirs)
                for name in sorted(filenames):
                    path = Path(dirpath) / name
                    if name in self.ignore_files or name.startswith(self.ignore_prefixes) or path.stat().st_size > limit:
                        continue
                    if prefix is None:
                        doc_id = path.relative_to(data_dir).as_posix()
                    else:
                        doc_id = f"{prefix}/{path.relative_to(root).as_posix()}"
                    yield path, doc_id


def load_catalog(settings: Settings) -> Catalog:
    config = yaml.safe_load(settings.sources_file.read_text(encoding="utf-8"))
    chunking = {kind: Chunking(**values) for kind, values in (config.get("chunking") or {}).items()}
    sources = []
    for entry in config["sources"]:
        loader = entry["loader"]
        extra: tuple[tuple[Path, str], ...] = ()
        if loader == "jira":
            extra = ((settings.jira_snapshot_dir, "jira-mcp"),)
        sources.append(
            Source(
                key=entry["key"],
                label=entry.get("label", entry["key"]),
                folder=(settings.data_dir / entry["folder"]).resolve(),
                loader=loader,
                phase=int(entry.get("phase", 1)),
                chunking=chunking.get(loader, Chunking(max_tokens=600)),
                extra_dirs=extra,
            )
        )
    ignore = config.get("ignore", {})
    return Catalog(
        sources=tuple(sources),
        chunking=chunking,
        ignore_dirs=frozenset(ignore.get("dirs", [])),
        ignore_files=frozenset(ignore.get("files", [])),
        ignore_prefixes=tuple(ignore.get("file_prefixes", [])),
        max_file_bytes=int(ignore.get("max_file_kb", 512)) * 1024,
    )
