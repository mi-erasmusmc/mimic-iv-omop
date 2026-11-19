# Run Achilles and the Data Quality Dashboard on a CDM exported with `mimic-omop export-duckdb`.
#
# Usage: Rscript scripts/run_dqd_achilles.R <database.duckdb> [output folder, default data/dqd]
# Needs the R packages DatabaseConnector, duckdb, Achilles and DataQualityDashboard.
#
# Memory: DuckDB's default limit is 80% of the machine's memory regardless of what else runs, which
# got DQD on the full MIMIC-IV killed by the kernel. The limit is set explicitly
# (DUCKDB_MEMORY_LIMIT, default 8GB) and DuckDB spills to <output>/duckdb-tmp beyond it. For a hard
# cap on the whole process, run it in a memory-limited scope (see scripts/capped.sh).
# Results are written to the `results` schema of the database and to the output folder
# (DQD: dqd/dqd.json, viewable with DataQualityDashboard::viewDqDashboard()).

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("usage: Rscript scripts/run_dqd_achilles.R <database.duckdb> [output]")
database <- normalizePath(args[1])
output <- if (length(args) > 1) args[2] else "data/dqd"
dir.create(output, recursive = TRUE, showWarnings = FALSE)

# Workaround: the duckdb R package (1.5 and 2.0) matches column names case-sensitively when
# appending to a table, and Achilles inserts its analysis list with upper-case column names
# through DatabaseConnector (7.1 and 8.0), which fails with "Column `ANALYSIS_ID` does not
# exist in target table". DuckDB identifiers are case-insensitive, so lower-casing the names
# before the insert changes nothing else. Remove once fixed upstream.
local({
  ns <- asNamespace("DatabaseConnector")
  original <- get("insertTable.DatabaseConnectorDbiConnection", envir = ns)
  patched <- function(connection, databaseSchema = NULL, tableName, data, ...) {
    names(data) <- tolower(names(data))
    original(connection, databaseSchema = databaseSchema, tableName = tableName, data = data, ...)
  }
  registerS3method("insertTable", "DatabaseConnectorDbiConnection", patched, envir = ns)
})

memory_limit <- Sys.getenv("DUCKDB_MEMORY_LIMIT", "8GB")
details <- DatabaseConnector::createConnectionDetails(
  dbms = "duckdb",
  server = database,
  extraSettings = list(config = list(
    memory_limit = memory_limit,
    temp_directory = normalizePath(file.path(output, "duckdb-tmp"), mustWork = FALSE)
  ))
)
connection <- DatabaseConnector::connect(details)
DatabaseConnector::executeSql(connection, "CREATE SCHEMA IF NOT EXISTS results;")
source_name <- DatabaseConnector::querySql(connection, "SELECT cdm_source_name FROM cdm.cdm_source")[1, 1]
DatabaseConnector::disconnect(connection)

Achilles::achilles(
  connectionDetails = details,
  cdmDatabaseSchema = "cdm",
  resultsDatabaseSchema = "results",
  sourceName = source_name,
  cdmVersion = "5.4", # Achilles knows no 5.5; 5.5 only adds fields and vocabulary tables
  outputFolder = file.path(output, "achilles")
)

DataQualityDashboard::executeDqChecks(
  connectionDetails = details,
  cdmDatabaseSchema = "cdm",
  resultsDatabaseSchema = "results",
  cdmSourceName = source_name,
  cdmVersion = "5.5",
  cohortDatabaseSchema = "cdm", # the empty cohort tables are in the CDM schema
  outputFolder = file.path(output, "dqd"),
  outputFile = "dqd.json",
  writeToTable = TRUE
)
