# General conventions

## Source data

MIMIC-IV is read from the PhysioNet `csv.gz` files, converted to parquet with explicit types
(`src/mimic_omop/source.py`). Types come from fixed rules, not inference, so a column has the
same type in the demo and the full data. Conversion fails on any value that does not parse.
The modules used are `hosp` and `icu` of MIMIC-IV 3.1 and, when converted into the same
directory, MIMIC-IV-ED 2.2 (`ed`; [review R33](review.md#r33-mimic-iv-ed)).

All MIMIC dates are shifted into 2100–2200, by a different offset per patient, and ages over 89
are reported as 91. Calendar dates, seasons and secular trends are therefore meaningless, and
comparisons across patients by date are invalid. Intervals within a patient are preserved.

## Identifiers

Identifiers are derived from source keys, so they are stable across runs, independent of the
person buckets used to build the CDM, and lead back to the source row.

| CDM id | Value |
|---|---|
| `person_id` | `subject_id` |
| `visit_occurrence_id`, admissions | `hadm_id` (20,000,000–29,999,999) |
| `visit_occurrence_id`, ED-only stays | first `transfer_id` of the stay (30,000,000–39,999,999) |
| `visit_occurrence_id`, Laboratory Visits | `10^12 + person_id * 10^5 + days since 1970-01-01` |
| `visit_occurrence_id`, Outpatient Visits | `2 * 10^12 + person_id * 10^5 + days since 1970-01-01` |
| `visit_detail_id` | `transfer_id` |
| `care_site_id` | first 31 bits of SHA-256 of the care unit name |
| `observation_period_id` | `person_id * 1000 + period number` |
| `specimen_id` | `micro_specimen_id` |
| event tables (`condition_occurrence_id`, ...) | `prefix * 10^15 + key * 10 + k`, see below |

For event tables the prefix names the source table (1 `diagnoses_icd`, 2 `procedures_icd`,
3 `hcpcsevents`, 4 `drgcodes`, 5 `labevents`, 6 `prescriptions`, 7 `emar`, 8 `inputevents`,
9 `chartevents`, 10 `outputevents`, 11 `omr`, 12 `microbiologyevents`, 13 `procedureevents`; MIMIC-IV-ED: 14 `diagnosis`, 15 `triage`,
16 `vitalsign`, 17 `medrecon`, 18 `pyxis`; 19 `datetimeevents`), the
key identifies the source row (`hadm_id * 1000 + seq_num` for per-admission tables, with the
ICD version added for `diagnoses_icd` and `procedures_icd`; `labevent_id` for labs; see each
table's page for the others), and k numbers the standard concepts the row maps to. The primary key check of every table guards against collisions.

These are the ids of the parquet output. Many of them do not fit the CDM's 32-bit `integer`, and
the event ids from prefix 9 up exceed 2^53, beyond which the doubles R reads 64-bit integers into
are no longer exact. The DuckDB export (`export-duckdb`, for the OHDSI tools) therefore writes
every integer field as a 32-bit INTEGER and renumbers the ids that do not fit:
- **Unchanged.** A table whose ids all fit keeps them: `person_id`, `visit_detail_id`,
  `specimen_id`, `care_site_id`, and the admissions' and ED stays' `visit_occurrence_id`.
- **Renumbered from 1,000,000,001.** The Laboratory and Outpatient Visits get new ids in the order
  of their old ones, so they are told apart from the admissions by range.
- **Renumbered from 1.** The other tables (event tables, observation periods, eras, episodes) get
  new ids in the order of their old ones.
- **References follow.** Foreign keys follow the new ids. So do `measurement_event_id`,
  `observation_event_id` and `episode_event.event_id`, through their field concept, and
  `fact_relationship`'s fact ids, through their domain concept.

A new id is the rank of the old one, so the database does not store the old ids; to trace a
renumbered record to its source row, `export.id_maps` recomputes them from the parquet.

## Vocabularies and mapping

Mappings are not curated in this repository, with small exceptions for what CVB does not map:
`hosp.omr` ([review R21](review.md#r21-local-mapping-for-omr)) and the MIMIC-IV-ED vital signs and
triage ([R33](review.md#r33-mimic-iv-ed)), written as LOINC and UCUM codes, insurance as SOPT
payers ([payer_plan_period](etl/payer_plan_period.md)), and a GSN crosswalk
derived from the prescriptions for MIMIC-IV-ED drugs ([R32](review.md#r32-a-gsn-crosswalk-derived-from-mimics-prescriptions)).
Two sources are used:

- **Athena** standard vocabularies, release 29-AUG-26 (`mimic-omop load-athena`), for codes from
  standard source vocabularies: ICD-9-CM, ICD-10-CM, ICD-9-Proc, ICD-10-PCS, CPT4, HCPCS, NDC,
  and UCUM units.
- **CVB**, the Tufts MIMIC vocabulary ([TuftsCTSI/CVB](https://github.com/TuftsCTSI/CVB/tree/main/MIMIC)),
  pinned to a commit, for MIMIC's local codes and strings: lab and ICU item ids, units, admission
  and discharge locations, race, DRG descriptions, care units. The ETL reads CVB's curation file
  `MIMIC/Mappings/mimic_mapping.csv`, because it records which MIMIC field each code belongs to;
  the same string can mean different things in different fields.

Rules:

1. **Athena first for standard source vocabularies.** For ICD, CPT4 and HCPCS codes Athena's
   'Maps to' is used, and CVB only for codes Athena does not map (CVB mostly duplicates Athena
   here). CVB codes carry no ICD version, and hundreds of codes exist in both ICD-9 and ICD-10, so
   a CVB mapping is applied to a version only if Athena has the code in that version, or in
   neither. *(Decision, [review R9](review.md#r9-cvb-and-athena-disagree-on-some-codes))*
2. **CVB targets are resolved through Athena.** A target that is still standard and valid is
   kept; one deprecated or de-standardised since the CVB refresh follows its own 'Maps to'; any
   other target becomes 0. The target's domain is taken from Athena, since CVB's copy can be
   stale.
3. **Broader concept when CVB has no equivalent** ("uphill" mapping). A CVB code without a
   'Maps to' target is mapped to the standard concept CVB records it `Is a` (`skos:broadMatch`):
   ICU midazolam to the ingredient midazolam, "Motor L Leg"-type items to the finding they are a
   kind of. If CVB names several broader concepts, the most specific is used when one is an
   ancestor of another; several drug ingredients (a combination product) each give a record;
   otherwise the code stays unmapped. Narrower (`Subsumes`) and related matches are not used.
   *(Decision, [review R29](review.md#r29-cvb-items-mapped-only-to-a-broader-concept))* The
   codes mapped this way are listed for review, most frequent first, in
   [uphill-review](uphill-review.md).
9. **Known errors are excluded.** CVB relationships found wrong (lung compliance as treatment
   adherence, a goal sedation score as an observed one, specimen tubes held by the lab as
   measurements) are listed in `src/mimic_omop/mappings/cvb_exclusions.csv` with the reason, left
   out before mapping, and reported to CVB ([R26](review.md#r26-feedback-for-cvb)); a test fails
   when an exclusion no longer matches CVB.
4. **Custom concepts.** CVB defines MIMIC4 concepts with ids from 2,000,000,000 as source
   concepts. They are written as `*_source_concept_id`, and the output vocabulary tables include
   them ([vocabulary](etl/vocabulary.md)).
5. **One-to-many mappings** produce one record per standard concept
   (**THEMIS** [one-to-many mappings](https://ohdsi.github.io/Themis/one_to_many_mapping.html)).
6. **'Maps to value'** targets become `value_as_concept_id` of the Observation or Measurement
   record (**THEMIS** [records with values](https://ohdsi.github.io/Themis/records_with_values.html)).
7. **Unmapped** codes get concept 0 and keep their `*_source_value` and, where known,
   `*_source_concept_id`.
8. **Units** come from CVB's unit mapping, else an exact UCUM code, else a case-insensitive UCUM
   code where that is unambiguous (`ml` is mL; `ms` could be three different units, so is not
   guessed).

`load-athena` warns when a vocabulary the ETL needs is missing from the download. Athena leaves
out relationships into vocabularies that are not in the bundle, so a missing target vocabulary
silently unmaps codes; Cancer Modifier, the target of ICD codes for metastases, is the one that
matters most.

## Coded records are routed by domain

Coded source records (`src/mimic_omop/etl/events.py`) are first mapped, then written to the table
of their standard concept's domain: a diagnosis code that maps to an Observation concept is
written to `observation`, not `condition_occurrence`
(**THEMIS** [records with values](https://ohdsi.github.io/Themis/records_with_values.html), CDM
domain conventions). Unmapped records go to the natural table of their source (`diagnoses_icd`
to condition_occurrence, `procedures_icd` and `hcpcsevents` to procedure_occurrence, `drgcodes` to
observation, `labevents` to measurement, the drug sources to drug_exposure).

A target in a domain that is not a clinical event domain (for example CVB maps lab item
'Problem Specimen' to the Meas Value concept 'Sample could not be processed') cannot be a
record's main concept; such records get concept 0 in the natural table. *(Decision,
[review R12](review.md#r12-targets-outside-the-event-domains))* Records in a domain whose table is
not built would be counted and logged by every run, not silently dropped; every event domain
now has its table (Episode since 2026-10-30, see [episode](etl/episode.md)).

## Visits for every record

Clinical records get the admission (`hadm_id`) when the source gives one, else the visit
covering their time. Days with outpatient measurements (`omr`) outside every admission get an
Outpatient Visit, and labs outside every visit a Laboratory Visit (see
[visit_occurrence](etl/visit_occurrence.md)), so every lab and outpatient measurement has a
visit; 1% of demo drug administrations fall outside every visit and have none. Assigning records to visits and care units by time
is done with a merged, forward-filled time line (`intervals.assign`), not an as-of join, so it
runs on the Polars streaming engine.

## Validation on every build

Each table is validated before it replaces the previous output:

- columns, types and order as in the CDM 5.5 specification, and no extra columns;
- required fields and primary keys not null; primary keys unique (within each person bucket for
  per-person tables: their ids derive from person-specific source keys, so buckets cannot share
  one, and this keeps the check of hundreds of millions of ids within memory);
- strings within their `varchar(n)` length (longer source text is truncated in the builder);
- foreign keys between CDM tables resolvable;
- with Athena loaded: every concept id exists in the output vocabulary, and fields other than
  `*_source_concept_id` hold valid standard concepts (0 allowed); the vocabulary tables' own
  foreign keys (domains, vocabularies, classes, relationships) resolve;
- reported, not enforced: visit_detail concepts that are not descendants of their visit's concept
  ([review R2](review.md#r2-visit-detail-concepts-are-not-always-descendants-of-the-visit-concept)),
  custom concept ids, and records in domains without a table yet.

The test suite additionally checks, on the demo, that every visit lies in an observation period,
observation periods neither overlap nor touch, multi-day visits do not overlap, every visit
detail lies inside its visit, every row of the coded source tables is accounted for, and the
output does not depend on the number of person buckets.
