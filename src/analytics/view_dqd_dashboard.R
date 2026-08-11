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

# 1. Definir a pasta de resultados usando caminho relativo
output_folder <- file.path(project_root, "dqd_results")

# 2. Procurar os ficheiros JSON
ficheiros_json <- list.files(path = output_folder, pattern = "\\.json$", full.names = TRUE)

# 3. Lançar o painel
if (length(ficheiros_json) > 0) {
  
  # Encontrar o ficheiro mais recente
  detalhes_ficheiros <- file.info(ficheiros_json)
  ficheiro_mais_recente <- rownames(detalhes_ficheiros)[which.max(detalhes_ficheiros$mtime)]
  
  message("✅ A iniciar o servidor web do OHDSI...")
  message(paste("📊 Ficheiro carregado:", ficheiro_mais_recente))
  
  DataQualityDashboard::viewDqDashboard(jsonPath = ficheiro_mais_recente)
  
} else {
  message("❌ Erro: Não foi encontrado nenhum ficheiro JSON na pasta de resultados.")
  message(paste("Procurado na pasta:", output_folder))
}