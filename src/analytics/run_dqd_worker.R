library(DatabaseConnector)
library(DataQualityDashboard)

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3L || !args[[1]] %in% c("base", "future_high")) {
  stop("Usage: run_dqd_worker.R <base|future_high> <project-root> <output-folder>")
}

mode <- args[[1]]
project_root <- normalizePath(args[[2]], winslash = "/", mustWork = TRUE)
output_folder <- normalizePath(args[[3]], winslash = "/", mustWork = FALSE)
dir.create(output_folder, recursive = TRUE, showWarnings = FALSE)

db_path <- file.path(project_root, "data", "omop_clinical.duckdb")
connection_details <- createConnectionDetails(dbms = "duckdb", server = db_path)
available_checks <- DataQualityDashboard::listDqChecks(cdmVersion = "5.4")
all_check_names <- unique(available_checks$checkDescriptions$checkName)

common_args <- list(
  connectionDetails = connection_details,
  cdmDatabaseSchema = "main",
  resultsDatabaseSchema = "main",
  cdmVersion = "5.4",
  cdmSourceName = "CMF-Synthea",
  numThreads = 1,
  sqlOnly = FALSE,
  outputFolder = output_folder,
  verboseMode = TRUE,
  writeToTable = FALSE
)

if (mode == "base") {
  common_args$checkNames <- setdiff(
    all_check_names,
    c("measureValueCompleteness", "plausibleValueHigh")
  )
} else {
  # Preserve every plausibleValueHigh check but normalize the one date
  # expression that SqlRender translates to unstable DuckDB TO_DAYS SQL.
  default_thresholds <- system.file(
    "csv", "OMOP_CDMv5.4_Field_Level.csv",
    package = "DataQualityDashboard"
  )
  thresholds <- readr::read_csv(default_thresholds, show_col_types = FALSE)
  future_date_rows <-
    !is.na(thresholds$plausibleValueHigh) &
    thresholds$plausibleValueHigh == "DATEADD(dd,1,GETDATE())"
  if (sum(future_date_rows) != 55L) {
    stop("DQD future-date configuration changed; dialect review is required.")
  }
  thresholds$plausibleValueHigh[future_date_rows] <-
    "CURRENT_DATE + INTERVAL 1 DAY"
  custom_thresholds <- tempfile(fileext = ".csv")
  readr::write_csv(thresholds, custom_thresholds, na = "")
  common_args$checkNames <- "plausibleValueHigh"
  common_args$fieldCheckThresholdLoc <- custom_thresholds
}

do.call(executeDqChecks, common_args)

if (exists("custom_thresholds")) {
  unlink(custom_thresholds)
}
