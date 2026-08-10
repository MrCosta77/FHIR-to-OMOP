# ==============================================================================
# CLINICAL MAPPING FRAMEWORK - DQD DASHBOARD VIEWER
# ==============================================================================

# Carregar bibliotecas necessárias
library(DataQualityDashboard)
library(shiny)

# 1. Definir a pasta de resultados (ajustar se o projeto mudar de pasta)
output_folder <- "C:/Users/mario/Documents/Clinical-Mapping-Framework/dqd_results"

# 2. Procurar os ficheiros JSON com o caminho completo
ficheiros_json <- list.files(path = output_folder, pattern = "\\.json$", full.names = TRUE)

# 3. Lançar o painel
if (length(ficheiros_json) > 0) {
  
  # Lógica para encontrar o ficheiro mais recente (útil se tiveres múltiplos relatórios no futuro)
  detalhes_ficheiros <- file.info(ficheiros_json)
  ficheiro_mais_recente <- rownames(detalhes_ficheiros)[which.max(detalhes_ficheiros$mtime)]
  
  message("✅ A iniciar o servidor web do OHDSI...")
  message(paste("📊 Ficheiro carregado:", ficheiro_mais_recente))
  
  # Executar o Dashboard (isto bloqueia a consola até o utilizador fechar a página web)
  DataQualityDashboard::viewDqDashboard(jsonPath = ficheiro_mais_recente)
  
} else {
  message("❌ Erro: Não foi encontrado nenhum ficheiro JSON na pasta de resultados.")
  message("Por favor, executa os testes do DQD primeiro.")
}