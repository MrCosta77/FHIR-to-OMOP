# ==============================================================================
# CLINICAL MAPPING FRAMEWORK - DQD DASHBOARD VIEWER
# ==============================================================================
library(DataQualityDashboard)
library(shiny)

# Descobre automaticamente a pasta do projeto (funciona no RStudio)
if (interactive() && requireNamespace("rstudioapi", quietly = TRUE)) {
  setwd(dirname(dirname(dirname(rstudioapi::getActiveDocumentContext()$path))))
}
project_root <- getwd()

# 1. Resolve the configured result directory used by the selected profile.
output_folder <- Sys.getenv(
  "CMF_DQD_RESULTS_DIR", unset = file.path(project_root, "dqd_results")
)

# 2. Procurar os ficheiros JSON
ficheiros_json <- list.files(path = output_folder, pattern = "\\.json$", full.names = TRUE)

# 3. Launch only an explicitly selected report when multiple runs exist.
if (length(ficheiros_json) > 0) {
  selected_report <- Sys.getenv("CMF_DQD_REPORT", unset = "")
  if (nzchar(selected_report)) {
    selected_report <- normalizePath(selected_report, winslash = "/", mustWork = TRUE)
    available <- normalizePath(ficheiros_json, winslash = "/", mustWork = TRUE)
    if (!selected_report %in% available) {
      stop("CMF_DQD_REPORT is not a JSON report in CMF_DQD_RESULTS_DIR.")
    }
  } else if (length(ficheiros_json) == 1L) {
    selected_report <- ficheiros_json[[1]]
  } else {
    stop(
      "Multiple DQD reports found. Set CMF_DQD_REPORT to the run-linked JSON."
    )
  }
  
  message("✅ A iniciar o servidor web do OHDSI...")
  message(paste("📊 Ficheiro carregado:", selected_report))
  message(paste("📁 Diretório configurado:", output_folder))
  
  DataQualityDashboard::viewDqDashboard(jsonPath = selected_report)
  
} else {
  message("❌ Erro: Não foi encontrado nenhum ficheiro JSON na pasta de resultados.")
  message(paste("Procurado na pasta:", output_folder))
}
