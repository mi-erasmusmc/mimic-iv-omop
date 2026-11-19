# Generate PhenotypeLibrary cohorts on a CDM exported with `mimic-omop export-duckdb` (evaluation
# E2b, step 1: the official Circe SQL).
#
# Usage: Rscript evaluation/R/generate_cohorts.R <database.duckdb> <output folder> <cohort id>...
# Needs CohortGenerator, CirceR and PhenotypeLibrary (v3.37.0; see docs/evaluation.md, E2b).
# Writes the cohort table to the `cohorts` schema of the database and, per cohort, the rows to
# <output>/cohort_<id>.csv, the definition to <output>/cohort_<id>.json and the counts to
# <output>/counts.csv.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) stop("usage: Rscript generate_cohorts.R <database.duckdb> <output> <cohort id>...")
database <- normalizePath(args[1])
output <- args[2]
ids <- as.integer(args[-(1:2)])
dir.create(output, recursive = TRUE, showWarnings = FALSE)

memory_limit <- Sys.getenv("DUCKDB_MEMORY_LIMIT", "32GB")
details <- DatabaseConnector::createConnectionDetails(
  dbms = "duckdb",
  server = database,
  extraSettings = list(config = list(
    memory_limit = memory_limit,
    temp_directory = normalizePath(file.path(output, "duckdb-tmp"), mustWork = FALSE)
  ))
)
connection <- DatabaseConnector::connect(details)
on.exit(DatabaseConnector::disconnect(connection))
# DatabaseConnector installs DuckDB's ICU extension but does not load it, which date arithmetic in
# the translated SQL needs (DatabaseConnector issue #340)
DatabaseConnector::executeSql(connection, "LOAD icu; CREATE SCHEMA IF NOT EXISTS cohorts;",
                              progressBar = FALSE, reportOverallTime = FALSE)

definitions <- PhenotypeLibrary::getPlCohortDefinitionSet(ids)
# the definitions as run, for the Python evaluator (mimic_eval.e2b)
for (i in seq_len(nrow(definitions))) {
  writeLines(definitions$json[i], file.path(output, sprintf("cohort_%d.json", definitions$cohortId[i])))
}
tables <- CohortGenerator::getCohortTableNames(cohortTable = "cohort")
CohortGenerator::createCohortTables(connection = connection, cohortDatabaseSchema = "cohorts",
                                    cohortTableNames = tables)
CohortGenerator::generateCohortSet(
  connection = connection,
  cdmDatabaseSchema = "cdm",
  cohortDatabaseSchema = "cohorts",
  cohortTableNames = tables,
  cohortDefinitionSet = definitions,
  incremental = FALSE
)

counts <- CohortGenerator::getCohortCounts(connection = connection, cohortDatabaseSchema = "cohorts",
                                           cohortTable = "cohort", cohortIds = ids)
write.csv(counts, file.path(output, "counts.csv"), row.names = FALSE)
for (id in ids) {
  rows <- DatabaseConnector::querySql(connection, sprintf(
    "SELECT subject_id, cohort_start_date, cohort_end_date FROM cohorts.cohort WHERE cohort_definition_id = %d",
    id
  ), snakeCaseToCamelCase = FALSE)
  names(rows) <- tolower(names(rows))
  write.csv(rows, file.path(output, sprintf("cohort_%d.csv", id)), row.names = FALSE)
}
print(counts)
