# Run the large DQD suite in isolated processes and merge disjoint results.

project_root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
if (!file.exists(file.path(project_root, "main.py"))) {
  stop("Run this script from the FHIR-to-OMOP project root.")
}

resume_id <- Sys.getenv("DQD_RESUME_ID", unset = "")
run_id <- if (nzchar(resume_id)) resume_id else format(Sys.time(), "%Y%m%d%H%M%S")
parts_root <- file.path(project_root, "dqd_results", ".parts", run_id)
base_folder <- file.path(parts_root, "base")
dir.create(base_folder, recursive = TRUE, showWarnings = FALSE)

rscript <- file.path(
  R.home("bin"),
  if (.Platform$OS.type == "windows") "Rscript.exe" else "Rscript"
)
worker <- file.path(project_root, "src", "analytics", "run_dqd_worker.R")

run_worker <- function(mode, output_folder, table = NULL) {
  label <- if (is.null(table)) mode else paste(mode, table, sep = ":")
  existing_json <- list.files(output_folder, pattern = "\\.json$", full.names = TRUE)
  if (length(existing_json) == 1L) {
    message("⏭️  Reusing completed DQD shard: ", label)
    return(invisible(NULL))
  }
  if (length(existing_json) > 1L) {
    stop("Cannot resume ", label, ": found multiple JSON result files.")
  }
  message("🚀 Starting isolated DQD shard: ", label)
  worker_args <- c(shQuote(worker), mode, shQuote(project_root), shQuote(output_folder))
  if (!is.null(table)) {
    worker_args <- c(worker_args, table)
  }
  status <- system2(rscript, args = worker_args)
  if (!identical(status, 0L)) {
    warning("DQD shard process failed once; retrying in a fresh isolated process: ", label)
    unlink(list.files(output_folder, pattern = "\\.json$", full.names = TRUE))
    status <- system2(rscript, args = worker_args)
  }
  if (!identical(status, 0L)) {
    stop("DQD shard failed twice: ", label, " (exit status ", status, ")")
  }
}

find_result <- function(folder) {
  files <- list.files(folder, pattern = "\\.json$", full.names = TRUE)
  if (length(files) != 1L) {
    stop("Expected exactly one DQD JSON in ", folder, "; found ", length(files))
  }
  files[[1]]
}

run_worker("base", base_folder)
shard_json <- c(find_result(base_folder))

threshold_path <- system.file(
  "csv", "OMOP_CDMv5.4_Field_Level.csv",
  package = "DataQualityDashboard"
)
thresholds <- readr::read_csv(threshold_path, show_col_types = FALSE)
heavy_tables <- sort(unique(thresholds$cdmTableName[
  !is.na(thresholds$plausibleValueHigh) |
  !is.na(thresholds$measureValueCompleteness)
]))

for (table in heavy_tables) {
  table_folder <- file.path(parts_root, "field_heavy", tolower(table))
  dir.create(table_folder, recursive = TRUE, showWarnings = FALSE)
  if (table == "OBSERVATION") {
    for (mode in c("field_measure", "field_high")) {
      check_folder <- file.path(table_folder, mode)
      dir.create(check_folder, recursive = TRUE, showWarnings = FALSE)
      run_worker(mode, check_folder, table)
      shard_json <- c(shard_json, find_result(check_folder))
    }
  } else {
    run_worker("field_heavy", table_folder, table)
    shard_json <- c(shard_json, find_result(table_folder))
  }
}

combined_json <- file.path(
  project_root, "dqd_results", paste0("cmf-synthea-", run_id, ".json")
)
python <- if (.Platform$OS.type == "windows") {
  file.path(project_root, ".venv", "Scripts", "python.exe")
} else {
  file.path(project_root, ".venv", "bin", "python")
}
if (!file.exists(python)) {
  python <- Sys.which("python")
}
if (!nzchar(python) || !file.exists(python)) {
  stop("Project Python was not found; unable to merge DQD shards.")
}
merge_status <- system2(
  python,
  args = c(
    "-m", "src.quality.merge_dqd_results",
    vapply(shard_json, shQuote, character(1)),
    "--output", shQuote(combined_json)
  )
)
if (!identical(merge_status, 0L)) {
  stop("DQD shard merge failed with exit status ", merge_status)
}

message("✅ Complete combined DQD report: ", combined_json)
