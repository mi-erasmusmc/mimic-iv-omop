# FeatureExtraction's default covariates for the E3 labels (evaluation/meds/fe_defaults.py).
#
# Usage: Rscript evaluation/R/fe_covariates.R <work> fe_clean|fe_default [task ...]
#
# Reads <work>/tasks.csv and the DuckDB copy <work>/cdm-fe.duckdb that `fe_defaults.py prepare`
# writes (cohort table e3fe.labels, one row per label with row_id; schema cdm as exported and
# schema clean with billing diagnoses at their visit's end). Per task, createDefaultCovariateSettings
# with the window ending `end_day` days from the prediction date (fe_clean) or on it (fe_default),
# on schema clean or cdm; writes <work>/fe/<variant>/<task>/{covariates,covariate_ref,analysis_ref}
# .parquet from the Andromeda object (a DuckDB connection).
#
# Memory: DuckDB's limit is DUCKDB_MEMORY_LIMIT (default 64GB), spilling to <work>/duckdb-tmp; run
# it in a memory-limited scope for a hard cap.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("usage: Rscript fe_covariates.R <work> fe_clean|fe_default [task ...]")
work <- normalizePath(args[1])
variant <- args[2]
schema <- c(fe_clean = "clean", fe_default = "cdm")[[variant]]
tasks <- read.csv(file.path(work, "tasks.csv"))
if (length(args) > 2) tasks <- tasks[tasks$name %in% args[-(1:2)], ]

details <- DatabaseConnector::createConnectionDetails(
  dbms = "duckdb",
  server = file.path(work, "cdm-fe.duckdb"),
  extraSettings = list(config = list(
    memory_limit = Sys.getenv("DUCKDB_MEMORY_LIMIT", "64GB"),
    temp_directory = file.path(work, "duckdb-tmp")
  ))
)
connection <- DatabaseConnector::connect(details)

for (i in seq_len(nrow(tasks))) {
  task <- tasks$name[i]
  # readmission's window ends on the prediction date either way (fe_defaults.variants)
  if (variant == "fe_default" && tasks$end_day[i] == 0) next
  out <- file.path(work, "fe", variant, task)
  if (file.exists(file.path(out, "covariates.parquet"))) next
  settings <- FeatureExtraction::createDefaultCovariateSettings()
  settings$endDays <- if (variant == "fe_clean") tasks$end_day[i] else 0
  start <- Sys.time()
  data <- FeatureExtraction::getDbCovariateData(
    connection = connection,
    cdmDatabaseSchema = schema,
    cohortDatabaseSchema = "e3fe",
    cohortTable = "labels",
    cohortIds = tasks$cohort_id[i],
    rowIdField = "row_id",
    covariateSettings = settings,
    aggregated = FALSE
  )
  dir.create(out, recursive = TRUE, showWarnings = FALSE)
  copy <- function(table, file) {
    DBI::dbExecute(data, sprintf("COPY %s TO '%s' (FORMAT parquet)", table, file.path(out, file)))
  }
  copy("covariateRef", "covariate_ref.parquet")
  copy("analysisRef", "analysis_ref.parquet")
  copy("covariates", "covariates.tmp.parquet")
  file.rename(file.path(out, "covariates.tmp.parquet"), file.path(out, "covariates.parquet"))
  n <- DBI::dbGetQuery(data, "SELECT count(*) AS n FROM covariates")$n
  message(sprintf(
    "%s %s: window end %d, %s covariate values, %.0f s", variant, task, settings$endDays,
    format(n, big.mark = ","), as.numeric(difftime(Sys.time(), start, units = "secs"))
  ))
  Andromeda::close(data)
}
DatabaseConnector::disconnect(connection)
message("FE_OK ", variant)
