# Estratégia Analítica, Estatística e de Dados
### RWE · Ensaio Clínico · OMOP CDM · Posicionamento de Mercado
**Perspetiva:** Senior Health Data Scientist / Lead Epidemiologist
**Ponto de partida:** três pipelines funcionais (raw→CDISC, CDISC→OMOP, FHIR→OMOP) e duas bases OMOP operacionais em DuckDB
**Data:** outubro de 2026

> Este documento é o **plano diretor**. O detalhe metodológico estudo-a-estudo dos Projetos A e B está em `RWE_ANALYTICS_DOSSIER.md`; aqui não o repito — assumo-o como base e construo por cima: estado da arte, portefólio de estudos nos **três** projetos, estratégia de dados, e arquitetura de apresentação.

---

# 0. SUMÁRIO EXECUTIVO — a tese em nove parágrafos

**1. Tens um ativo que quase ninguém tem, e não é o que pensas.** Não são "três pipelines". É uma **ponte bidirecional CDISC ↔ OMOP com camada de vocabulário governada**. A maior parte das pessoas no mercado sabe fazer ETL para OMOP. Uma minoria sabe fazer SDTM/ADaM. Quase ninguém faz os dois *e* consegue medir a concordância entre eles. Isso é a tua vantagem real, e é o eixo à volta do qual a camada analítica deve ser construída.

**2. A camada analítica não é "mais código". É uma mudança de unidade de entrega.** Um pipeline entrega tabelas. Uma análise entrega **uma afirmação com um intervalo de confiança e uma lista de razões pelas quais essa afirmação pode estar errada**. Tudo o que construíres daqui para a frente tem de produzir o segundo objeto, não o primeiro.

**3. O mercado mudou a teu favor em 2025–2026, e de forma datável.** A FDA publicou a orientação final de RWE a 18 de dezembro de 2025 e, dez dias antes, anunciou que aceita RWE sem exigir dados individuais identificáveis. A União Europeia publicou o primeiro relatório de Avaliação Clínica Conjunta (JCA) a 9 de junho de 2026. O EHDS torna o acesso secundário operacional a 26 de março de 2029. Nada disto é abstrato: são **prazos que estão a criar procura por pessoas que saibam executar estudos sobre modelos comuns**.

**4. A pergunta "sintético ou MIMIC?" está mal formulada.** A pergunta correta é: **que afirmações é que cada tipo de dado te licencia a fazer?** Dados sintéticos licenciam afirmações *metodológicas* ("o meu estimador recupera o efeito verdadeiro"). Dados reais credenciados licenciam afirmações *clínicas* ("este fármaco associa-se a este risco"). Confundir as duas é o erro que destrói candidaturas em 30 segundos de entrevista.

**5. A resposta operacional é: os dois, em papéis diferentes — e há um movimento de alavancagem altíssima que podes fazer este fim de semana.** O *MIMIC-IV Clinical Database Demo* existe em três formas independentes do **mesmo** conjunto de 100 doentes: relacional nativo, **FHIR R4** e **OMOP**. Isso permite-te fazer algo que praticamente ninguém faz: comparar a tua conversão FHIR→OMOP contra a conversão de referência do OHDSI, doente a doente, conceito a conceito. Custo zero. Sem credenciais. Chamo a isto **validação diferencial**, e é o estudo mais valioso do teu portefólio.

**6. Sobre "passar dados externos pelo meu ETL ou usar pré-mapeado": há uma regra de decisão.** Se **o ETL é a afirmação**, passa-o pelo teu pipeline. Se **a análise é a afirmação**, usa o pré-mapeado. Nunca faças engenharia de semanas sobre um dataset que já tem conversão oficial, para no fim correr uma regressão logística. E mesmo quando usares pré-mapeado, corre DQD sobre ele — as conversões publicadas têm lacunas documentadas, e quantificá-las já é um diferenciador.

**7. Os teus dados atuais não sustentam inferência comparativa, e isso não é um problema — é o teu enquadramento.** 25 doentes por braço dá 33% de potência para um RR de 2.0. Mas 1.508 pessoas-ano e 16.000 medições sustentam desenhos intra-pessoa. E, acima de tudo, sustentam **estudos de métodos com verdade conhecida**, que é onde a credibilidade se ganha sem precisar de escala.

**8. A apresentação é o produto.** O que separa "candidato com projetos" de "candidato contratável" não é a quantidade de análises — é ter **quatro pacotes de estudo com protocolo, atrição, diagnósticos, sensibilidade e manifesto de reprodutibilidade**. Quatro pacotes excelentes valem infinitamente mais do que vinte notebooks.

**9. A minha recomendação em uma frase:** constrói **quatro estudos-bandeira**, cada um a provar um músculo diferente — engenharia (validação diferencial), estatística regulatória (estimand lab), epidemiologia (o custo do RWD), e maturidade em dados reais (*informed presence*) — e entrega cada um como um pacote auditável, não como um resultado.

---

# PARTE I — ESTADO DA ARTE, OUTUBRO 2026

## 1.1 O enquadramento regulatório que criou este mercado

Isto não é contexto decorativo. É a razão pela qual alguém te paga.

| Data | Acontecimento | Consequência prática para ti |
|---|---|---|
| **15 dez 2025** | FDA anuncia que aceita RWE sem exigir sempre dados individuais identificáveis em submissões de dispositivos | Abre a porta a evidência gerada em ambientes federados e des-identificados — exatamente o modelo OMOP |
| **18 dez 2025** | FDA publica a orientação final *Use of Real-World Evidence to Support Regulatory Decision-Making for Medical Devices*, substituindo a de 2017 | Define "relevance" e "reliability" como os dois eixos de avaliação. Implementação a partir de **17 fev 2026** |
| **jan 2025 →** | Regulamento (UE) 2021/2282 (HTA) aplica-se. JCA obrigatória para novos princípios ativos em oncologia e ATMP. Evidência submetida em **100 dias** após notificação | Cria pressão brutal por **execução rápida e pré-especificada** — o oposto de análises exploratórias |
| **30 abr / 9 jun 2026** | Primeira JCA aprovada (tovorafenibe, glioma pediátrico de baixo grau) e primeiro relatório publicado | O mecanismo deixou de ser teórico. Em 2026 estão previstas ~35 JCA oncológicas, ~15 ATMP e ~5 de dispositivos |
| **26 mar 2027** | EHDS: aplicação geral | Governação e *implementing acts* |
| **26 mar 2029** | EHDS Capítulo IV: **uso secundário operacional**; HDABs emitem licenças de dados; HealthData@EU ativo | Mercado europeu de acesso secundário a dados de saúde passa a existir formalmente |
| **26 mar 2031** | Uso secundário estende-se a dados genómicos e **dados de ensaios clínicos** | A ponte CDISC↔OMOP que construíste torna-se infraestrutura, não curiosidade |

**O que isto significa.** O EHDS é *permit-based*, não *consent-based*: acesso através de licença, com análise dentro de ambientes seguros. Isto é, por desenho, **um modelo federado com processamento local**. É literalmente a arquitetura do OMOP. Quem em 2029 tiver competência em executar estudos sobre CDM num nó local, com protocolo auditável, estará do lado certo de um mercado criado por regulamento.

**E o número que deves saber de cor.** No quarto relatório do EMA sobre RWE (período 8 fev 2025 – 7 fev 2026), a DARWIN EU registou: **108 tópicos avaliados, 88 estudos concluídos ou em curso (+49% face ao período anterior), 43 concluídos, taxa de viabilidade ~78–80%, e uma mediana de 4,8 meses (IQR 4–6) entre aprovação do protocolo e resultados**. A mediana de parceiros de dados por estudo subiu de **3 para 6**. E o OHDSI, em colaboração com o Uppsala Monitoring Centre, desenhou e executou um estudo completo com `Strategus` sobre registos nacionais suecos **em cinco dias**.

**Lê isto duas vezes:** um estudo regulatório, federado, multi-nó, em 4,8 meses é o *benchmark de mercado*. Se o teu pacote de estudo não conseguir ser entregue num nó novo em dias, não está ao nível.

## 1.2 O enquadramento metodológico — o que é hoje padrão e o que é hoje diferenciador

**Já é padrão (não te distingue, mas a ausência desqualifica):**
- Desenho *new-user, active-comparator*; nunca *prevalent-user* passivo
- Propensity score de larga escala com *large-scale regularised regression*, avaliação por **SMD** e não por p-values
- *Target Trial Emulation* com time zero alinhado entre elegibilidade, tratamento e seguimento
- *Negative control outcomes* e **calibração empírica** de p-values e intervalos de confiança
- Reporte segundo STROBE / RECORD / RECORD-PE; avaliação de risco de viés por ROBINS-I
- **Estimand** com os cinco atributos de ICH E9(R1)

**Ainda é diferenciador (aqui é que ganhas):**
- **Quantificação de *informed presence* / informative observation** — a literatura de 2025–2026 está a mostrar que é uma fonte de confundimento sub-reconhecida, e quase ninguém a trata operacionalmente num pipeline
- **Ligar falhas de DQD ao impacto no estimando** — passar de "o DQD passou" para "esta falha enviesa este estimando nesta direção e nesta magnitude"
- **Análise de *fitness for purpose*** da fonte, não só de qualidade genérica
- **Calibração por simulação plasmode** com verdade conhecida — provar que o estimador recupera o parâmetro, com cobertura de IC ≈ 95%
- **Documentar o custo do RWD**: quanto do efeito se perde quando passas de um ensaio para dados observacionais
- **Reprodutibilidade ligada ao vocabulário** (assinatura criptográfica do conjunto de vocabulários, não apenas "v5.0")
- **Duplo domínio CDISC/ADaM ↔ OMOP** — raríssimo e diretamente monetizável

**Um detalhe que separa seniores de juniores, e que vais usar como sinal:** a razão de proporções de notificação (PRR) e o *reporting odds ratio* (ROR) são medidas de **desproporcionalidade em notificação espontânea** (farmacovigilância tipo VigiBase/EudraVigilance). **Não são válidas em dados de EHR em coorte**, porque não há denominador de exposição e o reporting é enviesado por notoriedade. Num EHR calculas **taxa de incidência**, não desproporcionalidade. Dizer isto espontaneamente numa entrevista sinaliza formação real.

## 1.3 O enquadramento técnico — a stack que deves dominar e a que deves saber nomear

| Camada | Ferramenta | Nota |
|---|---|---|
| Orquestração de estudo | **`Strategus`** (spec JSON → módulos) | É o padrão de facto para estudos de rede. Versão 1.5.x. Um spec JSON é hoje o formato em que um estudo viaja entre nós |
| Estimação causal | **`CohortMethod`** (v6.x, no CRAN desde mar 2026), `SelfControlledCaseSeries`, `CohortIncidence`, `Characterization` | Pacotes HADES |
| Síntese federada | **`EvidenceSynthesis`** | Meta-análise sem partilha de dados individuais; intervalos de previsão |
| Diagnóstico de fenótipos | **`CohortDiagnostics`**, **`PhenotypeR`**, `PheValuator` | |
| Qualidade | **`DataQualityDashboard`** (já o usas), **Achilles** | |
| Ligação a CDM | **`CDMConnector`** + `omopgenerics` (R), `SqlRender`/`DatabaseConnector` | `CDMConnector` fala DuckDB nativamente |
| Programação estatística regulatória | **pharmaverse**: `admiral`, `sdtmchecks`, `xportr`, `metacore`/`metatools`, `rtables`, `cards`, `chevron`, `tfrmt` | O equivalente moderno do SAS |
| Validação CDISC | **CDISC CORE** (motor de regras open-source), Pinnacle 21, `sdtmchecks` | |
| Motor local | **DuckDB** | A tua escolha está validada: há pipelines `dbt`+DuckDB para MIMIC-IV→OMOP publicados em 2026, a correr em hardware de consumidor |

**Ponto de honestidade sobre DuckDB.** O `Strategus` e a generalidade do HADES assumem R + JDBC. Não vais correr `CohortMethod` em DuckDB. A arquitetura correta é: **DuckDB para ETL, qualidade e exploração; R/PostgreSQL para a camada de estimação HADES**. Saber dizer isto — e saber que `DatabaseConnector` tem suporte DuckDB mas que o ecossistema de resultados assume PostgreSQL — é exatamente o tipo de detalhe que um Data Lead testa.

## 1.4 A janela

Três factos que, juntos, definem a tua janela de oportunidade:

1. **A regulamentação criou procura** (FDA 2025, JCA 2026, EHDS 2029) por execução de estudos sobre modelos comuns.
2. **A tooling tornou-se acessível** (DuckDB, HADES, pharmaverse, MIMIC aberto) — deixou de ser preciso um departamento de TI.
3. **A competência escassa não é a tooling** — é julgar. É saber quando um efeito é real, quando é *informed presence*, quando é confundimento por indicação, e quando é simplesmente ruído numa amostra de 58.

**A camada analítica que vais construir tem de demonstrar o ponto 3.** Os pontos 1 e 2 são o vento de cauda.

---

# PARTE II — Q1: OS ESTUDOS, POR PROJETO

## 2.0 Como priorizei

Critério: **valor demonstrativo ÷ esforço**, com um ajuste — cada estudo tem de provar um músculo **diferente**. Repetir o mesmo músculo quatro vezes não soma.

| Músculo | Estudo que o prova | Porque é insubstituível |
|---|---|---|
| **Engenharia medida** | X1 — Validação diferencial | Prova que sabes *medir* a correção de uma conversão, não só fazê-la |
| **Estatística regulatória** | P1.1 — Estimand Lab | Prova que pensas em termos de E9(R1) e não de "p-value" |
| **Epidemiologia** | P2.4 — O Custo do RWD | Prova que entendes *porque* a evidência observacional é difícil |
| **Maturidade em dados reais** | P3.3 — *Informed presence* | Prova que não confundes dados com medições |

Os outros estudos são suporte: alguns são necessários, nenhum é diferenciador por si.

## 2.1 Projeto 1 — Raw → CDISC (SDTM/ADaM): a camada analítica é *statistical programming*

**Primeiro, a distinção que quase todos falham.** Num ensaio, **o ADaM é a camada analítica**. Não há "uma camada de análise por cima do ADaM" — o ADSL *é* a Tabela 1, o ADAE *é* a análise de segurança, o ADTTE *é* a sobrevivência. Portanto, para o Projeto 1, "desenvolver a camada analítica" significa **subir ao nível de qualidade regulatório**, não acrescentar gráficos.

**E há aqui um ativo que provavelmente não estás a usar: o CDISC Pilot 01 é público.**

O estudo `CDISCPILOT01` — xanomelina em adesivo transdérmico, fase III, doença de Alzheimer ligeira a moderada — está integralmente disponível em `github.com/cdisc-org/sdtm-adam-pilot-project`, com **306 doentes rastreados, 254 aleatorizados (86 placebo, 84 dose baixa, 84 dose alta)**, endpoint primário ADAS-Cog(11) variação desde baseline à semana 24, e — crucialmente — **SDTM e ADaM completos, com `define.xml` para ambas as camadas**. Os ADaM incluem `adsl` (254 linhas), `adae`, `adlbc` (74.264), `adlbh` (49.932), `advs`, `adtte`, `adqsadas`, `adqscibc`, `adqsnpix`.

Ou seja: tens um **gabarito oficial** contra o qual validar o teu pipeline raw→SDTM→ADaM. Isso transforma o Projeto 1 de "construí um pipeline" em "construí um pipeline cujos outputs batem certo com uma submissão de referência". É a diferença entre confiança e evidência.

### P1.1 — ⭐ **ESTIMAND LAB: os mesmos dados, três estimandos, três respostas**

**O estudo-bandeira do Projeto 1.** E é simples de executar, devastador de apresentar.

A pergunta: o ensaio tem descontinuações por eventos adversos no domínio `DS`. Um doente que sai aos 3 meses por intolerância — o que fazes com ele na análise? A resposta **muda o resultado**, e a maior parte das pessoas não sabe que muda.

**Desenho.** Sobre o `CDISCPILOT01`, define o evento intercorrente *"descontinuação do tratamento por evento adverso"* (a partir de `DS`, `DSDECOD`, e `AE` com `AEACN = 'DRUG WITHDRAWN'`). Implementa três estratégias de ICH E9(R1):

| Estratégia | Definição operacional | População de análise | O que mede |
|---|---|---|---|
| **Treatment policy** | Ignora a descontinuação; usa todos os dados pós-randomização independentemente da adesão | Todos os aleatorizados | Efeito de *atribuir* o tratamento |
| **Hypothetical** | Censura o doente na descontinuação (ou usa *copy reference* / múltipla imputação) | Todos os aleatorizados | Efeito *se* todos tolerassem |
| **Composite** | Descontinuação por EA conta como falha do desfecho | Todos os aleatorizados | Efeito sobre "sucesso sem intolerância" |

E adiciona uma quarta, a que os reguladores mais gostam de discutir: **while-on-treatment**.

**O entregável não é o número — é a tabela.** Uma linha por estratégia: estimativa, IC 95%, e uma frase a explicar *porque é diferente*. Quando as três divergirem (e vão divergir, porque a descontinuação por EA está associada ao braço ativo), tens a demonstração mais limpa que existe de que o estimando é uma **decisão de desenho**, não uma propriedade dos dados.

**Como o apresentas em entrevista, em 20 segundos:**
> "Peguei no CDISC Pilot público e implementei o mesmo endpoint sob quatro estratégias de evento intercorrente. O efeito no braço alto muda de X para Y conforme a estratégia. Não é um bug — é a razão pela qual o E9(R1) existe. Se o protocolo não declara o estimando, o resultado é negociável depois de o veres, e isso é precisamente o que a FDA e a JCA estão a tentar eliminar."

Isto é *muito* difícil de responder em cima. É a tua melhor carta no Projeto 1.

### P1.2 — Double programming com relatório de comparação

Re-deriva um subconjunto de ADaM (`ADSL`, `ADAE`, `ADTTE`) **duas vezes, independentemente** — uma em R (`admiral`, `metacore`) e outra em SQL/DuckDB ou SAS — e produz um **relatório de comparação programático**: contagem de linhas, igualdade variável a variável, tolerância numérica, e lista de divergências com causa-raiz.

**Porque vale a pena:** *double programming* é o ritual de qualidade da indústria farmacêutica. A maior parte dos candidatos diz "sei SAS e R". Praticamente nenhum traz um *comparison report* real. E faz uma coisa subtil: prova que percebes que a independência da segunda implementação é o que dá valor — se ambas usarem a mesma função, não é dupla programação, é a mesma programação duas vezes.

### P1.3 — Reconstrução de TLF a partir do ADaM

Tabela 1 (demografia, baseline, história de doença), tabela de exposição, `ADAE` por SOC/PT com *treatment-emergent* flag, `ADTTE` com Kaplan-Meier e HR de Cox, e *shift tables* de laboratório (baseline → pior valor pós-baseline). Com `rtables`/`chevron` ou `gtsummary`.

**Nota de priorização:** este é o estudo que eu **cortaria primeiro** se o objetivo for uma empresa de RWE e não uma CRO. É competência valiosa, mas pertence a outro mercado. Mantém-no **como apêndice** do pacote P1.1, não como estudo autónomo.

### P1.4 — Validação formal e `define.xml`

Corre `sdtmchecks` e/ou **CDISC CORE** sobre os teus datasets, produz o relatório de conformidade, e — se tiveres energia — gera `define.xml` 2.1 com `metacore`/`xportr`. Para dados OMOP, corre o teu DQD.

**Porque vale a pena:** em 30 segundos mostras que conheces o lado *conformance* do ciclo regulatório, não só o lado *analysis*.

### P1.5 — Re-derivação de potência: o que o protocolo assumiu vs o que os dados entregaram

Recalcula, a partir do `ADSL`/`ADAS-Cog`, o desvio-padrão observado, o efeito assumido no protocolo e a potência que o estudo **realmente** tinha. Compara com o desvio-padrão usado no cálculo original.

**Porque vale a pena:** é um exercício de humildade estatística que quase ninguém faz, e liga diretamente ao problema dos teus próprios N=50/58. Mostra que pensas em potência antes de pensar em resultados.

### P1.6 — A ponte: SDTM → OMOP → estimando, e de volta

Já está no `RWE_ANALYTICS_DOSSIER.md` (§4) como estudo de reconciliação. Aqui acrescento só o enquadramento estratégico: **este é o estudo que liga os teus três projetos num só argumento**. Se conseguires mostrar que o mesmo estimando, calculado (a) sobre ADaM e (b) sobre OMOP derivado do mesmo SDTM, converge dentro de tolerância pré-definida, então provaste que a tua conversão preserva a **semântica analítica** — não apenas o formato. Isso é uma afirmação muito mais forte do que "os row counts batem certo".

## 2.2 Projeto 2 — CDISC → OMOP: para além dos estudos clínicos

Os estudos A1–A6 (replicação, farmacovigilância com eDISH/Hy's Law, sobrevivência, trajetórias laboratoriais, medicação de resgate, mecânica de new-user) estão detalhados no dossiê anterior. Aqui acrescento os dois que faltavam, e que são melhores do que qualquer um deles.

### P2.4 — ⭐ **O CUSTO DO RWD: a escada de degradação**

**Este é o estudo mais inteligente que podes fazer com o Projeto 2. E é praticamente inédito como peça de portefólio.**

**A ideia.** Tens um ensaio aleatorizado com uma verdade conhecida (o efeito do braço ativo vs placebo, já medido em A1). Agora **degrada-o progressivamente até parecer dados observacionais reais**, um degrau de cada vez, medindo em cada degrau quanto é que a estimativa se afasta da verdade.

| Degrau | O que removes | Viés que introduzes | Como o corriges |
|---|---|---|---|
| **D0** | Nada — ensaio íntegro | Nenhum (referência) | — |
| **D1** | A aleatorização (atribuição por indicação) | **Confundimento por indicação** | Propensity score, IPTW |
| **D2** | A regularidade das visitas (só se mede quando há contacto) | ***Informed presence*** / informative observation | Inverse intensity weighting |
| **D3** | O seguimento completo (censura associada ao desfecho) | **Censura informativa** | IPCW, *clone-censor-weight* |
| **D4** | A classificação perfeita do desfecho (códigos em vez de adjudicação) | **Misclassificação não-diferencial** | Análise de sensibilidade quantitativa (matriz de confusão) |
| **D5** | A adesão registada (doses omitidas não observadas) | **Adesão imperfeita / *as-treated* enviesado** | New-user + PDC, *treatment policy* |
| **D6** | Tudo em simultâneo — "isto agora é um EHR" | Todos os anteriores, interagindo | Pacote completo |

**O que produzes:** um gráfico de *forest plot* onde cada degrau é uma linha, com a estimativa, o IC, e **a verdade do ensaio como linha vertical de referência**. Vês a estimativa a deslizar. E vês quanto de cada deslize é recuperado pela correção apropriada.

**Porque é que isto é ouro:**
- Prova, com números teus, que entendes **porque** a evidência observacional é difícil — não como frase feita, mas como uma curva.
- Dá-te uma resposta devastadora a "qual é a limitação da tua análise?" — não uma limitação, um *orçamento de viés quantificado*.
- É o argumento comercial de qualquer empresa de RWE, demonstrado em vez de afirmado: *"a diferença entre um ensaio e o mundo real tem este tamanho, e a nossa tecnologia recupera esta fração dela."*
- Não exige dados externos. Só exige que sejas honesto com os teus.

**O rigor que o torna publicável:** define a métrica de erro **antes** de correr (viés absoluto em escala log, e cobertura do IC 95%), e repete o processo em pelo menos 200 réplicas por degrau com sementes registadas. Sem repetição, estás a mostrar uma trajetória; com repetição, estás a mostrar uma distribuição.

### P2.7 — Viabilidade de braço de controlo sintético (arquitetura, não afirmação)

Com 25 doentes por braço, **não podes** produzir um braço de controlo sintético credível, e deves dizê-lo antes que te perguntem. O que **podes** fazer é entregar a **arquitetura** e a **análise de requisitos**: quantos doentes externos seriam necessários para igualar a precisão do braço aleatorizado, que covariáveis teriam de estar disponíveis em ambos os lados, que método de ponderação (IPTW, *entropy balancing*, *matching with caliper*) e que diagnóstico de comparabilidade.

**Porque vale a pena:** os *synthetic control arms* são um dos temas comerciais mais quentes (e mais contestados) em RWE. Demonstrar que sabes calcular o **requisito de tamanho e de comparabilidade** — em vez de fingir que o resolves com 25 doentes — é sinal de senioridade, não de fraqueza.

## 2.3 Projeto 3 — FHIR → OMOP: onde a janela de 26 anos se transforma em método

Os estudos B1–B8 estão no dossiê. Os dois novos são os melhores que este projeto permite.

### P3.3 — ⭐ **INFORMED PRESENCE: quantificar o viés que os teus dados têm por construção**

**Este é o estudo que prova maturidade em dados reais. E é o que a literatura de 2025–2026 está a pedir e quase ninguém implementa.**

**O problema.** Num EHR, um doente não "tem" 276 medições. Ele tem 276 medições **porque foi ao hospital 276 vezes**. A frequência de contacto é determinada pela saúde do doente, pelo acesso, pela prática clínica e pelo diagnóstico. Logo, o ato de medir está correlacionado com o desfecho. Isto não é *missing data* no sentido clássico: é **presença informativa** — os dados que existem não são uma amostra aleatória dos dados que existiriam se todos fossem medidos.

**A evidência de que isto não é teoria.** Num estudo de 2026 sobre dados de EHR longitudinais, uma razão de taxas de incidência que era **1,83 (IC 95% 1,02–3,28)** numa análise ingénua passou a **1,37 (0,73–2,57)** depois de contabilizada a *informed presence* por *inverse intensity weighting*. O efeito não desapareceu — **atenuou-se**, e o intervalo passou a incluir o nulo. Em sentido oposto, outro trabalho de 2026 encontrou que **o número de vezes que um peso é registado** era o preditor mais forte de 10 desfechos cardiovasculares — mais forte do que o peso mínimo, máximo, médio, a variação ou o IMC. A frequência de medição é, ela própria, um confundidor.

**O desenho, nos teus dados.**

1. **Caracteriza o processo de visita.** Modela a intensidade de contacto (visitas por pessoa-ano) em função de idade, sexo, comorbilidade (Charlson), e número de condições ativas. Produz o perfil de quem é "observado com frequência".
2. **Mostra que a intensidade prediz o desfecho.** Regressão de Poisson ou Cox com a intensidade de visita como exposição. Se prediz — e vai predizer — tens confundimento por *informação*, não por *doença*.
3. **Repete uma associação concreta, ingénua vs corrigida.** Escolhe uma associação plausível nos teus dados (biomarcador → desfecho, ou fármaco → desfecho). Estima (a) em todos os registos, (b) só em contacto de rotina/ambulatório, (c) com *inverse intensity weighting* por visita, (d) com *multiple outputation* como verificação. Reporta as quatro.
4. **Quantifica o *reporting rate ratio*** e a diferença entre estimativas.
5. **Faz a análise de sensibilidade quantitativa** para o caso de a intensidade estar ela própria afetada por exposição e desfecho (aí condicionar introduz **viés de colisor** — e tens de o dizer).

**Porque é que isto te distingue.** A maior parte das pessoas que "usa EHR" trata o número de medições como um dado. Tu tratas como um **processo gerador de dados** que tem de ser modelado. É a diferença entre usar dados e fazer inferência a partir de dados. E é exatamente o tipo de raciocínio que um Lead Epidemiologist procura — porque é a razão pela qual a maior parte dos estudos de RWE falha em revisão.

**Uma nota de rigor:** em 100 ou 58 doentes, o modelo de intensidade vai ser instável. Reporta-o como **estimativa de viabilidade com intervalos largos**, não como resultado definitivo. O entregável é a **demonstração de que sabes executar e corrigir o procedimento**, com o efeito estimado como subproduto.

### P3.9 — DQD → *fitness for purpose*: ligar qualidade de dados a viés

Já sabes correr DQD e já tens política de qualidade (`quality/dqd_policy.json`, `max_errors: 0`, `max_failed_checks: 0`). O passo que quase ninguém dá é o seguinte:

**Para cada check falhado, escreve a consequência analítica.** Três exemplos concretos do teu domínio:

| Falha DQD | Consequência no estimando |
|---|---|
| `measurement_unit_concept_id` nulo numa fração dos registos | Valores não comparáveis entre doentes → erro de medição não-diferencial → atenuação do HR em direção ao nulo |
| `condition_start_date` anterior a `birth_datetime` | Eventos impossíveis → se correlacionados com o centro, confundimento por centro |
| `drug_exposure_end_date` nulo / `days_supply` ausente | Impossível calcular PDC/MPR → persistência não estimável, ou estimada só na subcoorte com dados completos (que não é aleatória) |

Isto transforma o DQD de um *checkbox* de engenharia num **instrumento de avaliação de risco de viés**. É a diferença entre "o meu pipeline é limpo" e "o meu pipeline é limpo *no sentido que importa para esta pergunta*".

E há um segundo uso, mais fino: **correr o DQD sobre o MIMIC-CDM de referência**. As conversões publicadas têm lacunas documentadas — o demo OMOP do OHDSI/MIMIC, por exemplo, não incorpora `inputevents`/`outputevents` e não preenche notas. **Quantificar a qualidade de um dataset de referência** é uma coisa que quase nenhum utilizador de MIMIC faz, e que demonstra que não tratas dados publicados como verdade revelada.

### P3.10 — Predição: porque é que eu a **adiava**

Com 58 doentes e 16.000 medições, tens ~276 medições por doente. Qualquer modelo preditivo ao nível do doente vai ter um AUROC com intervalo de confiança gigante, e vai falhar os requisitos mínimos de tamanho de amostra de Riley et al. para desenvolvimento de modelos. Um revisor experiente vê isso em 10 segundos, e o efeito é **negativo** — não é "não provou", é "não sabe que não pode".

**Se quiseres a camada preditiva, a resposta é MIMIC-IV completo** (credenciado, >300 mil doentes), com `PatientLevelPrediction`, validação interna por bootstrap, reporte TRIPOD+AI, e avaliação PROBAST+AI. Aí a predição é defensável. Antes disso, é teatro.

## 2.4 A tabela mestra

| ID | Estudo | Dados | Método central | Prova | Esforço | Valor |
|---|---|---|---|---|---|---|
| **X1** | ⭐ Validação diferencial da conversão | MIMIC-IV Demo (relacional + FHIR + OMOP) | Concordância de conceitos, κ, DQD lado a lado | Engenharia | M | **Máximo** |
| **P1.1** | ⭐ Estimand Lab | CDISCPILOT01 (público) | ICH E9(R1), 4 estratégias de evento intercorrente | Estatística reg. | S | **Máximo** |
| **P2.4** | ⭐ O Custo do RWD | Projeto A (próprio) | Escada D0–D6, 200 réplicas, viés e cobertura | Epidemiologia | M | **Máximo** |
| **P3.3** | ⭐ *Informed presence* | Projeto B (próprio) | Inverse intensity weighting, QBA | Dados reais | M | **Máximo** |
| P1.2 | Double programming | CDISCPILOT01 | R vs SAS/SQL + *comparison report* | Rigor | M | Alto |
| P1.3 | TLF a partir de ADaM | CDISCPILOT01 | `rtables`/`chevron`, KM, shift tables | Rigor | M | Médio |
| P1.4 | Conformidade CDISC | Projeto 1 | `sdtmchecks`/CORE, `define.xml` | Conformidade | S | Médio |
| P1.5 | Re-derivação de potência | CDISCPILOT01 | Potência observada vs assumida | Humildade | S | Médio |
| P1.6 | Ponte ADaM↔OMOP | Projetos 1+2 | Replicação de estimando, κ/ICC | **Duplo domínio** | M | **Máximo** |
| P2.1 | Replicação/reconciliação | Projeto A | ADaM vs OMOP, Bland-Altman | Rigor | S | Alto |
| P2.2 | Farmacovigilância + eDISH | Projeto A | IR exata, Hy's Law | Clínico | S | Alto |
| P2.3 | Sobrevivência + TTE | Projeto A | KM, Cox, RMST | Clínico | S | Alto |
| P2.5 | Medicação de resgate | Projeto A | *Treatment patterns* | Clínico | S | Médio |
| P2.7 | Viabilidade de SCA | Projeto A | Requisitos de precisão e comparabilidade | Arquitetura | S | Alto |
| P3.1 | Cascata de fenótipos | Projeto B | `CONCEPT_ANCESTOR`, concept sets | OHDSI | S | Alto |
| P3.2 | Perfil de observabilidade | Projeto B | Achilles, caracterização | Qualidade | S | Alto |
| P3.4 | Persistência e adesão | Projeto B | PDC/MPR, Kaplan-Meier de descontinuação | Clínico | S | Alto |
| P3.5 | Trajetórias de biomarcadores | Projeto B | Modelos mistos, decomposição within/between | Estatística | S | Alto |
| P3.6 | SCCS | Projeto B | SCCS com intervalos dependentes do evento | Farmacoepi | M | Alto |
| P3.7 | Multimorbilidade | Projeto B | Charlson/Elixhauser, SMD | Descritivo | S | Médio |
| P3.8 | Controlos negativos + calibração | Projeto B | `EmpiricalCalibration` | Rigor | M | Alto |
| P3.9 | DQD → viés | Projeto B | Mapeamento falha→consequência | Qualidade | S | Alto |
| **X2** | Rede federada num portátil | Projeto B fragmentado | Sharding + spec + `EvidenceSynthesis` | Federado | M | Alto |
| **X3** | Simulação plasmode | Projeto B / MIMIC | Verdade conhecida, cobertura de IC | Métodos | M | **Máximo** |

**Regra de execução:** quatro estudos-bandeira, mais os de esforço **S** que consigas fechar em fins de semana. Nada de começar dez.

---

# PARTE III — Q2: PERGUNTAS CLÍNICAS, MÉTRICAS E METODOLOGIAS

## 3.1 A escada de ambição — sobe um degrau de cada vez

| Degrau | Pergunta | Métrica | Nunca dizes |
|---|---|---|---|
| 1. Descrição | Quem está na base de dados? | Contagens, mediana (IQR), cobertura | "significativo" |
| 2. Caracterização | Como se distribuem exposições e desfechos? | Prevalência, incidência por 1.000 pessoas-ano | "associação" |
| 3. Comparação ajustada | Há diferença entre grupos? | HR/OR/IRR com IC 95%, SMD | "causa" |
| 4. Causal | Qual seria o efeito se interviéssemos? | Estimando + IPTW/PS + análise de sensibilidade | "provámos" |
| 5. Predição | Quem vai ter o desfecho? | AUROC **+ calibração** + Brier + DCA | "diagnostica" |
| 6. Decisão | Vale a pena tratar? | NNT, custo-efetividade, análise de decisão | — |

A maior parte dos portefólios vive no degrau 2 e chama-lhe degrau 4. O teu valor está em **explicitar em que degrau cada estudo está**.

## 3.2 Perguntas clínicas concretas, por projeto

### Projeto 1 (ensaio CDISC) — perguntas de método estatístico, não de clínica

| Pergunta | Métrica | Método | Armadilha |
|---|---|---|---|
| O efeito muda conforme a estratégia de evento intercorrente? | Diferença de médias (ou HR) por estratégia | ANCOVA / MMRM / modelo de sobrevivência, 4 estratégias | Comparar estratégias com p-values em vez de mostrar a divergência das estimativas |
| A análise primária é robusta a desvios de distribuição? | Mediana vs média, IC bootstrap | Bootstrap BCa, teste de permutação | Assumir normalidade em `ADAS-Cog` com n=254 |
| O modelo misto é preferível ao ANCOVA? | AIC/BIC, estrutura de variância | MMRM com estrutura não-estruturada | Ignorar dados omissos não-MAR |
| A descontinuação é informativa? | Curva de sobrevivência da descontinuação | Kaplan-Meier de *time-to-discontinuation* | Tratar descontinuação como censura administrativa |

### Projeto 2 (OMOP de origem CDISC) — perguntas de epidemiologia clínica

| Pergunta | Métrica | Método | Armadilha |
|---|---|---|---|
| Qual a incidência de eventos adversos emergentes de tratamento? | IR por 1.000 pessoas-ano, IC Poisson exato | Contagem de eventos / tempo em risco | **Misturar `MH` (história) com `AE` (emergente)** — ambos vivem em `CONDITION_OCCURRENCE` com o mesmo type concept 32809 |
| Há sinal de hepatotoxicidade? | Quadrantes eDISH, Hy's Law (ALT>3×LSN e BT>2×LSN) | Rácio ALT vs BT no pico | Ignorar LSN (falta de referência) em vez de o declarar como limitação |
| A sobrevivência livre de evento difere entre braços? | KM, mediana, **RMST** | Log-rank, Cox, RMST (preferível com n pequeno e censura) | Reportar só HR quando os riscos não são proporcionais |
| O efeito varia com o tempo? | HR dependente do tempo | Schoenfeld residuals, modelo com interação tempo×braço | Assumir proporcionalidade sem testar |

**Sobre o RMST** — vale a pena insistir. Com 25 doentes por braço e ~70 dias, a mediana de sobrevivência pode não ser atingida, e o HR assume proporcionalidade que não podes verificar. O **restricted mean survival time** (área sob a curva KM até τ) é uma métrica absoluta, interpretável em dias, e não exige proporcionalidade. É a escolha correta e quase ninguém a faz. Dizer "preferi RMST a HR porque com n=25 não posso verificar proporcionalidade" é uma frase de sénior.

### Projeto 3 (EHR longitudinal) — perguntas de epidemiologia de dados reais

| Pergunta | Métrica | Método | Armadilha |
|---|---|---|---|
| Quem tem a doença X e quando começa? | Incidência por pessoa-ano | Cascata de fenótipos com ≥1 ou ≥2 códigos, janela de washout | Definição de caso escolhida *post hoc* |
| Os doentes tomam a medicação? | PDC, MPR, persistência (gap ≤ 30/60/90d) | `DrugUtilisation`-style, KM de descontinuação | Janela de gap escolhida para maximizar o resultado |
| Há associação fármaco→evento? | IRR | SCCS, autocontrolado | Intervalos de observação dependentes do evento |
| O efeito é constante entre subgrupos? | HR por subgrupo + teste de interação | Modelos com interação, *forest plot* | Testar 12 subgrupos e reportar o que deu p<0.05 |
| Quantos doentes têm multimorbilidade? | Índice de Charlson/Elixhauser | Mapeamento por `CONCEPT_ANCESTOR` | Usar códigos literais em vez de descendentes |

## 3.3 O catálogo de métricas — o que tens de saber explicar e quando usar

**Ocorrência**
- **Taxa de incidência** = eventos / pessoa-tempo. IC por Poisson exato (Clopper-Pearson para proporções). *Usa quando* há seguimento variável.
- **Incidência cumulativa** (risco) = eventos / pessoas em risco no início. *Usa quando* queres risco absoluto a um horizonte definido.
- **Prevalência pontual vs periódica**. *Nunca* confundas com incidência — num EHR com 26 anos, a prevalência depende da janela.

**Associação**
- **RR / OR / HR / IRR / RD**. Regra de ouro: **OR ≈ RR só quando o desfecho é raro (<10%)**. Num desfecho frequente, o OR exagera — e dizer isto espontaneamente é um sinal de rigor.
- **NNT/NNH** — a métrica que os clínicos entendem e os estatísticos esquecem.
- **Fração atribuível** — para comunicação de saúde pública.

**Sobrevivência**
- KM + mediana + **RMST** + **risco competitivo (CIF)** com *cause-specific* e *subdistribution* HR. Em idosos com 26 anos de dados, a morte é sempre um risco competitivo e ignorá-lo sobrestima a incidência cumulativa.

**Longitudinal**
- MMRM, modelos mistos (intercepto e declive aleatórios), GEE (efeito populacional). **Bland-Altman** e **ICC(2,1)** para concordância — não correlação de Pearson.
- Decomposição *within/between*: separar "os doentes que têm valores altos" de "os doentes cujos valores sobem" é a diferença entre um modelo misto e uma regressão ingénua.

**Adesão**
- PDC, MPR, persistência, *time-to-discontinuation*, *grace period*.

**Segurança**
- IR por 1.000 pessoas-ano com IC exato; *eDISH*; Hy's Law; SMQ a nível de PT.
- **Não**: PRR/ROR em EHR de coorte (só fazem sentido em notificação espontânea).

**Fenotipagem**
- Sensibilidade, especificidade, VPP/VPN do algoritmo vs adjudicação; **κ de Cohen** (e PABAK quando a prevalência é extrema — κ colapsa com prevalência baixa, e há que o reconhecer).

**Predição**
- AUROC **e** AUPRC (em desfechos raros o AUROC engana), **calibração** (slope e intercept), Brier, *decision curve analysis*.

**Sensibilidade a confundimento**
- **E-value** (quão forte teria de ser o confundidor não medido para anular o efeito), análise de viés quantitativa, *tipping point*, controlos negativos.

**Heterogeneidade federada**
- I², τ², **intervalo de previsão** (não só IC da média), *forest plot* com pesos.

## 3.4 Os oito métodos que tens de explicar de cor, sem notas

1. **New-user, active-comparator** — e porquê: evita viés de utilizador prevalente, *depletion of susceptibles* e confundimento por contraindicação.
2. **Propensity score de larga escala** — milhares de covariáveis, regularização, avaliação por **SMD < 0.1** e *love plot*, nunca por p-value de balanceamento.
3. **IPTW com pesos estabilizados e truncados** — e o que o truncagem faz ao viés vs variância.
4. **Controlos negativos + calibração empírica** — a ideia de que um estudo sem distribuição nula conhecida não é interpretável. É o argumento mais forte contra "o meu p-value é 0.03".
5. **SCCS com períodos de observação dependentes do evento** — a extensão que evita o viés de eventos que encurtam a janela de observação.
6. **Target trial emulation com *clone-censor-weight*** — e a diferença entre *intention-to-treat* emulado e *per-protocol* emulado.
7. **Múltipla imputação vs *complete-case* vs indicador de omissão** — e porque é que o indicador de omissão é quase sempre errado.
8. **Riscos competitivos** — *cause-specific* vs *subdistribution*: a primeira responde "o efeito na taxa instantânea", a segunda "o efeito na incidência cumulativa". Confundi-las é o erro mais comum em análises de idosos.

## 3.5 O que **não** fazer com estes dados

- **Não** faças inferência comparativa com 25 doentes por braço. Nem com 58.
- **Não** publiques um AUROC sem calibração.
- **Não** uses `LIKE '%paracetamol%'`. Já está identificado no teu código (`rwe_queries.py`); corrige-o para *concept set* versionado com `CONCEPT_ANCESTOR`.
- **Não** reportes um `p<0.05` num teste de balanceamento de baseline.
- **Não** compares tabelas 1 com p-values entre estudos diferentes (a significância depende de N; o SMD não).
- **Não** apresentes descobertas clínicas a partir de dados sintéticos. Nem uma.
- **Não** faças 12 testes de subgrupo sem pré-especificação e sem correção.

---

# PARTE IV — Q3: ESTRATÉGIA DE DADOS

## 4.1 O princípio central: **os dados licenciam as afirmações**

Esta é a ideia que organiza tudo o resto. Antes de escolher um dataset, pergunta: **que frase vou poder dizer no fim, e com que autoridade?**

| Afirmação que queres fazer | Dados mínimos necessários |
|---|---|
| "O meu código corre e é reprodutível" | Sintético próprio |
| "O meu estimador recupera o parâmetro verdadeiro com cobertura de 95%" | Sintético **com verdade conhecida** (simulação plasmode) |
| "A minha conversão FHIR→OMOP está correta" | Fonte real **+ conversão de referência** (T1) |
| "A minha análise replica a análise regulatória" | ADaM de referência (T1) |
| "O meu pipeline sobrevive a dados reais sujos" | Dados reais abertos (T1) |
| "Este é um achado clínico" | Dados reais credenciados (T2) |
| "Este achado generaliza" | T2, multi-fonte, com heterogeneidade quantificada |
| "Isto sustenta uma decisão regulatória" | T2 + protocolo pré-registado + calibração + auditoria + DQD |

**A frase que mata 90% dos portefólios de RWE:** apresentar uma conclusão clínica a partir de dados sintéticos. O avaliador sabe, em 10 segundos, que a conclusão é uma propriedade do gerador de dados e não do mundo. E a partir daí lê tudo o resto com suspeita.

**A frase que salva o teu:** dizer isso **antes** de te perguntarem. *"Estes dados são sintéticos, portanto o que eles me permitem afirmar é que o meu estimador é válido — não que o fármaco funciona. Separei deliberadamente as duas coisas: o estudo de métodos corre aqui, o estudo clínico corre em MIMIC."*

## 4.2 O portefólio de dados em quatro camadas

### T0 — Sintético próprio (o que já tens)
**50 doentes (ensaio) e 58 doentes (EHR).**
*Licencia:* correção do código, reprodutibilidade, testes adversariais, verdade conhecida, privacidade total.
*Custo:* zero. *Latência:* imediata.
*Limitação brutal:* não contém nada sobre o mundo.

### T1 — Aberto e real (ou quase) — **é aqui que deves investir agora**
| Dataset | Acesso | Tamanho | O que licencia |
|---|---|---|---|
| **CDISCPILOT01** | GitHub `cdisc-org/sdtm-adam-pilot-project` | 254 aleatorizados; SDTM+ADaM+`define.xml` | Replicação da análise regulatória; estimandos |
| **MIMIC-IV Clinical Database Demo** | PhysioNet, **aberto, sem credenciais** | 100 doentes; versão relacional **+ FHIR (v2.1.0) + OMOP** | Validação diferencial de ETL; robustez em dados reais |
| **Eunomia / GiBleed** | `OHDSI/Eunomia` | 2.694 doentes, CDM 5.5 | Testes de CI do pipeline HADES; *pre-flight* de estudos |
| **SynPUF (DE-SynPUF) em OMOP** | AWS Open Data Registry | 1k / 100k / **2,33 M** doentes | Escala e desempenho de queries; benchmarks |
| **Synthea** | Open | ~1 M doentes sintéticos, FHIR R4 | Volume para testar o pipeline FHIR→OMOP |

**Nota sobre o SynPUF:** o CMS construiu-o **quebrando deliberadamente as relações entre variáveis** para proteger os beneficiários. Consequência: findings clínicos a partir do SynPUF **não têm significado**. É excelente para *método* e *escala*, inútil para *clínica*. Saber isto e dizê-lo é um teste de literacia que muito poucos passam.

### T2 — Credenciado, real — **o próximo salto de credibilidade**
| Dataset | Acesso | Escala | O que licencia |
|---|---|---|---|
| **MIMIC-IV v3.1** | PhysioNet credenciado (CITI + DUA) | ~300 mil+ doentes, UCI de um centro terciário | Epidemiologia clínica, segurança, predição |
| **MIMIC-IV on FHIR v2.1** | PhysioNet credenciado | 315.460 doentes, ~5,84 M recursos, 24+6 perfis | **Testar o teu pipeline FHIR→OMOP a escala real** |
| **MIMIC-IV-ED** | PhysioNet credenciado | ~425 mil episódios de urgência | Trajetórias agudas, adesão pós-alta |
| **eICU-CRD** | PhysioNet credenciado | ~200 mil internamentos em 200+ UCI | **Multi-centro → heterogeneidade → sinergia com o desenho federado** |
| **NHANES** | Público | ~10 mil/ ciclo, transversal | Prevalência populacional, fatores de risco |
| **All of Us** | *Researcher Workbench* | Centenas de milhares, diverso | Generalização, equidade |

### T3 — Licenciado (Optum, MarketScan, CPRD, TriNetX, IQVIA)
Sem acesso. Servem para **arquitetura**: desenhar as interfaces e as *specs* como se fosses correr nelas. Nunca finjas que tens.

## 4.3 Quando o sintético é **superior** — e não é um mal menor

Há cinco situações em que o sintético não é o plano B, é o plano A:

1. **Verdade conhecida.** Só com dados gerados sabes o parâmetro verdadeiro. Se queres demonstrar que o teu estimador é válido — viés, cobertura, erro quadrático médio — precisas de sintético. **Não há alternativa.**
2. **Testes adversariais.** Introduzir violações deliberadas: datas impossíveis, unidades trocadas, códigos órfãos, `concept_id = 0` misturado com `NULL`. Nos dados reais não escolhes os erros.
3. **CI e reprodutibilidade.** Um teste que corre em 30 segundos com 50 doentes pode estar em cada commit. Um que exige uma DUA, não.
4. **Casos-limite raros.** Queres testar o comportamento com 100% de abandono, ou com um só doente, ou com todos os valores nulos. Não encontras isso num dataset real.
5. **Privacidade e partilha.** Podes publicar o dataset, o que significa que o revisor pode correr o teu código. Isso vale mais do que parece.

## 4.4 Quando **precisas** de real — e o que custa

Precisas de dados reais quando a afirmação envolve o mundo: epidemiologia, segurança, efetividade, predição, generalização. Não há como contornar.

**O custo real, honestamente contabilizado:**
- **MIMIC-IV demo:** 0 €, ~1 hora (download + carga em DuckDB). **O melhor retorno por hora de todo este plano.**
- **MIMIC-IV completo:** 0 €, mas exige *PhysioNet credentialed access* — formação CITI em proteção de participantes humanos, submissão de DUA, aprovação que pode levar dias a semanas. Deves **começar o processo hoje**, mesmo que só uses os dados dentro de dois meses.
- **MIMIC-IV on FHIR completo:** a mesma credencial, mais um download de vários GB em NDJSON.

**Restrições que tens de respeitar e saber recitar:** não redistribuir; não tentar re-identificar; usar apenas em ambiente controlado; citar corretamente; respeitar o âmbito do DUA. Violar isto termina a carreira de alguém, e num entrevista é uma pergunta de triagem.

## 4.5 **ETL-through vs pré-mapeado: a regra de decisão**

A pergunta que fizeste é excelente, e a resposta não é "depende" — é uma regra.

> **Se o ETL é a afirmação, passa os dados pelo teu pipeline. Se a análise é a afirmação, usa os dados pré-mapeados.**

E um corolário ainda mais importante:

> **Nunca passes semanas a mapear um dataset que já tem conversão oficial, para no fim correr uma regressão.**

Aplicando aos teus casos:

| Situação | Escolha | Porquê |
|---|---|---|
| Queres provar que o teu pipeline FHIR→OMOP funciona | **ETL-through**: MIMIC-IV-on-FHIR → o teu pipeline | O ETL é a afirmação |
| Queres estudar *informed presence* em 26 anos de dados | **Pré-mapeado** (ou o teu Projeto B já mapeado) | A análise é a afirmação |
| Queres testar `CohortMethod` | **Pré-mapeado**: MIMIC-CDM, Eunomia | A metodologia é a afirmação |
| Queres um estudo clínico credível | **Pré-mapeado**: MIMIC-IV v3.1 | O achado é a afirmação |
| Queres demonstrar rigor de ingestão ponta-a-ponta | **ETL-through**: CDISCPILOT01 raw → SDTM → ADaM | O ETL é a afirmação |

### ⭐ 4.5.1 A proposta forte: **VALIDAÇÃO DIFERENCIAL**

Aqui está a jogada que eu faria primeiro, e que provavelmente ninguém no teu painel já viu.

**O facto que torna isto possível:** o *MIMIC-IV Clinical Database Demo* (100 doentes, derivado do MIMIC-IV Demo v2.2) existe em **três representações independentes**:
1. **Relacional nativo** (`mimiciv_hosp`, `mimiciv_icu`)
2. **FHIR R4** — 100 doentes, ~915 mil recursos, NDJSON, perfis MIMIC
3. **OMOP CDM** — conversão do OHDSI (`OHDSI/MIMIC`), 100 doentes

**Logo:** podes pegar na representação **FHIR**, passá-la pelo teu pipeline FHIR→OMOP, e comparar o resultado com a **conversão de referência do OHDSI** — que partiu dos *mesmos doentes* mas por um caminho completamente diferente.

**Isto não é uma validação contra o vazio. É uma validação contra um par.** Duas implementações independentes do mesmo problema, comparadas doente a doente.

**O que medes — sete métricas, definidas antes de correr:**

| # | Métrica | Como |
|---|---|---|
| 1 | **Cobertura de registos** | Linhas de origem → linhas mapeadas → não mapeadas, com razões |
| 2 | **Integridade referencial** | Órfãos em `*_occurrence`, `person_id` inexistente, visitas sem doente |
| 3 | **Concordância de conceito** | Por domínio: % de `*_concept_id` iguais; **κ de Cohen**; matriz de confusão dos top-20 conceitos |
| 4 | **Concordância de valores** | `value_as_number` e `unit_concept_id`: viés médio e limites de concordância de Bland-Altman |
| 5 | **Concordância temporal** | Diferença em dias entre datas equivalentes; % dentro de ±1 dia |
| 6 | **DQD lado a lado** | Mesmos checks, dois CDMs, taxa de aprovação comparada |
| 7 | **Atrição por domínio** | Quantos doentes ficam com pelo menos um registo em cada tabela |

**O entregável:** um relatório com uma tabela de concordância por domínio, um *heatmap* de discordância de conceitos, e uma **lista de causas-raiz das divergências** — que é a parte mais valiosa, porque revela *decisões de modelação*, não bugs.

**Exemplos do tipo de achado que vais encontrar, e do que ele significa:**
- O FHIR não impõe uma representação única para "medicação administrada" vs "prescrita". O teu pipeline pode mapear `MedicationAdministration` para `DRUG_EXPOSURE`, enquanto a referência mapeia `MedicationRequest`. **Nenhum está errado** — são semânticas diferentes. Documentar isto é ouro, porque é exatamente o problema que a Promptly, a EHDEN e a DARWIN EU enfrentam a cada novo parceiro de dados.
- Unidades: o FHIR transporta `Quantity` com `unit` e `code`; o OMOP exige `unit_concept_id`. Uma unidade não mapeada produz `unit_concept_id = 0`. Quantos? Isso é uma métrica de qualidade, não um detalhe.
- Datas: `Encounter.period` vs `VISIT_OCCURRENCE.visit_start_date`. Fuso horário, granularidade, valores nulos.

**Porque é que isto vale mais do que qualquer outro estudo do portefólio:**
- Prova **engenharia medida** — não "construí", mas "medi a correção da construção".
- É replicável por qualquer pessoa (dados abertos) — logo, é *verificável*.
- É **publicável** no simpósio europeu do OHDSI. Há espaço real para um *methods abstract* sobre validação diferencial de conversões.
- Fala diretamente à dor comercial de uma rede federada: *"como sei eu que o ETL do novo parceiro é bom?"* — a resposta é: comparando contra uma referência quando existe, e contra um perfil esperado quando não existe.

**Tempo estimado:** 2 a 3 fins de semana. **Custo:** zero. **Risco:** baixo.

### 4.5.2 O segundo uso: validar o Projeto 1 contra um gabarito

O mesmo padrão aplica-se ao CDISC: pega no `CDISCPILOT01`, corre **o teu pipeline raw→SDTM→ADaM**, e compara com os ADaM oficiais. Métricas: contagem de linhas por dataset, igualdade variável a variável, e — o mais interessante — **% de valores derivados que coincidem**.

Aqui há um detalhe que deves antecipar: **vais divergir**, e as divergências são o conteúdo. Um ADSL oficial tem decisões de *população de análise* (quem é `SAFFL`, quem é `ITTFL`, como se tratam os *screen failures* — 306 rastreados, 254 aleatorizados). Se o teu pipeline produzir 306 no ADSL e o oficial 254, não é um bug: é uma diferença de definição de população. **Documentar isso é mais valioso do que acertar à primeira.**

## 4.6 A rede federada num só portátil

Esta é a segunda ideia original, e a que tem mais potencial de conversa em entrevista.

**O problema.** Ninguém te vai dar acesso a uma rede federada para tu aprenderes. Mas quase toda a gente te vai perguntar: *"já trabalhaste em ambiente federado?"*

**A solução.** Simula-a. Os teus 58 doentes fragmentam-se em 3 "nós" por `hash(subject_id)`, com sobreposição de perfis de doente deliberadamente diferente entre nós (para criar heterogeneidade realista).

Depois:
1. Escreve o estudo como uma **spec declarativa em JSON** — cohorts (Circe), janelas, análises, covariáveis, método de ajuste. Formato inspirado no `Strategus`.
2. Corre a mesma spec em cada nó, localmente em DuckDB, sem que nenhum nó veja os dados dos outros.
3. Agrega os **resultados sumariais** com meta-análise (`EvidenceSynthesis` ou implementação própria): efeito por nó, I², τ², **intervalo de previsão**, *forest plot*.
4. Mostra que o código é **literalmente o mesmo nos três nós** — só muda a *connection string*.

**O que isto demonstra, e porque é exatamente o que a DARWIN EU compra:** a única coisa que viaja entre nós é o **código** e os **resultados agregados**. A mediana de 4,8 meses protocolo→resultado da DARWIN EU só é possível porque o estudo é uma spec e não um script artesanal. Mostrar que sabes estruturar um estudo assim — mesmo sobre 58 doentes — é mostrar que entendes o modelo operacional.

**E há um bónus:** podes usar o eICU-CRD como "multi-centro real" mais tarde (200+ UCI, um só credential), o que dá heterogeneidade genuína em vez de simulada. Aí a demonstração passa de metáfora a dados.

## 4.7 Considerações que não são burocracia

- **PhysioNet:** começa o processo de credenciação **esta semana**. CITI training + DUA. Não custa dinheiro, custa tempo de calendário, e é o único passo que te separa de MIMIC completo.
- **Não redistribuas** MIMIC em nenhum repositório público. Mantém os dados fora do Git (o teu repo já ignora `data/`? Confirma), e versiona apenas o **código** e os **resultados agregados**.
- **Cita corretamente** (DOI de versão), porque os revisores verificam.
- **Mostra o `cdm_source`** de cada base de dados no relatório: nome, versão do CDM, versão do vocabulário, data de release. É o equivalente ao "materials and methods" dos dados.
- **Regista o teu `vocabulary_signature`.** O teu repo já tem uma função que calcula um sha256 sobre a versão do schema, os vocabulários, a contagem e o intervalo de `concept_id`, e um `BIT_XOR(HASH(...))`. **Isto é raro e é um diferenciador real**: quase nenhum estudo OMOP liga os resultados a uma assinatura de vocabulário. Usa-o em todos os relatórios.

## 4.8 O programa de aquisição de dados em 90 dias

| Semana | Ação | Entregável |
|---|---|---|
| 1 | Iniciar credenciação PhysioNet (CITI + DUA) | Submissão feita |
| 1 | Descarregar MIMIC-IV Demo (relacional + FHIR + OMOP) | Três CDMs locais em DuckDB |
| 2–4 | **X1 — Validação diferencial** | Relatório #1 |
| 3 | Descarregar CDISCPILOT01 | SDTM/ADaM em DuckDB/Parquet |
| 5–6 | **P1.1 — Estimand Lab** | Relatório #2 |
| 7–8 | **P2.4 — Custo do RWD** | Relatório #3 |
| 7 | Credenciação MIMIC completo (se aprovada) | Acesso a v3.1 + FHIR + eICU |
| 9–10 | **P3.3 — Informed presence** | Relatório #4 |
| 11 | **X2 — Rede federada num portátil** | Demo + *forest plot* |
| 12 | Consolidação: site, README, 4 vídeos de 90s | Portefólio |

**Nota:** as semanas 2–10 assumem trabalho paralelo. Se só tiveres ~10 horas por semana, isto é um plano de 5–6 meses, não de 3. Sê honesto contigo sobre a capacidade — um estudo terminado vale mais do que quatro a meio.

---

# PARTE V — Q4: COMO APRESENTAR PARA DEMONSTRAR RIGOR DE NÍVEL MUNDIAL

## 5.1 A unidade de entrega é o **pacote de estudo**, não o notebook

Um notebook responde a "o que fizeste?". Um pacote de estudo responde a "posso confiar nisto, e posso correr noutro sítio?".

A diferença não é estética. É a diferença entre um artefacto que alguém **audita** e um artefacto que alguém **lê**.

## 5.2 A anatomia de um pacote de estudo

```
study_<nome>/
├── README.md                     # 1 página: pergunta, dados, resultado principal, como correr
├── protocol/
│   ├── protocol.md               # pré-especificado, datado
│   ├── estimand.md               # 5 atributos ICH E9(R1)
│   └── sap.md                    # análise primária, secundárias, sensibilidade
├── cohorts/
│   ├── *.json                    # Circe / concept sets, versionados
│   └── manifest.csv              # id, nome, racional, versão
├── sql/
│   ├── 00_cdm_checks.sql
│   ├── 10_cohorts.sql
│   ├── 20_attrition.sql
│   └── 30_analysis.sql
├── src/                          # orquestração (R ou Python)
├── results/
│   ├── attrition.csv
│   ├── table1.csv
│   ├── estimates.csv
│   └── diagnostics/
├── reports/
│   ├── report.md / .html / .pdf
│   └── figures/
├── logs/
│   └── analysis_log.md           # decisões, tentativas abandonadas, motivos
├── manifest.json                 # versões, hashes, vocabulary_signature, tempo
└── tests/
```

**Três coisas neste layout que quase ninguém tem e que valem muito:**

**`protocol/` preenchido antes de correr a análise.** Isto é o artefacto mais impressionante de todos. Um protocolo de 2–3 páginas, datado, com hipótese, estimando, análise primária, análises de sensibilidade e — a parte que ninguém faz — uma secção **"o que me faria abandonar esta análise"**. Se o resultado violar essa condição, tens de o reportar. Isso é a marca de quem não está a fazer *p-hacking*.

**`logs/analysis_log.md`.** Um registo de decisões: "testei a janela de washout a 90, 180 e 365 dias; escolhi 180 porque X; a 90 o N caía para 12 e o IC tornava-se ininterpretável". Isto é o que separa uma análise honesta de uma análise que parece honesta. E é extraordinariamente raro.

**`manifest.json` com `vocabulary_signature`.** Liga o resultado a um estado exato do vocabulário. É a única forma de garantir que o estudo é reproduzível daqui a dois anos, quando o Athena publicar outra versão.

## 5.3 Os seis artefactos que separam amador de profissional

| # | Artefacto | O que prova | Como falha o amador |
|---|---|---|---|
| 1 | **Protocolo pré-especificado datado** | Não fizeste *p-hacking* | Analisa primeiro, escreve depois |
| 2 | **Tabela de atrição por passo** | Sabes exatamente quem entrou e quem saiu | Reporta só o N final |
| 3 | **Tabela 1 com SMD + love plot** | Sabes avaliar confundimento | Usa p-values, que dependem do N |
| 4 | **Controlos negativos + calibração** | Sabes avaliar se o teu estudo é interpretável | Reporta o p-value como se fosse prova |
| 5 | **Análises de sensibilidade + E-value** | Sabes o que te pode derrubar | Uma análise, um resultado |
| 6 | **Manifesto de reprodutibilidade** | Alguém pode correr isto | "Funciona na minha máquina" |

**Sobre o #3, com precisão.** O **SMD** (diferença de médias padronizada) não depende do tamanho da amostra. Isso tem duas consequências: (a) não fica "significativo" só porque tens 2 milhões de doentes — como acontece com os p-values em estudos de claims; (b) **é comparável entre nós de uma rede federada**, porque um nó com 50 mil doentes e outro com 5 milhões produzem o mesmo SMD para o mesmo desequilíbrio. Num contexto federado, o SMD não é só melhor — é a **única** métrica de balanceamento que viaja. Dizer isto numa entrevista é um sinal forte.

**Sobre o #4, com precisão.** Um estudo observacional sem distribuição nula conhecida não é interpretável, porque não sabes se o teu método produz falsos positivos naquele dataset. Os controlos negativos dão-te essa distribuição: escolhes desfechos onde sabes que o efeito é nulo, estimas, e verificas se a tua distribuição de p-values é uniforme. Se não for, calibras empiricamente. E podes reportar **o erro sistemático e o erro de aleatorização** estimados (as duas componentes que a `EmpiricalCalibration` separa). Isto é o padrão-ouro do OHDSI e é o que distingue um estudo que *parece* rigoroso de um que *é* rigoroso.

## 5.4 A estrutura do relatório — as doze secções que um revisor espera

1. **Título + registo** (onde está pré-registado, data, versão do protocolo)
2. **Resumo em linguagem simples** (5 frases, sem jargão, sem números)
3. **Contexto e racional** (porque é que esta pergunta importa clinicamente)
4. **Objetivos e estimando** (população, exposição, comparador, desfecho, eventos intercorrentes, medida sumária)
5. **Fonte de dados e proveniência** (CDM, versão, vocabulário + assinatura, DQD sumário, limitações conhecidas da fonte)
6. **Construção da coorte e atrição** (C1 → C2 → C3 → C4 com N em cada passo)
7. **Métodos** (desenho, confundimento, omissão, riscos competitivos, sensibilidade)
8. **Resultados** (Tabela 1 com SMD; resultado primário; secundários)
9. **Diagnósticos** (controlos negativos, calibração, balanceamento, proporcionalidade)
10. **Sensibilidade** (análise principal replicada sob cada decisão contestável, + E-value)
11. **Discussão e limites** (ROBINS-I domínio a domínio; o que este estudo **não** pode dizer)
12. **Apêndice de reprodutibilidade** (versões, hashes, tempo de execução, como correr)

**Uma regra de ouro para a secção 11:** escreve tu a crítica mais dura que consegues imaginar ao teu próprio estudo, e responde-lhe. Se um revisor encontrar uma crítica que tu não antecipaste, perdeste. Se encontrar todas e tu já as respondeste, ganhaste — mesmo que o resultado seja fraco.

## 5.5 O mapa de standards — mostra que sabes qual se aplica a quê

| Norma | Aplica-se a | Onde no relatório |
|---|---|---|
| **STROBE** | Estudos observacionais | Estrutura geral |
| **RECORD** | Dados de saúde recolhidos rotineiramente | Proveniência, linkage, código de fenótipos |
| **RECORD-PE** | Farmacoepidemiologia | Definição de exposição, janelas, confundimento |
| **GRACE** | Boas práticas em RWE | Checklist de qualidade do estudo |
| **HARPER** | RWE em decisão de saúde | Transparência e reprodutibilidade |
| **ROBINS-I** | Risco de viés em estudos não aleatorizados | Secção 11 |
| **ICH E9(R1)** | Definição do estimando | Secção 4 e protocolo |
| **TRIPOD+AI / PROBAST+AI** | Modelos preditivos (se algum dia) | Modelo e validação |
| **ENCEPP** | Protocolo de estudo pós-autorização (UE) | Modelo de protocolo |
| **CDISC SDTM/ADaM/Define-XML** | Projeto 1 | Todo |

**Não cites todos em todos os estudos.** Cita os que se aplicam, e diz porquê. Um relatório que invoca sete standards parece inseguro; um que invoca dois, com justificação, parece competente.

## 5.6 A estratégia de prova pública

Ordem de importância, do mais para o menos impactante:

1. **Quatro relatórios de estudo completos, cada um com uma página de resumo** — legíveis em 10 minutos cada.
2. **Os repositórios**, com o README a dizer em três linhas qual é a pergunta e qual é o resultado principal.
3. **Quatro vídeos de 90 segundos** — um por estudo, ecrã + voz, a explicar a pergunta, o método e o resultado. Isto é desproporcionadamente eficaz: um Lead vê 90 segundos, quase nunca lê um relatório de 20 páginas.
4. **Uma página de portefólio** que diz: que músculo cada estudo prova.
5. **Um *methods abstract* submetido ao simpósio europeu do OHDSI** (validação diferencial ou *informed presence*). Mesmo que não seja aceite, o processo força-te a escrever para revisores — e um "submetido" no CV é verificável.

**O que NÃO fazer:** um repositório com 40 notebooks sem README, chamado "RWE-Playground". Isso transmite o oposto de rigor: transmite entusiasmo sem direção, e um Lead lê isso como "vai levar 6 meses a produzir algo que eu possa mostrar a um cliente".

## 5.7 O pitch de 90 segundos — template

> **Pergunta.** "O estudo X pergunta [pergunta clínica em uma frase]."
> **Porquê.** "Importa porque [consequência clínica ou regulatória]."
> **Dados.** "[Fonte], [N], [seguimento], CDM [versão], vocabulário [versão + assinatura]."
> **Desenho.** "[New-user / SCCS / TTE], com [ajuste]. O estimando é [5 atributos em 1 frase]."
> **Resultado.** "[Estimativa] ([IC 95%])."
> **Rigor.** "Antes de olhar para o resultado, pré-especifiquei [X]. Corri [N] controlos negativos; a calibração mostrou [Y]. A análise não sobrevive a um confundidor de [E-value], o que é [plausível/não plausível] porque [razão]."
> **Limite.** "O que isto **não** pode dizer é [limitação]. E é por isso que o próximo passo seria [Z]."

A parte que ganha é a última. Candidatos falam do que fizeram; seniores falam do que os seus resultados não provam.

## 5.8 Anti-padrões que destroem credibilidade

- **"Estatisticamente significativo"** sem estimativa e IC. (E nunca "ns".)
- **AUROC sem calibração.**
- **p-value num teste de balanceamento.**
- **Conclusão clínica a partir de sintético.**
- **"Correlação de Pearson" para concordância** — devia ser ICC ou Bland-Altman.
- **Fenotipagem por `LIKE '%...%'`** em vez de concept sets.
- **Misturar `concept_id = 0` com `NULL`** (o teu repo já distingue — mantém).
- **Nenhuma menção a confundimento não medido.**
- **Gráficos com eixos truncados, 3D, ou *pie charts* com 12 fatias.**
- **Um "pipeline" que só corre na tua máquina.**
- **Dizer "usei IA" sem dizer para quê, com que validação e com que taxa de erro.**

---

# PARTE VI — PLANO DE 12 SEMANAS

Assumindo ~10–12 h/semana. Cada semana termina num artefacto verificável, não numa intenção.

| Semana | Foco | Entregável concreto |
|---|---|---|
| 0 | Credenciação PhysioNet + downloads | MIMIC Demo (3 formas) e CDISCPILOT01 em DuckDB |
| 1 | Template do pacote de estudo + `manifest.json` + `analysis_log.md` | Template reutilizável, testado num estudo vazio |
| 2 | X1: carregar FHIR demo, correr o teu pipeline | OMOP próprio a partir de FHIR, com DQD |
| 3 | X1: comparação com o OMOP de referência | Tabela das 7 métricas |
| 4 | X1: causas-raiz + relatório | **Relatório #1** + *abstract* OHDSI |
| 5 | P1.1: definir evento intercorrente em `DS`/`AE` | Especificação escrita do estimando |
| 6 | P1.1: 4 estratégias + IC bootstrap + relatório | **Relatório #2** |
| 7 | P2.4: construir os degraus D0–D6 | Código parametrizado por degrau |
| 8 | P2.4: 200 réplicas + viés e cobertura | **Relatório #3** com *forest plot* da escada |
| 9 | P3.3: modelo de intensidade de visita | Perfil de observabilidade |
| 10 | P3.3: ingénuo vs IIW vs outputation | **Relatório #4** |
| 11 | X2: fragmentação + spec JSON + síntese | Demo federada + *forest plot* |
| 12 | Consolidação | Site, README, 4 vídeos de 90s, CV atualizado |

**Pontos de decisão:** se na semana 4 o X1 estiver atrasado, **corta o P1.1 para metade** em vez de cortar o X1. A validação diferencial é a peça mais rara e mais difícil de replicar por outro candidato.

---

# PARTE VII — POSICIONAMENTO DE MERCADO

## 7.1 A frase

> **"Construo e valido a camada analítica que transforma um nó OMOP num estudo auditável — e consigo provar que a conversão que está por baixo não está a mentir."**

Repara no que ela faz: não diz "sei SQL" nem "sei OMOP". Diz que resolves um problema que quem contrata tem e não consegue resolver. Toda a gente sabe fazer ETL; quase ninguém sabe **medir se o ETL está certo** e **transformá-lo num estudo que aguenta revisão**.

## 7.2 Os arquétipos de empregador, e o que cada um compra

| Arquétipo | Exemplos ilustrativos | O que compra | Qual dos teus estudos é a prova |
|---|---|---|---|
| **Rede federada / data partner** | Promptly, nós EHDEN, centros DARWIN EU | Portabilidade, governação, DQD, mapeamento de vocabulário | **X1** + **X2** |
| **Software/analytics de RWE** | Aetion, TriNetX, COTA | Velocidade, replicabilidade, pensamento de produto | **X1** + **P3.8** |
| **Consultoria a pharma / HTA** | IQVIA, Parexel, Certara, Putnam | Estimandos, prontidão para JCA, disciplina de TLF | **P1.1** + **P2.4** |
| **CRO / statistical programming** | ICON, Syneos, Fortrea | CDISC, dupla programação, `define.xml` | **P1.2** + **P1.4** |
| **Payers / HTA / regulador** | NICE, AIFA, EMA | Análise de viés, calibração, incerteza | **P2.4** + **P3.3** |

**A implicação estratégica:** não podes otimizar para os cinco. O teu perfil atual (três pipelines + vocabulário governado + DQD) está mais próximo do **primeiro** arquétipo. Os estudos X1/X2/P3.3 consolidam essa posição. O P1.1 e o P2.4 dão-te opcionalidade para o terceiro — que é onde estão os salários mais altos e a barreira de entrada mais alta.

## 7.3 Porque é que a janela está aberta agora

Três pressões de mercado que estão a criar exatamente a procura que tu podes servir:

1. **A JCA europeia exige submissão em 100 dias.** Isso mata estudos artesanais. Só sobrevive quem tem *specs* e pipelines reutilizáveis.
2. **O EHDS vai multiplicar o número de nós de dados.** Cada novo nó é um problema de *onboarding* — e onboarding é exatamente mapeamento de vocabulário + DQD + validação. O OHDSI/UMC fez isso em 5 dias com `Strategus`; esse é o novo benchmark.
3. **A FDA deixou de exigir dados individuais identificáveis** (dezembro de 2025). Isso torna a evidência federada e des-identificada submetível. A arquitetura que construíste passa de "academicamente elegante" a "comercialmente necessária".

## 7.4 O que o teu portefólio tem de provar a cada entrevistador

Em 60 segundos, o teu interlocutor quer responder a três perguntas silenciosas:

1. **"Ele sabe o que é um estimando?"** → P1.1
2. **"Ele sabe quando os dados o estão a enganar?"** → P2.4 + P3.3
3. **"Ele consegue medir a qualidade da sua própria engenharia?"** → X1

Se as três respostas forem sim, o resto é negociação.

---

# PARTE VIII — RISCOS, TRADE-OFFS, E O QUE EU CORTARIA

## 8.1 Os riscos reais

| Risco | Probabilidade | Mitigação |
|---|---|---|
| **Dispersão** — cinco estudos a 60% | **Alta** | Regra dos quatro. Nada de começar o #5 antes de fechar o #4 |
| **X1 revelar que o teu pipeline tem falhas grandes** | Média | É o ponto. Um relatório que encontra e corrige 6 divergências vale mais do que um que diz "0 divergências" (que ninguém acredita) |
| **Credenciação PhysioNet atrasar** | Média | Todo o plano T1 funciona sem ela. Só o P3.10 e o eICU dependem |
| **P3.3 produzir estimativas ininterpretáveis com n=58** | **Alta** | Enquadra como estudo de viabilidade; o entregável é o procedimento, não o número |
| **P2.4 consumir mais tempo do que previsto** | Média | Começa por D0–D3 (os quatro degraus que importam); D4–D6 são extensões |
| **Sobreposição com o dossiê anterior** | Baixa | O dossiê é referência teórica; isto é execução. Não os fundas num só documento |

## 8.2 O que eu cortaria, com justificação

- **P1.3 (TLF completo)** — cortaria para apêndice. É competência de CRO, não de RWE, e consome semanas.
- **P3.10 (predição)** — adiaria até ter MIMIC completo. Com 58 doentes é contraproducente.
- **P1.4 (`define.xml` completo)** — faria a versão mínima. O sinal já é dado pelo `sdtmchecks`; o benefício marginal é baixo.
- **VBHC / PROMs (B8 do dossiê)** — cortaria por agora. Sem dados de PROMs, é conversa. Retoma quando tiveres uma fonte.
- **Qualquer estudo sobre Kaggle** — cortaria. Datasets de proveniência desconhecida não sobrevivem à primeira pergunta séria ("de onde vêm estes doentes?"), e a proveniência é precisamente o produto que estás a vender.

## 8.3 As três decisões que eu tomaria hoje

1. **Iniciar a credenciação PhysioNet hoje.** É o único item com latência de calendário e custo zero.
2. **Descarregar os três MIMIC Demo e o CDISCPILOT01 hoje.** Em duas horas tens a matéria-prima de dois dos quatro estudos-bandeira.
3. **Escrever o protocolo do X1 antes de tocar no código.** Se não conseguires escrever o protocolo, o estudo não está suficientemente bem definido para o começares — e essa é a lição que o próprio protocolo existe para te dar.

## 8.4 A frase final

O teu portefólio atual diz: *"sei construir a infraestrutura"*.
O portefólio que este documento descreve diz: *"sei o que a infraestrutura vale, quanto erra, e como provar isso a um revisor."*

A distância entre as duas frases não é mais código. É **protocolo, atrição, diagnóstico e sensibilidade** — quatro coisas que custam pouco e que quase ninguém faz.

---

# APÊNDICE A — RUNBOOK DE ACESSO AOS DADOS

```bash
# 1. CDISC Pilot 01 (público, sem registo)
git clone --depth 1 https://github.com/cdisc-org/sdtm-adam-pilot-project.git
#   SDTM: updated-pilot-submission-package/900172/m5/datasets/cdiscpilot01/tabulations/sdtm
#   ADaM: updated-pilot-submission-package/900172/m5/datasets/cdiscpilot01/analysis/adam/datasets
#   define.xml em ambas as camadas

# 2. MIMIC-IV Clinical Database Demo (aberto, sem credenciais)
#    Relacional:  https://physionet.org/content/mimiciv-demo/
#    FHIR:        https://physionet.org/content/mimic-iv-fhir-demo/2.1.0/
#    OMOP:        https://physionet.org/content/mimic-iv-demo-omop/0.9/

# 3. Eunomia (DuckDB, instantâneo)
#    install.packages("Eunomia")  |  CDMConnector::eunomiaDir()

# 4. SynPUF em OMOP (1k / 100k / 2.33M)
#    AWS Open Data Registry: cmsdesynpuf-omop

# 5. MIMIC-IV completo — requer credenciação
#    https://physionet.org/content/mimiciv/3.1/
#    Pré-requisito: CITI "Data or Specimens Only Research" + DUA
```

**Carga em DuckDB (exemplo):**
```sql
INSTALL duckdb; LOAD duckdb;
CREATE TABLE person AS SELECT * FROM read_csv_auto('.../person.csv');
-- ou, para NDJSON FHIR, carregar e achatar com json_extract
```

# APÊNDICE B — PACOTES POR CAMADA

| Camada | R | Python |
|---|---|---|
| Ligação ao CDM | `CDMConnector`, `omopgenerics` | `sqlalchemy` + DuckDB |
| Coortes | `CohortGenerator`, `CapR`, `CirceR` | SQL / `polars` |
| Caracterização | `Characterization`, `CohortCharacteristics`, `DrugUtilisation` | — |
| Incidência/prevalência | `IncidencePrevalence`, `CohortIncidence` | — |
| Estimação causal | `CohortMethod`, `SelfControlledCaseSeries` | `DoWhy`, `EconML` |
| Síntese | `EvidenceSynthesis` | `statsmodels` |
| Diagnóstico | `CohortDiagnostics`, `PhenotypeR`, `DataQualityDashboard` | — |
| Sobrevivência | `survival`, `riskRegression`, `cmprsk` | `lifelines` |
| Modelos mistos | `lme4`, `nlme`, `mmrm` | `statsmodels` MixedLM |
| TLF | `rtables`, `chevron`, `gtsummary`, `cards` | `tableone` |
| ADaM/SDTM | `admiral`, `metacore`, `xportr`, `sdtmchecks` | — |

# APÊNDICE C — MODELO DE UM CONCEPT SET VERSIONADO

```json
{
  "id": "cs_alt_elevation_v1",
  "name": "Elevated ALT (any descendant)",
  "version": "1.0.0",
  "domains": ["Measurement"],
  "includeDescendants": true,
  "includeMapped": false,
  "exclude": [],
  "concepts": [
    {"concept_id": 3013721, "concept_name": "Alanine aminotransferase [Enzymatic activity/volume] in Serum or Plasma",
     "vocabulary_id": "LOINC", "include_descendants": true, "include_mapped": false}
  ],
  "notes": "Não atravessar vocabulários. Respeitar min/max_levels_of_separation quando aplicável. SELECT DISTINCT obrigatório com CONCEPT_ANCESTOR."
}
```

# APÊNDICE D — CHECKLIST DE PRÉ-REGISTO

- [ ] Pergunta em uma frase, com população, exposição, comparador, desfecho
- [ ] Estimando completo (5 atributos ICH E9(R1))
- [ ] Análise primária, com o modelo escrito por extenso
- [ ] Análises secundárias (lista fechada)
- [ ] Análises de sensibilidade (lista fechada)
- [ ] Poder/precisão **declarados a priori** — ou a assunção de que é exploratório
- [ ] Definição da janela de *washout*, do *time zero*, do tempo em risco
- [ ] Definição do tratamento da omissão
- [ ] Definição do tratamento de riscos competitivos
- [ ] Controlos negativos (quais, e porque são negativos)
- [ ] **"O que me faria abandonar esta análise"**
- [ ] Data e versão do protocolo

# APÊNDICE E — FONTES

**Regulamentação**
- FDA, *Use of Real-World Evidence to Support Regulatory Decision-Making for Medical Devices*, orientação final, 18 dez 2025 (supersede a de 2017; implementação 17 fev 2026) — `fda.gov/media/190201`
- FDA, comunicado de 15 dez 2025 sobre aceitação de RWE sem dados individuais identificáveis — `fda.gov/news-events/press-announcements`
- Regulamento (UE) 2021/2282 (HTA); HTACG *Annual Work Programme 2026*; primeira JCA aprovada 30 abr 2026 (tovorafenibe), primeiro relatório publicado 9 jun 2026 — `health.ec.europa.eu`
- Regulamento (UE) 2025/327 (EHDS): em vigor 26 mar 2025; aplicação geral 26 mar 2027; uso secundário 26 mar 2029; ensaios/genómica 26 mar 2031 — `health.ec.europa.eu`
- EMA, *Real-world evidence framework to support EU regulatory decision-making — 4.º relatório* (8 fev 2025 – 7 fev 2026): 108 tópicos, 88 estudos, mediana 4,8 meses, parceiros 3→6, viabilidade ~78–80%

**Dados**
- CDISC SDTM/ADaM Pilot Project (`cdisc-org/sdtm-adam-pilot-project`): 306 rastreados, 254 aleatorizados, xanomelina, ADAS-Cog(11) à semana 24
- MIMIC-IV — `physionet.org/content/mimiciv/3.1/`; demo aberto `mimiciv-demo`
- MIMIC-IV on FHIR v2.1 — 315.460 doentes, ~5,84 M recursos; demo 2.1.0 com 100 doentes e ~915 mil recursos — `physionet.org/content/mimic-iv-fhir/2.1/`
- MIMIC-IV Demo em OMOP CDM (OHDSI/MIMIC), CDM 5.3.1, 100 doentes — `physionet.org/content/mimic-iv-demo-omop/0.9/`
- `CogStack/dbt_mimic_omop` — pipeline dbt+DuckDB para MIMIC-IV→OMOP 5.4; *BIONLP 2026*, `aclanthology.org/2026.bionlp-1.80`
- Eunomia / GiBleed (2.694 doentes, CDM 5.5); DE-SynPUF 1k/100k/2,33 M em OMOP (AWS Open Data Registry)

**Métodos e tooling**
- OHDSI HADES: `CohortMethod` 6.0.1 (CRAN, mar 2026), `Strategus` 1.5.x, `EvidenceSynthesis`, `CohortDiagnostics` — `ohdsi.github.io/Hades`
- OHDSI + Uppsala Monitoring Centre: estudo completo com `Strategus` em 5 dias sobre registos suecos — `ohdsi.org/community-calls-2026`
- *Electronic Health Records in Epidemiology: Appropriate Questions, Common Biases, and Potential Sensitivity Analyses* — `Springer, Curr Epidemiol Rep, 2025`
- *Informed presence in electronic health record data: illustrating bias and bias reduction approaches in longitudinal analyses* (2026): IRR 1,83 (1,02–3,28) → 1,37 (0,73–2,57) com *inverse intensity weighting* — `PubMed 41849238`
- *Mitigating bias in the analysis and inferences from using longitudinal EHR data* (2026): número de registos de peso como preditor mais forte de 10 desfechos cardiovasculares — `BMC Med Res Methodol`
- *Informative presence bias in analyses of EHR-derived data: a cautionary note* — `PMC9196698`

---

*Documento complementar a `RWE_ANALYTICS_DOSSIER.md` (métodos estudo-a-estudo dos Projetos A e B) e a `PROMPTLY_HEALTH_INTERVIEW_PREP.md` (preparação de entrevista).*
