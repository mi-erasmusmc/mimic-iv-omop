import pytest
from conftest import cvb

from mimic_omop.vocab import cvb_lookup

SET = "mimiciv_hosp.admissions.race"


def test_lookup_keeps_maps_to_in_set_and_domain():
    mapping = cvb(
        (SET, "WHITE", "Maps to", 8527, "Race"),
        (SET, "HISPANIC/LATINO", "Maps to", 38003563, "Ethnicity"),
        (SET, "ASIAN - SOUTH EAST ASIAN", "Is a", 8515, "Race"),
        (SET, "UNKNOWN", None, 0, None),
        ("mimiciv_hosp.other", "WHITE", "Maps to", 1, "Race"),
    )
    df = cvb_lookup(mapping, SET, domains=("Race",)).collect()
    assert df.select("source_code", "target_concept_id").rows() == [("WHITE", 8527)]


def test_lookup_rejects_one_to_many():
    mapping = cvb((SET, "WHITE", "Maps to", 8527, "Race"), (SET, "WHITE", "Maps to", 1, "Race"))
    with pytest.raises(ValueError, match="WHITE"):
        cvb_lookup(mapping, SET)
