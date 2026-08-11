library(DatabaseConnector)
library(DataQualityDashboard)

# Descobre automaticamente a pasta do projeto (funciona no RStudio)
if (interactive() && requireNamespace("rstudioapi", quietly = TRUE)) {
  setwd(dirname(dirname(dirname(rstudioapi::getActiveDocumentContext()$path))))
}
project_root <- getwd()

# 1. Configurar caminhos relativos
db_path <- file.path(project_root, "data", "omop_clinical.duckdb")
output_folder <- file.path(project_root, "dqd_results")
connectionDetails <- createConnectionDetails(dbms = "duckdb", server = db_path)

# 2. Ignorar o teste problemático do DuckDB
available_checks <- DataQualityDashboard::listDqChecks()
all_check_names <- unique(available_checks$checkDescriptions$checkName)
safe_checks <- all_check_names[all_check_names != "measureValueCompleteness"]

message("🚀 A iniciar a re-avaliação da base de dados no caminho: ", db_path)

# 3. Disparar os testes
executeDqChecks(
  connectionDetails = connectionDetails,
  cdmDatabaseSchema = "main", 
  resultsDatabaseSchema = "main",
  cdmSourceName = "CMF-Synthea", 
  numThreads = 1, 
  sqlOnly = FALSE,
  outputFolder = output_folder,
  verboseMode = TRUE,
  writeToTable = FALSE,
  checkNames = safe_checks
)