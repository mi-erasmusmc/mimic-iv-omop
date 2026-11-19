from conftest import admissions, cvb, patients, ts

from mimic_omop import concepts as c
from mimic_omop.etl.person import person
from mimic_omop.vocab import cvb_lookup

SET = "mimiciv_hosp.admissions.race"
RACE_MAP = cvb_lookup(
    cvb(
        (SET, "WHITE", "Maps to", c.WHITE, "Race"),
        (SET, "BLACK/AFRICAN AMERICAN", "Maps to", c.BLACK, "Race"),
        (SET, "BLACK/CAPE VERDEAN", "Maps to", 1546200, "Ethnicity"),
        (SET, "HISPANIC/LATINO - PUERTO RICAN", "Maps to", 1547071, "Ethnicity"),
    ),
    SET,
)


def one(pat, adm=None):
    df = person(pat, adm if adm is not None else admissions(), RACE_MAP).collect()
    assert df.height == 1
    return df.row(0, named=True)


def test_demographics():
    p = one(patients({"gender": "M", "anchor_age": 40, "anchor_year": 2150}))
    assert p["person_id"] == 1
    assert p["gender_concept_id"] == c.MALE
    assert p["year_of_birth"] == 2110


def test_person_without_admissions_has_unknown_race():
    p = one(patients({}), admissions({"subject_id": 2, "race": "WHITE"}))
    assert (p["race_concept_id"], p["ethnicity_concept_id"], p["race_source_value"]) == (0, 0, None)


def test_race_from_latest_informative_admission():
    adm = admissions(
        {"hadm_id": 1, "admittime": ts("2150-01-01"), "race": "BLACK/AFRICAN AMERICAN"},
        {"hadm_id": 2, "admittime": ts("2151-01-01"), "race": "WHITE"},
        {"hadm_id": 3, "admittime": ts("2152-01-01"), "race": "UNKNOWN"},
    )
    p = one(patients({}), adm)
    assert (p["race_concept_id"], p["race_source_value"]) == (c.WHITE, "WHITE")
    assert p["ethnicity_concept_id"] == c.NOT_HISPANIC


def test_ethnicity_mapping_falls_back_to_race_prefix():
    p = one(patients({}), admissions({"race": "BLACK/CAPE VERDEAN"}))
    assert (p["race_concept_id"], p["ethnicity_concept_id"]) == (c.BLACK, c.NOT_HISPANIC)


def test_hispanic_is_ethnicity_not_race():
    p = one(patients({}), admissions({"race": "HISPANIC/LATINO - PUERTO RICAN"}))
    assert (p["race_concept_id"], p["ethnicity_concept_id"]) == (0, c.HISPANIC)


def test_persons_without_an_observation_period_are_excluded():
    observed = patients({"subject_id": 2}).select(person_id="subject_id")
    df = person(patients({"subject_id": 1}, {"subject_id": 2}), admissions({}), RACE_MAP, observed)
    assert df.collect()["person_id"].to_list() == [2]
