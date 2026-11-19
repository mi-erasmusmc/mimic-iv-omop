from datetime import datetime, timedelta

import polars as pl
from conftest import admissions, cvb, no_transfers, transfers, ts
from hypothesis import given
from hypothesis import strategies as st

from mimic_omop import concepts as c
from mimic_omop.etl.visit_occurrence import visit_occurrence
from mimic_omop.vocab import cvb_lookup

ADMIT = "mimiciv_hosp.admissions.admission_location"
DISCH = "mimiciv_hosp.admissions.discharge_location"
ADMIT_MAP = cvb_lookup(cvb((ADMIT, "TRANSFER FROM HOSPITAL", "Maps to", 8717, "Visit")), ADMIT)
DISCH_MAP = cvb_lookup(cvb((DISCH, "HOME", "Maps to", 581476, "Visit")), DISCH)
T0 = datetime(2150, 1, 1)


def visits(
    adm: pl.LazyFrame, tr: pl.LazyFrame | None = None, unattached: pl.LazyFrame | None = None
) -> pl.DataFrame:
    tr = no_transfers() if tr is None else tr
    return (
        visit_occurrence(adm, tr, ADMIT_MAP, DISCH_MAP, unattached)
        .collect()
        .sort("visit_occurrence_id")
    )


def test_visit_concept_by_admission_type_and_ed():
    df = visits(
        admissions(
            {"hadm_id": 1, "admission_type": "ELECTIVE"},
            {"hadm_id": 2, "admission_type": "EW EMER.", "edregtime": ts("2150-01-01 08:00")},
            {"hadm_id": 3, "admission_type": "EU OBSERVATION", "edregtime": ts("2150-01-01 08:00")},
            # observation in billing only: inpatient, from the ED or not (review R3)
            {
                "hadm_id": 4,
                "admission_type": "OBSERVATION ADMIT",
                "edregtime": ts("2150-01-01 08:00"),
            },
            {"hadm_id": 5, "admission_type": "DIRECT OBSERVATION"},
        )
    )
    assert df["visit_concept_id"].to_list() == [
        c.INPATIENT, c.ER_AND_INPATIENT, c.OBSERVATION_ROOM, c.ER_AND_INPATIENT, c.INPATIENT
    ]  # fmt: skip


def test_ed_registration_starts_the_visit():
    v = visits(admissions({"edregtime": ts("2150-01-01 08:00")})).row(0, named=True)
    assert v["visit_start_datetime"] == ts("2150-01-01 08:00")


def test_visit_spans_its_transfers():
    v = visits(
        admissions({}),
        transfers(
            {"transfer_id": 1, "intime": ts("2150-01-01 02:00"), "outtime": ts("2150-01-02")},
            {"transfer_id": 2, "eventtype": "transfer", "intime": ts("2150-01-02"),
             "outtime": ts("2150-01-05 12:30")},
            {"transfer_id": 3, "eventtype": "discharge", "intime": ts("2150-01-09"), "outtime": None},
        ),
    ).row(0, named=True)  # fmt: skip
    assert v["visit_start_datetime"] == ts("2150-01-01 02:00")
    assert v["visit_end_datetime"] == ts("2150-01-05 12:30")  # discharge events are not stays


def test_end_before_start_is_clamped():
    v = visits(admissions({"dischtime": ts("2149-12-31 00:00")})).row(0, named=True)
    assert v["visit_end_datetime"] == v["visit_start_datetime"] == ts("2150-01-01 10:00")


def test_places_are_mapped_and_unmapped_ones_are_zero():
    v = visits(
        admissions({"admission_location": "TRANSFER FROM HOSPITAL", "discharge_location": "DIED"})
    ).row(0, named=True)
    assert (v["admitted_from_concept_id"], v["discharged_to_concept_id"]) == (8717, 0)
    assert v["discharged_to_source_value"] == "DIED"


def test_unadmitted_ed_stays_become_er_visits():
    ed = {"hadm_id": None, "eventtype": "ED", "careunit": "Emergency Department"}
    df = visits(
        admissions({"hadm_id": 20_000_000}),
        transfers(
            ed | {"transfer_id": 31, "intime": ts("2150-03-01 10:00"), "outtime": ts("2150-03-01 14:00")},
            # contiguous with the previous row: the same stay
            ed | {"transfer_id": 32, "careunit": "Emergency Department Observation",
                  "intime": ts("2150-03-01 14:00"), "outtime": ts("2150-03-02 09:00")},
            ed | {"transfer_id": 33, "intime": ts("2150-05-01 10:00"), "outtime": ts("2150-05-01 11:00")},
        ),
    )  # fmt: skip
    ed_visits = df.filter(pl.col("visit_concept_id") == c.EMERGENCY_ROOM)
    assert ed_visits.select(
        "visit_occurrence_id", "visit_start_datetime", "visit_end_datetime"
    ).rows() == [
        (31, ts("2150-03-01 10:00"), ts("2150-03-02 09:00")),
        (33, ts("2150-05-01 10:00"), ts("2150-05-01 11:00")),
    ]
    assert df.sort("visit_start_datetime")["preceding_visit_occurrence_id"].to_list() == [
        None,
        20_000_000,
        31,
    ]


admission_rows = st.lists(
    st.builds(
        lambda subject, start_h, los_h, ed_h: {
            "subject_id": subject,
            "admittime": T0 + timedelta(hours=start_h),
            "dischtime": T0 + timedelta(hours=start_h + los_h),
            "edregtime": None if ed_h is None else T0 + timedelta(hours=start_h - ed_h),
        },
        st.integers(1, 4),
        st.integers(0, 10_000),
        st.integers(-48, 24 * 60),
        st.none() | st.integers(0, 24),
    ),
    min_size=1,
    max_size=30,
)  # fmt: skip


@given(admission_rows)
def test_visit_invariants(rows):
    df = visits(admissions(*[r | {"hadm_id": i} for i, r in enumerate(rows)]))
    assert df.height == len(rows)
    assert (df["visit_end_datetime"] >= df["visit_start_datetime"]).all()
    assert (df["visit_start_date"] == df["visit_start_datetime"].dt.date()).all()

    # preceding visit belongs to the same person and does not start later
    prev = df.join(
        df.select(
            preceding_visit_occurrence_id="visit_occurrence_id",
            prev_person="person_id",
            prev_start="visit_start_datetime",
        ),
        on="preceding_visit_occurrence_id",
    )
    assert (prev["prev_person"] == prev["person_id"]).all()
    assert (prev["prev_start"] <= prev["visit_start_datetime"]).all()
    # exactly one visit per person has no predecessor
    firsts = df.filter(pl.col("preceding_visit_occurrence_id").is_null())
    assert sorted(firsts["person_id"]) == sorted(df["person_id"].unique())


def test_outpatient_events_get_one_laboratory_visit_per_day():
    from datetime import date

    loose = pl.LazyFrame(
        {
            "person_id": [1, 1, 1, 1],
            "time": [
                ts("2150-01-02 10:00"),
                ts("2150-03-01 08:00"),
                ts("2150-03-01 15:00"),
                ts("2150-03-02 08:00"),
            ],
            "source": ["labevents", "labevents", "microbiologyevents", "labevents"],
        }
    )
    df = visits(admissions({}), unattached=loose)  # the first event is inside the admission
    labs = df.filter(pl.col("visit_concept_id") == c.LABORATORY_VISIT)
    days = [(date(2150, 3, 1) - date(1970, 1, 1)).days, (date(2150, 3, 2) - date(1970, 1, 1)).days]
    assert labs.select(
        "visit_occurrence_id", "visit_start_datetime", "visit_end_datetime", "visit_source_value"
    ).rows() == [
        (10**12 + 1 * 10**5 + days[0], ts("2150-03-01 08:00"), ts("2150-03-01 15:00"), "labevents+microbiologyevents"),
        (10**12 + 1 * 10**5 + days[1], ts("2150-03-02 08:00"), ts("2150-03-02 08:00"), "labevents"),
    ]  # fmt: skip


def test_admissions_sharing_one_ed_registration_do_not_overlap():
    # an observation stay and the admission after it list the same edregtime; transfers give the
    # ED stay to the first, so the second starts at its own first transfer
    ed = ts("2193-08-05 05:04")
    df = visits(
        admissions(
            {"hadm_id": 1, "admission_type": "EU OBSERVATION", "edregtime": ed,
             "admittime": ts("2193-08-05 06:18"), "dischtime": ts("2193-08-05 11:44")},
            {"hadm_id": 2, "admission_type": "DIRECT EMER.", "edregtime": ed,
             "admittime": ts("2193-08-05 11:45"), "dischtime": ts("2193-08-11 09:25")},
        ),
        transfers(
            {"hadm_id": 1, "transfer_id": 11, "eventtype": "ED", "intime": ed, "outtime": ts("2193-08-05 06:19")},
            {"hadm_id": 1, "transfer_id": 12, "eventtype": "admit", "intime": ts("2193-08-05 06:19"),
             "outtime": ts("2193-08-05 11:44")},
            {"hadm_id": 2, "transfer_id": 21, "eventtype": "admit", "intime": ts("2193-08-05 11:45"),
             "outtime": ts("2193-08-11 09:26")},
        ),
    )  # fmt: skip
    assert df["visit_start_datetime"].to_list() == [ed, ts("2193-08-05 11:45")]


def test_discharge_to_home_is_concept_zero():
    home = cvb_lookup(cvb((DISCH, "HOME", "Maps to", 581476, "Visit")), DISCH)
    df = visit_occurrence(
        admissions({"discharge_location": "HOME"}), no_transfers(), ADMIT_MAP, home
    ).collect()
    assert df.select("discharged_to_concept_id", "discharged_to_source_value").row(0) == (0, "HOME")


def test_ed_arrival_and_disposition_do_not_depend_on_row_order():
    ed = {"hadm_id": None, "eventtype": "ED", "careunit": "Emergency Department"}
    tr = transfers(
        ed
        | {"transfer_id": 31, "intime": ts("2150-03-01 10:00"), "outtime": ts("2150-03-01 14:00")}
    )
    # two ED stays registered at the same time; the short one looks like a duplicate registration
    stays = pl.DataFrame(
        {"subject_id": [1, 1], "stay_id": [300, 301], "intime": [ts("2150-03-01 10:00")] * 2,
         "outtime": [ts("2150-03-01 10:05"), ts("2150-03-01 14:00")],
         "arrival_transport": ["UNKNOWN", "WALK IN"], "disposition": ["OTHER", "HOME"]}
    )  # fmt: skip
    for order in (stays, stays.reverse()):
        df = visit_occurrence(
            admissions({"hadm_id": 20_000_000}), tr, ADMIT_MAP, DISCH_MAP, edstays=order.lazy()
        ).collect()
        assert df.filter(pl.col("visit_occurrence_id") == 31).select(
            "admitted_from_source_value", "discharged_to_source_value"
        ).row(0) == ("UNKNOWN", "HOME")  # the first stay's arrival, the last one's disposition
