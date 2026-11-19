# drug_exposure

**Source:** coded records whose standard concept is in the Drug domain (see
[general](../general.md#coded-records-are-routed-by-domain)): orders from `hosp.prescriptions`,
ward administrations from `hosp.emar`, ICU administrations from `icu.inputevents`, plus the rare
code of another source that maps to a Drug concept (1 procedure code in the demo). Unmapped
orders and administrations are written here with concept 0.
**Code:** `src/mimic_omop/etl/drug_events.py`, `src/mimic_omop/etl/drug_exposure.py`.

## Three kinds of record

| Kind | Source | drug_type_concept_id | Demo rows | Mapped |
|---|---|---|---:|---:|
| Order | `prescriptions` (every row, including the BASE and ADDITIVE components of IV mixes) | 32833 EHR order | 18,135 | 94% |
| Discharge prescription | `prescriptions` whose pharmacy record is a discharge medication (none in the demo; 21,731 in MIMIC-IV 3.1) | 32838 EHR prescription | 0 | |
| Ward administration | `emar` rows whose action means the drug was given | 32818 EHR administration record | 29,074 | 94% |
| ICU administration | `inputevents` rows other than rewritten orders | 32818 EHR administration record | 17,973 | 85% |
| ED medication list | MIMIC-IV-ED `medrecon`: the medications a patient takes before the ED stay | 32830 EHR medication list | | |
| ED dispensing | MIMIC-IV-ED `pyxis`: medications dispensed in the ED | 32825 EHR dispensing record | | |

Orders and administrations of the same drug are both recorded, told apart by type, so analyses
choose: administrations for exposure, orders for intent. See
[review R17](../review.md#r17-orders-and-administrations-are-both-recorded).

## Conventions

- **THEMIS** ([start date](https://ohdsi.github.io/Themis/drug_exposure_start_date.html)): orders
  start when ordered to start (`starttime`), administrations when the administration was recorded
  (`charttime` for eMAR, `starttime` for ICU inputs).
- Orders without a start time (0.1% in MIMIC-IV 3.1) start when the pharmacy started or entered
  them (`hosp.pharmacy`). Nearly all are discharge medications (`pharmacy.proc_type = 'Discharge
  Med'`): prescriptions for after discharge, which MIMIC records without start or stop time. They
  are typed EHR prescription and, per THEMIS, end 29 days after their start when they have no stop
  time ([review R27](../review.md#r27-discharge-medications)).
- **THEMIS** ([end date](https://ohdsi.github.io/Themis/drug_end_date_not_in_data.html)): the end
  date must not be before the start. Orders end at `stoptime` (4% of demo orders stop before they
  start and end on their start date; `verbatim_end_date` keeps the recorded stop date). eMAR
  administrations have no duration: end = start, as THEMIS prescribes for administration records.
  ICU inputs end at `endtime`.
- **THEMIS** (ratified, [multiple administration actions](https://ohdsi.github.io/Themis/multiple_drug_admin_actions.html)):
  only actions meaning the drug was given become records; missed or refused doses do not. A rate
  change ends one record and starts the next; MIMIC's `inputevents` already split infusions at
  rate changes. The eMAR actions counted as given are listed in
  [review R18](../review.md#r18-which-emar-actions-are-administrations); 18% of demo eMAR rows are
  other actions or have none, and are left out.
- **THEMIS** (ratified, [missing days supply](https://ohdsi.github.io/Themis/missing_days_supply.html)):
  `days_supply` is left empty, MIMIC having no verbatim days supply.
- Records whose concept maps to the Device domain (blood products, enteral nutrition, some NDC
  products) go to [device_exposure](device_exposure.md); "PO Intake" maps to an Observation
  concept and goes to [observation](observation.md).
- Drug concepts:
  - orders: the first of NDC via Athena, NDC via CVB, the drug name via CVB, the formulary code
    via CVB that maps the order;
  - eMAR: the linked order's concept (through `pharmacy_id`, using the order's MAIN component),
    else the `medication` name via CVB's drug names, then CVB's product names (`gcpt_drug_ndc`);
  - ICU inputs: CVB's itemid mapping. CVB gives 180 of the 283 input items a 'Maps to'; most
    others only hierarchy relationships. An `Is a` (e.g. "Midazolam (Versed)" `Is a` midazolam)
    is used as the item's concept, a `Subsumes` is not, hence the lower rate. See
    [review R19](../review.md#r19-icu-input-items-without-a-mapping-and-route) and
    [R29](../review.md#r29-cvb-items-mapped-only-to-a-broader-concept).
  - A drug name CVB relates to several ingredients (a combination not in RxNorm) gives one
    record per ingredient.
- Every record gets its admission, or the visit covering its start time; and the care unit stay
  covering it. 1% of demo administrations fall in no visit and have none.
- eMAR administrations of one product get its amount as `quantity` (`hosp.emar_detail`
  `product_amount_given`: 1 TAB, 2 TAB, 30 mL) and its unit in `dose_unit_source_value`, as the
  OHDSI MIMIC ETL does for orders with the dispensed form; the dose follows from the drug
  concept's strength in DRUG_STRENGTH. MIMIC's `dose_given` (5000 UNIT, 400 mg) has no CDM
  field. An administration made up of several products (two tablet strengths for one dose) gets
  no quantity, as their amounts are not a quantity of one drug concept
  ([R20](../review.md#r20-dose-and-quantity-are-not-populated)). In MIMIC-IV 3.1, 22,820,626 of
  33,243,555 eMAR records (68.6%) have a quantity, mostly in TAB, mL, SYR (syringe) and CAP.
- `hosp.pharmacy` and `icu.ingredientevents` are not used: the first repeats orders with
  pharmacy processing detail, the second breaks ICU inputs down into ingredients (water, calories).

## Fields

| Field | Order (`prescriptions`) | Ward administration (`emar`) | ICU administration (`inputevents`) |
|---|---|---|---|
| `drug_exposure_id` | `6 * 10^15 + (hadm_id * 10^5 + seq) * 10 + k`, seq the order's position in the admission | `7 * 10^15 + (subject_id * 10^6 + emar_seq) * 10 + k` | `8 * 10^15 + (stay_id * 10^5 + seq) * 10 + k`, seq the input's position in the ICU stay |
| `person_id` | `subject_id` | `subject_id` | `subject_id` |
| `drug_concept_id` | see above | see above | see above |
| `drug_exposure_start_date` | date of the start (below) | date of `charttime` | date of `starttime` |
| `drug_exposure_start_datetime` | `starttime`, else the pharmacy record's `starttime`, else its `entertime` | `charttime` | `starttime` |
| `drug_exposure_end_date` | date of the end (below) | date of `charttime` | date of `endtime`, not before the start |
| `drug_exposure_end_datetime` | `stoptime`, not before the start; discharge prescription without `stoptime`: start + 29 days | `charttime` | `endtime`, not before `starttime` |
| `verbatim_end_date` | date of `stoptime` as recorded | not populated | date of `endtime` as recorded |
| `drug_type_concept_id` | 32833 EHR order; 32838 EHR prescription for discharge medications | 32818 EHR administration record | 32818 EHR administration record |
| `stop_reason` | not populated | not populated | not populated |
| `refills` | not populated | not populated | not populated |
| `quantity` | not populated, see [R20](../review.md#r20-dose-and-quantity-are-not-populated) | `product_amount_given` of `hosp.emar_detail`, for an administration of one product | not populated |
| `days_supply` | not populated (THEMIS) | not populated | not populated |
| `sig` | not populated (no verbatim sig in MIMIC) | not populated | not populated |
| `route_concept_id` | CVB `mimiciv_hosp.prescriptions.route` | the linked order's route; empty if unlinked | not populated, see [R19](../review.md#r19-icu-input-items-without-a-mapping-and-route) |
| `lot_number` | not populated | not populated | not populated |
| `provider_id` | not populated | not populated | not populated |
| `visit_occurrence_id` | `hadm_id`, else the visit covering `starttime` | `hadm_id`, else the visit covering `charttime` | `hadm_id`, else the visit covering `starttime` |
| `visit_detail_id` | the care unit stay covering `starttime` | the care unit stay covering `charttime` | the care unit stay covering `starttime`; an input starting up to 6 hours before its ICU stay (`stay_id`) gets the stay's first care unit stay, up to 1 hour after it the last ([R30](../review.md#r30-chart-rows-outside-their-icu-stay)) |
| `drug_source_value` | `drug`, first 50 characters | `medication`, first 50 characters | `itemid` |
| `drug_source_concept_id` | the matched key's concept: Athena NDC concept, or CVB's MIMIC4 concept | the linked order's, or CVB's MIMIC4 concept | CVB's MIMIC4 concept for `itemid` |
| `route_source_value` | `route` | the linked order's `route` | not populated |
| `dose_unit_source_value` | not populated | `product_unit` of that product (TAB, mL, CAP, VIAL, ...) | not populated |

### ED medication lists and dispensing (`ed.medrecon`, `ed.pyxis`, MIMIC-IV-ED)

- **Medicine reconciliation** lists the medications a patient takes at ED arrival, typed EHR
  medication list (32830) and dated when documented, not when taken
  ([review R33](../review.md#r33-mimic-iv-ed)). `medrecon` repeats a medication once per drug
  class (`etc_rn`); the row with `etc_rn` 1 stands for it and the 389,130 repeats are left out by
  design. The concept comes from the NDC through Athena, else from the GSN crosswalk below. In
  MIMIC-IV 3.1: 2,580,635 records, 95.8% mapped: 87.6% by NDC, 8.2% by GSN (mostly to Clinical
  Drugs, some Branded and Quantified Drugs by NDC); 20,370 more are devices (glucose meters, lancets,
  insulin syringes and needles), in device_exposure.
- **Pyxis** records medications dispensed from the ED's automated cabinets, typed EHR dispensing
  record (32825) and dated at `charttime`. Pyxis has no NDC, only a First Databank GSN, which
  Athena has no vocabulary for; the ETL derives a GSN to RxNorm crosswalk from
  `hosp.prescriptions`, which records both ([review
  R32](../review.md#r32-a-gsn-crosswalk-derived-from-mimics-prescriptions)). A medication is listed
  once per GSN (`gsn_rn`); the first GSN that maps gives the concept, and the 396,835 other rows
  are left out by design. In MIMIC-IV 3.1: 1,187,889 records, 96.9% mapped, almost all to
  Clinical Drugs; 1,329 more are devices (mostly the contrast agent
  Gastroview, whose GSN the crosswalk maps to a Device concept).

| Field | ED medication list (`medrecon`) | ED dispensing (`pyxis`) |
|---|---|---|
| `drug_exposure_id` | `17 * 10^15 + (stay_id * 1000 + seq) * 10 + k`, seq the medication's position in the stay | `18 * 10^15 + (stay_id * 1000 + seq) * 10 + k` |
| `drug_concept_id` | the NDC's RxNorm concept, else the GSN crosswalk's Clinical Drug, else 0 | the GSN crosswalk's Clinical Drug, else 0 |
| `drug_exposure_start_date`, `_datetime` | `charttime` (when documented) | `charttime` (when dispensed) |
| `drug_exposure_end_date`, `_datetime` | the start | the start |
| `drug_type_concept_id` | 32830 EHR medication list | 32825 EHR dispensing record |
| `visit_occurrence_id` | the visit of the ED stay's transfer | same |
| `visit_detail_id` | the care unit stay covering `charttime` in that visit | same |
| `drug_source_value` | `name` | `name` |
| `drug_source_concept_id` | the NDC's concept when the NDC maps, else 0 | 0 |
| `quantity`, `route_concept_id` | not populated | not populated |
