"""What a table builder gets to read: MIMIC source, vocabulary, and CDM tables already built."""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import polars as pl

from mimic_omop.source import Source, in_bucket
from mimic_omop.vocab import Vocab


class Cdm:
    """CDM output written earlier in the run, restricted to the same person bucket as the source."""

    def __init__(self, root: Path, bucket: tuple[int, int] | None = None):
        self.root = Path(root)
        self.bucket = bucket

    def has(self, table: str) -> bool:
        return any((self.root / table).glob("part-*.parquet"))

    def scan(self, table: str) -> pl.LazyFrame:
        path = self.root / table
        if not any(path.glob("part-*.parquet")):
            raise FileNotFoundError(f"CDM table {table!r} has not been built in {self.root}")
        return in_bucket(pl.scan_parquet(path / "part-*.parquet"), "person_id", self.bucket)


@dataclass(frozen=True)
class Release:
    """Options of a run (CLI options of `run`): what cdm_source and metadata say about this CDM,
    and the observation period strategy (a key of observation_period.STRATEGIES)."""

    cdm_holder: str = "not specified"
    source_version: str = ""
    source_release_date: date | None = None  # defaults to the CDM release date
    cdm_release_date: date | None = None  # defaults to today
    observation_periods: str = "themis"


@dataclass(frozen=True)
class Inputs:
    src: Source
    vocab: Vocab
    cdm: Cdm
    release: Release = field(default_factory=Release)

    @classmethod
    def create(
        cls, source_dir: Path, vocab_dir: Path, cdm_dir: Path, release: Release | None = None
    ) -> "Inputs":
        return cls(Source(source_dir), Vocab(vocab_dir), Cdm(cdm_dir), release or Release())

    def for_bucket(self, k: int, n: int) -> "Inputs":
        return Inputs(
            Source(self.src.root, (k, n)), self.vocab, Cdm(self.cdm.root, (k, n)), self.release
        )
