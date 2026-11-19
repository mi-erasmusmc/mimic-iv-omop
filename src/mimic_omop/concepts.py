"""Standard concepts used directly by ETL conventions (verified against Athena v27-FEB-25)."""

# Gender
MALE = 8507
FEMALE = 8532

# Race (fallbacks when CVB maps a combined race/ethnicity string to an ethnicity concept)
WHITE = 8527
BLACK = 8516
ASIAN = 8515

# Ethnicity (OMB categories, the only values allowed in person.ethnicity_concept_id)
HISPANIC = 38003563
NOT_HISPANIC = 38003564

# Visit
INPATIENT = 9201
OUTPATIENT = 9202
EMERGENCY_ROOM = 9203
ER_AND_INPATIENT = 262
INTENSIVE_CARE = 32037
LABORATORY_VISIT = 32036
OBSERVATION_ROOM = 581385

# Condition status
PRIMARY_DIAGNOSIS = 32902
SECONDARY_DIAGNOSIS = 32908

# Type concepts
EHR = 32817
EHR_BILLING = 32821
EHR_ADMINISTRATION = 32818
EHR_ORDER = 32833
EHR_PRESCRIPTION = 32838
EHR_MEDICATION_LIST = 32830
EHR_DISPENSING = 32825
LAB = 32856
REGISTRY = 32879
ALGORITHM_FROM_EHR = 32882

# Measurement value operators
OPERATORS = {"<": 4171756, "<=": 4171754, "=": 4172703, ">=": 4171755, ">": 4172704}

# Domains (domain_concept_id), for FACT_RELATIONSHIP
DOMAIN_CONCEPT = {
    "Procedure": 10, "Drug": 13, "Device": 17, "Condition": 19, "Measurement": 21,
    "Observation": 27, "Specimen": 36,
}  # fmt: skip
SPECIMEN_DOMAIN = DOMAIN_CONCEPT["Specimen"]

# Relationships (relationship_concept_id), for FACT_RELATIONSHIP
HAS_SPECIMEN = 44818756  # 'Has specimen': a record has the specimen it was made on
SPECIMEN_OF = 44818854  # 'Specimen of', its reverse

# ED arrival transport (edstays.arrival_transport), as visit admitted_from
ARRIVAL = {
    "AMBULANCE": 8668,
    "HELICOPTER": 8850,
}  # CMS Place of Service: Ambulance - Land, - Air or Water
