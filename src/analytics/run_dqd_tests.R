# Run the large DQD suite in two isolated R processes. DuckDB/R can exhaust
# process memory when plausibleValueHigh follows the other ~1,900 checks, while
# both disjoint shards complete reliably and preserve the full check set.

project_root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
if (!file.exists(file.path(project_root, "main.py"))) {
  stop(
    "Project root not found. Run setwd('C:/Users/mario/Documents/FHIR-to-OMOP') before sourcing this script."
  )
}

run_id <- format(Sys.time(), "%Y%m%d%H%M%S")
parts_root <- file.path(project_root, "dqd_results", ".parts", run_id)
base_folder <- file.path(parts_root, "base")
high_folder <- file.path(parts_root, "future_high")
dir.create(base_folder, recursive = TRUE, showWarnings = FALSE)
dir.create(high_folder, recursive = TRUE, showWarnings = FALSE)

rscript <- file.path(
  R.home("bin"),
  if (.Platform$OS.type == "windows") "Rscript.exe" else "Rscript"
)
worker <- file.path(project_root, "src", "analytics", "run_dqd_worker.R")

run_worker <- function(mode, output_folder) {
  message("🚀 Starting isolated DQD shard: ", mode)
  status <- system2(
    rscript,
    args = c(shQuote(worker), mode, shQuote(project_root), shQuote(output_folder))
  )
  if (!identical(status, 0L)) {
    stop("DQD shard failed: ", mode, " (exit status ", status, ")")
  }
}

run_worker("base", base_folder)
run_worker("future_high", high_folder)

find_result <- function(folder) {
  files <- list.files(folder, pattern = "\\.json$", full.names = TRUE)
  if (length(files) != 1L) {
    stop("Expected exactly one DQD JSON in ", folder, "; found ", length(files))
  }
  files[[1]]
}

base_json <- find_result(base_folder)
high_json <- find_result(high_folder)
combined_json <- file.path(
  project_root, "dqd_results", paste0("cmf-synthea-", run_id, ".json")
)
python <- Sys.which("python")
if (!nzchar(python)) {
  python <- Sys.which("python3")
}
if (!nzchar(python)) {
  stop("Python was not found on PATH; unable to merge DQD shards.")
}
merge_status <- system2(
  python,
  args = c(
    "-m", "src.quality.merge_dqd_results",
    shQuote(base_json), shQuote(high_json),
    "--output", shQuote(combined_json)
  )
)
if (!identical(merge_status, 0L)) {
  stop("DQD shard merge failed with exit status ", merge_status)
}

message("✅ Complete combined DQD report: ", combined_json)
