from conftest import cvb, transfers

from mimic_omop.etl.care_site import care_site, stable_id
from mimic_omop.vocab import cvb_lookup

SET = "mimiciv_hosp.transfers.careunit"
PLACES = cvb_lookup(cvb((SET, "Emergency Department", "Maps to", 8870, "Visit")), SET)


def test_one_site_per_unit_with_stable_ids():
    df = care_site(
        transfers(
            {"transfer_id": 1, "careunit": "Emergency Department"},
            {"transfer_id": 2, "careunit": "Medicine"},
            {"transfer_id": 3, "careunit": "Medicine"},
            {"transfer_id": 4, "careunit": "UNKNOWN", "eventtype": "discharge"},
            {"transfer_id": 5, "careunit": None},
        ),
        PLACES,
    ).collect()
    assert df.select("care_site_name", "care_site_id", "place_of_service_concept_id").rows() == [
        ("Emergency Department", stable_id("Emergency Department"), 8870),
        ("Medicine", stable_id("Medicine"), 0),
    ]


def test_stable_id_is_pinned():
    # changing the id scheme re-keys every care_site_id, so it should be a deliberate change
    assert stable_id("Medicine") == 1154733102
    assert stable_id("Medical Intensive Care Unit (MICU)") == 761775437
