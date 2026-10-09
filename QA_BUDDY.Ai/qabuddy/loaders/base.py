"""What every loader receives, plus small file helpers."""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from ..models import Chunk
from ..settings import Settings
from ..sources import Chunking, Source


class Unsupported(Exception):
    """Raised by a loader for a file type it does not index."""


@dataclass(frozen=True)
class RepoInfo:
    """Where a code folder lives on GitHub (or GitLab), so citations can link to the exact lines."""

    web_url: str
    commit: str

    def file_url(self, rel_path: str, first: int | None = None, last: int | None = None) -> str:
        url = f"{self.web_url}/blob/{self.commit}/{quote(rel_path)}"
        if first:
            url += f"#L{first}" + (f"-L{last}" if last and last != first else "")
        return url

    @classmethod
    def detect(cls, folder: Path) -> "RepoInfo | None":
        git = folder / ".git"
        if not git.is_dir():
            return None
        try:
            config = configparser.ConfigParser(strict=False)
            config.read(git / "config", encoding="utf-8")
            remote = config.get('remote "origin"', "url", fallback="")
            commit = _head_commit(git)
        except (OSError, configparser.Error):
            return None
        web = _web_url(remote)
        return cls(web, commit) if web and commit else None


def _head_commit(git: Path) -> str:
    head = (git / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref:"):
        return head
    ref = head.split(":", 1)[1].strip()
    ref_file = git / ref
    if ref_file.exists():
        return ref_file.read_text(encoding="utf-8").strip()
    packed = git / "packed-refs"
    if packed.exists():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0]
    return ""


def _web_url(remote: str) -> str:
    remote = remote.strip()
    ssh = re.match(r"^git@([^:]+):(.+?)(?:\.git)?/?$", remote)
    if ssh:
        return f"https://{ssh.group(1)}/{ssh.group(2)}"
    https = re.match(r"^https?://(?:[^@/]+@)?(.+?)(?:\.git)?/?$", remote)
    return f"https://{https.group(1)}" if https else ""


@dataclass
class LoadContext:
    source: Source
    path: Path
    doc_id: str
    rel_path: str  # relative to the source folder, for display
    settings: Settings
    repo: RepoInfo | None = None
    chunkings: dict[str, Chunking] = field(default_factory=dict)

    def chunking_for(self, loader: str) -> Chunking:
        """Chunk sizes of another loader kind, e.g. a README inside a code repository."""
        return self.chunkings.get(loader, self.source.chunking)

    def chunk(self, text: str, title: str, location: str = "", url: str = "", source_type: str = "", **meta) -> Chunk:
        return Chunk(
            text=text,
            source=self.source.key,
            source_type=source_type or self.source.loader,
            doc_id=self.doc_id,
            title=title,
            location=location,
            url=url,
            meta={k: v for k, v in meta.items() if v not in (None, "", [], {})},
        )


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def page_label(pages: list[int]) -> str:
    pages = sorted({p for p in pages if p})
    if not pages:
        return ""
    return f"p. {pages[0]}" if pages[0] == pages[-1] else f"pp. {pages[0]}-{pages[-1]}"
