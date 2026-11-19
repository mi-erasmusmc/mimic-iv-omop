"""The ETL documentation must cover every CDM field of every table the pipeline builds."""

import re
from pathlib import Path

import pytest

from mimic_omop.cdm.spec import TABLES
from mimic_omop.pipeline import REGISTRY, VOCABULARY_TABLES

DOCS = Path(__file__).parents[1] / "docs"


@pytest.mark.parametrize("table", sorted(VOCABULARY_TABLES))
def test_every_vocabulary_table_is_listed(table):
    """Vocabulary tables share one page; most are copies of Athena."""
    page = (DOCS / "etl" / "vocabulary.md").read_text()
    assert re.search(rf"^\| `{table}` \|", page, re.M), f"vocabulary.md does not list {table}"


@pytest.mark.parametrize("table", [t.name for t in REGISTRY if t.name not in VOCABULARY_TABLES])
def test_every_field_of_a_built_table_is_documented(table):
    page = DOCS / "etl" / f"{table}.md"
    assert page.exists(), f"no documentation page for {table}"
    documented = set(re.findall(r"^\| `([a-z0-9_]+)` \|", page.read_text(), re.M))
    missing = [f.name for f in TABLES[table] if f.name not in documented]
    assert not missing, f"{page.name} does not document {missing}"


def test_review_items_linked_from_pages_exist():
    review = (DOCS / "review.md").read_text()
    anchors = {
        re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-")
        for h in re.findall(r"^## (.+)$", review, re.M)
    }
    for page in DOCS.rglob("*.md"):
        for anchor in re.findall(r"review\.md#([a-z0-9-]+)", page.read_text()):
            assert anchor in anchors, f"{page.name} links to missing review item #{anchor}"
