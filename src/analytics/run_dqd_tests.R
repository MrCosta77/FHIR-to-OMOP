library(DatabaseConnector)
library(DataQualityDashboard)

# 1. Configurar caminhos e ligação
db_path <- "C:/Users/mario/Documents/Clinical-Mapping-Framework/data/omop_clinical.duckdb"
output_folder <- "C:/Users/mario/Documents/Clinical-Mapping-Framework/dqd_results"
connectionDetails <- createConnectionDetails(dbms = "duckdb", server = db_path)

# 2. Ignorar o teste problemático do DuckDB
available_checks <- DataQualityDashboard::listDqChecks()
all_check_names <- unique(available_checks$checkDescriptions$checkName)
safe_checks <- all_check_names[all_check_names != "measureValueCompleteness"]

message("🚀 A iniciar a re-avaliação da base de dados...")

# 3. Disparar os testes (Vai ler o DuckDB atualizado e gerar um JSON novo)
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