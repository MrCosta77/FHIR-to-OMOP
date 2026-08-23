library(DatabaseConnector)
library(DataQualityDashboard)

# Usa o projeto aberto como base. Se o ficheiro estiver aberto no RStudio,
# aceita o caminho do editor apenas quando este identifica realmente o projeto.
project_root <- getwd()
if (interactive() && requireNamespace("rstudioapi", quietly = TRUE)) {
  active_path <- tryCatch(
    rstudioapi::getActiveDocumentContext()$path,
    error = function(e) ""
  )
  if (is.character(active_path) && length(active_path) == 1L && nzchar(active_path)) {
    candidate_root <- normalizePath(
      file.path(dirname(active_path), "..", ".."),
      winslash = "/",
      mustWork = FALSE
    )
    if (file.exists(file.path(candidate_root, "main.py"))) {
      project_root <- candidate_root
    }
  }
}

if (!file.exists(file.path(project_root, "main.py"))) {
  stop(
    "Project root not found. Run setwd('C:/Users/mario/Documents/FHIR-to-OMOP') before sourcing this script."
  )
}

# 1. Configurar caminhos relativos
db_path <- file.path(project_root, "data", "omop_clinical.duckdb")
output_folder <- file.path(project_root, "dqd_results")
connectionDetails <- createConnectionDetails(dbms = "duckdb", server = db_path)

# 2. Ignorar o teste problemático do DuckDB
available_checks <- DataQualityDashboard::listDqChecks(cdmVersion = "5.4")
all_check_names <- unique(available_checks$checkDescriptions$checkName)
safe_checks <- all_check_names[all_check_names != "measureValueCompleteness"]

message("🚀 A iniciar a re-avaliação da base de dados no caminho: ", db_path)

# 3. Disparar os testes
executeDqChecks(
  connectionDetails = connectionDetails,
  cdmDatabaseSchema = "main", 
  resultsDatabaseSchema = "main",
  cdmVersion = "5.4",
  cdmSourceName = "CMF-Synthea", 
  numThreads = 1, 
  sqlOnly = FALSE,
  outputFolder = output_folder,
  verboseMode = TRUE,
  writeToTable = FALSE,
  checkNames = safe_checks
)
