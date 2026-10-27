# Parecer de Revisão por Pares
## Arquitetura de 4 Pilares — RWE · CDISC · OMOP CDM
**Submetido a:** revisão crítica independente
**Objeto:** ecossistema de 4 pilares para portefólio e preparação da entrevista técnica na Promptly Health (João Fonseca, Data Lead; Max Gleichauf, BD)
**Data do parecer:** outubro de 2026
**Recomendação:** **REVISÃO MAJOR** — arquitetura sólida no osso, com quatro erros técnicos verificáveis e uma contradição de governança que precisa de ser resolvida antes de qualquer entrevista.

---

# 0. VEREDITO EM SEIS FRASES

1. A escolha dos dados é **excelente** e demonstra acesso a ativos que 95% dos candidatos não têm — em particular o CRF anotado do CALGB 40101, que é o artefacto que torna um pipeline raw→SDTM defensável e não apenas plausível.
2. O **eixo da arquitetura está errado**: os pilares estão organizados por *fonte de dados*, não por *afirmação científica*. Quatro pilares que fazem "carregar → mapear → descrever" repetem o mesmo músculo quatro vezes.
3. O **Pilar 3 viola a governança que a pergunta 1 tenta proteger**: o módulo WOMAC é VITAL, é um ensaio, e não existe em FHIR — colocá-lo no pipeline hospitalar é precisamente a mistura que a arquitetura diz evitar.
4. Estás a **desperdiçar o teu melhor ativo científico**: o CALGB 40101 é um ensaio de **não-inferioridade** (margem HR 1,30) com desenho fatorial 2×2 e três emendas de protocolo a meio. Descrito como "replicação de DFS com KM e Cox", perde tudo o que o torna único.
5. **Achilles não é uma ferramenta de validação de qualidade** — é caracterização, e o componente Achilles Heel foi removido do pacote. O DQD é a ferramenta de validação. O João Fonseca trabalha com DQD; este erro é detetado numa frase.
6. Nada disto é fatal. **Todas as correções são executáveis em duas a três semanas** e várias tornam a arquitetura *mais* impressionante, não menos.

---

# 1. O QUE ESTÁ A SER AVALIADO

## 1.1 Ativos de dados

| # | Dataset | N | Natureza | Estado de acesso |
|---|---|---|---|---|
| 1 | CALGB 40101 | 3.871 aleatorizados | Ensaio de fase III, 2×2, não-inferioridade | Microdados + CRF anotado + MedDRA |
| 2 | VITAL | 25.871 (1.398 no subestudo WOMAC) | Ensaio fatorial 2×2, prevenção | Microdados + PROMs longitudinais |
| 3 | MIMIC-IV Demo on FHIR 2.1.0 | 100 doentes | EHR hospitalar, HL7 FHIR nativo | Open access |
| 4 | MIMIC-IV Relacional Demo 2.2 | 100 doentes | EHR hospitalar, tabelas hosp/icu | Open access |
| 5 | eICU-CRD Demo 2.0.1 | ~2.500 estadias, 20 hospitais | UCI multicêntrica, APACHE | Open access (ODbL) |
| 6 | NHANES Ciclo L 2021–2023 | 11.933 | Inquérito populacional, PHQ-9, biomarcadores | Domínio público |

## 1.2 Pilares declarados

- **P1** `Clinical-data-to-CDISC` — raw/EDC → SDTM + ADaM, guiado pelo CRF anotado CALGB 40101
- **P2** `CDISC-to-OMOP` — harmonização + "Estudo de Concordância e Sobrevivência" (KM, Cox, Tabela 1 em SMD, segurança MedDRA, "alinhado com CohortMethod")
- **P3** `FHIR-to-OMOP` — MIMIC-IV FHIR → OMOP 5.4, qualidade com Achilles, módulo PROMs WOMAC em `SURVEY_CONDUCT`/`OBSERVATION`
- **P4** Estudo integrador — *Study Feasibility & Target Trial Emulation*, atrição CONSORT, transportabilidade via SMD

## 1.3 O que o parecer reconhece como genuinamente forte

Antes das críticas, e a sério:

- **O CRF anotado do CALGB 40101 é um ativo raro.** A maioria dos portefólios "raw→SDTM" inventa o raw. Ter o CRF anotado oficial significa que o mapeamento campo-a-campo é *auditável contra a especificação do promotor*. Isso é a diferença entre demonstrar e afirmar.
- **A separação em zonas de dados é a intuição correta.** Ensaio e hospital têm DUAs diferentes, vocabulários diferentes, granularidades diferentes e regimes de reidentificação diferentes. Misturá-los num único CDM sem fronteira é o erro clássico.
- **A cobertura de domínios é invulgar**: SDTM, ADaM, OMOP, FHIR, CDISC Controlled Terminology, MedDRA, LOINC, PROMs. Quase ninguém tem os cinco em simultâneo.
- **O instinto de cruzar fontes (P4) é o instinto certo** — é o que as redes federadas fazem. O problema é a execução, não a ambição.

---

# 2. QUESTÕES MAIORES

Sete questões que, no seu estado atual, impedem a aceitação. Ordenadas por gravidade.

## M1 — O Pilar 3 contradiz a governança que o Pilar 1 existe para proteger

**O problema.** O módulo de PROMs do Pilar 3 é descrito como "WOMAC 4 anos em `SURVEY_CONDUCT` e `OBSERVATION` (LOINC)", dentro do pipeline `FHIR-to-OMOP` que ingere MIMIC-IV. Mas:

- **O MIMIC-IV não contém PROMs.** Não há `SURVEY_CONDUCT`, não há WOMAC, não há questionários de qualidade de vida. As tabelas do MIMIC-IV são eventos clínicos, prescrições, laboratórios, microbiologia, imagiologia textual e *outpatient measurements* (`omr`) — nada disto é um PRO.
- **Os dados WOMAC são do VITAL** — um ensaio aleatorizado, populacional, com follow-up anual por questionário. Não são dados hospitalares.
- **Não existe representação FHIR do VITAL.** Se o módulo "FHIR→OMOP" os ingere, alguém teve de fabricar FHIR a partir de um ensaio — e nesse momento a afirmação "construí um pipeline FHIR→OMOP" deixa de ser verdadeira para esse módulo.

**Porque é grave.** A pergunta 1 pergunta se a separação em pilares respeita a governança, evitando misturar dados de ensaio com pipelines hospitalares. **A resposta honesta é: o desenho diz que sim, a execução diz que não.** Um revisor que encontre isto perde a confiança no resto da arquitetura — porque passa a suspeitar que os pilares são uma organização de conveniência e não uma fronteira real.

**Há ainda um segundo problema, independente e maior.** O WOMAC é um instrumento com **licença de utilizador final**. O artigo do subestudo VITAL declara explicitamente: *"A WOMAC end-user license was obtained for this study."* Usar WOMAC num produto, numa demo pública ou num portefólio comercial pode exigir licença própria. Isto não é um detalhe burocrático — é um risco jurídico que um Data Lead de uma empresa que vende dados vai identificar imediatamente.

**Correção.**
1. **Mover o módulo WOMAC para a Zona de Ensaio (P2 ou um P2-b).** É um ensaio, é CDISC, pertence ao lado do CALGB. O Pilar 3 fica com o que o MIMIC realmente tem.
2. **No Pilar 3, substituir o módulo PROMs por algo que o MIMIC suporta e que é igualmente VBHC**: trajectórias de *length of stay*, readmissão a 30 dias, mortalidade, utilização de UCI, e — se quiseres VBHC a sério — *cost proxies* via `DRGcodes`. Isso é valor em saúde medido em dados hospitalares reais.
3. **Resolver a licença do WOMAC antes de publicar.** Alternativas: usar um instrumento de domínio público (KOOS é licenciado; **PROMIS** é livre mas requer registo; **EQ-5D** é licenciado por região), ou manter o WOMAC e documentar a licença no README com uma nota de uso não-comercial.

---

## M2 — Achilles não é validação de qualidade. E o Achilles Heel foi removido.

**O problema.** O Pilar 3 afirma "validação de qualidade de dados com OHDSI Achilles". Isto é tecnicamente incorreto e desatualizado.

**A distinção correta**, tal como a própria OHDSI a formula:

| | **Achilles** | **Data Quality Dashboard (DQD)** |
|---|---|---|
| Função | **Caracterização** — descreve o que está na base | **Validação** — quantifica o que não conforma |
| Volume | ~250 análises descritivas | **>3.500 checks** |
| Framework | Achilles Heel (limitado) | Kahn: Conformance, Completeness, Plausibility |
| Saída | Dashboard de caracterização no Atlas | Pass/fail por campo, com thresholds configuráveis |
| Estado | **Achilles Heel foi removido** do pacote | Substituiu o Heel; exigido antes de estudos de rede |

O fórum da OHDSI é explícito: *"Achilles Heel has been removed. The Data Quality Dashboard is to replace Achilles Heel."* E a documentação do CDM diz que o DQD *"is required to be run on all databases prior to participating in an OHDSI network research study"*.

**Porque é grave para esta entrevista em particular.** O João Fonseca descreve o seu trabalho de qualidade como *"integrando testes de qualidade de dados em cada fase… avaliações por coluna, progredindo para avaliações ao nível da tabela, culminando em testes unitários de alto nível"*. Isso **é** o DQD, descrito em linguagem própria. Dizer "Achilles" onde ele espera "DQD" sinaliza que aprendeste os nomes das ferramentas mas não o que cada uma faz — exatamente o tipo de lacuna que um Data Lead testa deliberadamente.

**Correção.**
1. **Pilar 3: DQD como validação** (com thresholds, política de falhas e a tua `quality/dqd_policy.json` existente), **Achilles como caracterização** em secção separada. São dois deliverables, não um.
2. **Liga as falhas do DQD a consequências analíticas.** Isto já está no dossiê de estratégia (§P3.9) e é o que distingue "corri o DQD" de "sei o que o DQD significa". Exemplo: `measurement_unit_concept_id` nulo → medições não comparáveis → erro não-diferencial → atenuação do HR na direção do nulo.
3. **Nota fina que vale muito:** o DQD classifica os checks em *verification* (estrutura) e *validation* (conteúdo), e tem uma categoria de **convenção** onde alguma falha é inevitável (ex.: conceitos de origem sem standard correspondente). Saber que `max_failed_checks: 0` é uma política defensável para *verification* mas potencialmente irrealista para *convention* demonstra que leste o output e não só o README.

---

## M3 — O CALGB 40101 é um ensaio de não-inferioridade. Tratá-lo como superioridade destrói o ativo.

**Este é o erro científico mais importante do parecer.**

**O que o ensaio realmente é.** O CALGB 40101 (NCT00041119) é um desenho fatorial 2×2 que testa **duas** questões:

1. **Não-inferioridade** de paclitaxel single-agent (T) versus AC, com **margem de HR = 1,30** — definida como limite superior do IC 95% unilateral do HR(T:AC) **inferior a 1,30** para RFS. Poder de 89%, assumindo RFS a 5 anos de 88% (AC) e 84,7% (T).
2. **Superioridade** de 6 ciclos versus 4 ciclos.

**Os números reais:**

| Parâmetro | Valor |
|---|---|
| Aleatorizados | **3.871** — AC4: 1.142 · AC6: 789 · T4: 1.151 · T6: 789 |
| Por regime | AC 1.931 · T 1.940 |
| Endpoint primário | **RFS** (relapse-free survival) — *não* DFS |
| Eventos RFS | 437 |
| Mortes | 266 |
| Seguimento mediano | 6,1 anos (máx. 8,9) |
| **Resultado** | **HR 1,26 (IC 95% unilateral UCB 1,48)** → **não-inferioridade NÃO demonstrada** |
| OS | HR 1,27 (UCB 1,56) |
| RFS a 5 anos | 91% (AC) vs 88% (T) |
| OS a 5 anos | 95% vs 94% |
| Toxicidade | AC mais tóxico; **todas as mortes relacionadas com tratamento ocorreram no braço AC** |

**Porque "replicação de DFS com KM e Cox" é a descrição errada.** Se replicares com um Cox univariável e reportares um HR com p-value bicaudal, estás a responder a uma pergunta que o ensaio não fez. Perdes:

- **A margem.** O resultado do ensaio não é "HR 1,26, p=0,06". É "o UCB de 1,48 não desce abaixo de 1,30, logo não posso declarar não-inferioridade". A conclusão vive na comparação com a margem, não no p-value.
- **A estratificação.** O teste primário usa um modelo de Cox **estratificado pelos fatores de estratificação do estudo**. Um Cox não estratificado não replica o teste.
- **O teste unilateral.** Uma cauda, não duas.
- **O per-protocol.** Num ensaio de não-inferioridade, a análise per-protocol é *co-primária* na prática regulatória, porque a não-inferioridade é vulnerável a contaminação do braço experimental. Isto é a razão pela qual a FDA e o EMA exigem ambas.
- **O nome do endpoint.** RFS e DFS não são sinónimos. DFS inclui tipicamente segundos tumores primários e morte por qualquer causa; RFS é recorrência ou morte atribuível ao cancro da mama. Dizer "DFS" quando o ensaio publicou "RFS" é um deslize pequeno mas numa entrevista técnica conta.

**Porque isto é uma oportunidade, não uma crítica.** Um candidato que replica uma **não-inferioridade contra uma margem declarada, com UCB unilateral e dupla análise ITT/per-protocol**, demonstra competência regulatória que quase nenhum júnior tem. É muito mais difícil do que um Cox com p-value — e é muito mais impressionante.

**Correção.**
1. Renomear o estudo: **"Replicação de um teste de não-inferioridade"**, não "replicação de DFS".
2. Reportar o **UCB unilateral a 95%** e a **comparação explícita com a margem de 1,30**.
3. Correr **ITT e per-protocol**, e discutir a diferença.
4. Declarar o estimando completo (ver M4).
5. Verificar se o teu CRM/raw permite reconstruir os **estratos de randomização** (nós, tamanho do tumor, estado hormonal, menopausa). Sem eles, o Cox estratificado não é replicável — e isso é uma limitação que deves declarar, não esconder.

---

## M4 — "Estimand Lab" sem estimandos é inflação de nomenclatura

**O problema.** O que está descrito no Pilar 2 — KM + Cox + Tabela 1 + segurança MedDRA — é uma **análise de sobrevivência padrão**. Um estimando, segundo ICH E9(R1), tem **cinco atributos**:

1. População (quem)
2. Condições de tratamento (que regimes, incluindo intercorrências)
3. Variável (o endpoint, como medido)
4. **Tratamento de eventos intercorrentes** (o atributo esquecido)
5. Medida sumária ao nível da população (HR, diferença de risco, RMST…)

Se o teu documento não nomeia os cinco, não é um estimand lab — é uma análise. E o nome "Estimand Lab" cria uma expectativa que o conteúdo não cumpre, o que é pior do que não a criar.

**Porque o CALGB 40101 é o ensaio *ideal* para um verdadeiro estimand lab.** Tem uma densidade invulgar de eventos intercorrentes e decisões de desenho:

| Evento intercorrente / decisão | Detalhe |
|---|---|
| **Descontinuação por toxicidade** | AC significativamente mais tóxico; todas as mortes relacionadas com tratamento no braço AC |
| **Reduções de dose** | Frequentes em AC |
| **Cruzamento para trastuzumab** | Permitido a partir de 2005 em ambos os braços, em timing diferente |
| **Emenda 2003: q3w → q2w** | Após CALGB 9741 demonstrar superioridade da dose-densa. Muda a *versão de tratamento* |
| **Emenda 2005: elegibilidade** | Alargada de 0 nós positivos para 1–3 nós positivos. Muda a população a meio |
| **Fecho dos braços de 6 ciclos** | Fevereiro de 2008, por *slow accrual*. Alocação final desequilibrada (1.142+1.151 vs 789+789) |

Isto dá-te material para uma escada de sensibilidade que é *exatamente* o que o E9(R1) existe para resolver:

| Estratégia | Pergunta que responde | Implementação |
|---|---|---|
| **Treatment policy** | Qual o efeito de *atribuir* T vs AC, independentemente da adesão? | Todos os aleatorizados; ignora descontinuação |
| **Hypothetical** | Qual o efeito *se* todos tolerassem? | Censura na descontinuação, ou *copy reference* |
| **Composite** | Qual o efeito sobre "sobrevivência sem recurrência **e sem** descontinuação por toxicidade"? | Descontinuação por EA como evento |
| **While-on-treatment** | Qual o efeito durante o tratamento? | Censura na descontinuação + IPCW |

E, para a **não-inferioridade**, a questão torna-se ainda mais interessante: **a escolha da estratégia pode mudar a conclusão face à margem**. Se a estratégia *while-on-treatment* mostrar não-inferioridade e a *treatment policy* não, tens uma conclusão que depende de uma decisão pré-especificada. Isso é exatamente o argumento que os reguladores fazem — e demonstrá-lo com números é raro e poderoso.

**Correção.**
1. Escreve o estimando com os cinco atributos, em quatro linhas, antes de correr nada.
2. Implementa **pelo menos três** estratégias de evento intercorrente.
3. Reporta a conclusão face à margem **sob cada estratégia**.
4. Se as conclusões divergirem: esse é o resultado. Não é um problema — é a demonstração.

---

## M5 — SMD < 0,10 como critério num ensaio aleatorizado é conceptualmente errado

**O problema.** O Pilar 2 propõe "Tabela 1 em SMD |SMD| < 0,10". Num ensaio aleatorizado isto confunde duas coisas.

**O que está certo:** usar SMD em vez de p-values numa Tabela 1 é correto e é sinal de rigor — os p-values de balanceamento dependem de N e não informam nada sobre a magnitude do desequilíbrio.

**O que está errado:** tratar |SMD| < 0,10 como **critério de aceitação** num ensaio.

- Num ensaio aleatorizado, o balanceamento é garantido **em expectativa**. Num único ensaio, um |SMD| > 0,10 numa covariável é uma flutuação aleatória, não uma falha.
- Com ~40 covariáveis de baseline e n=3.871, é **esperado** que algumas excedam 0,10 por acaso.
- Mais importante: **não deves ajustar para desequilíbrios pós-randomização.** Ajustar para uma covariável que difere por acaso quebra a aleatorização e pode introduzir viés. Num RCT, a Tabela 1 é **descritiva**; não é um gate.
- O SMD como gate pertence ao **Pilar 4** e a qualquer comparação observacional, onde não há aleatorização e o desequilíbrio é sistemático.

**Nota de contexto que vale a pena ter na ponta da língua:** o artigo original do CALGB 40101 reporta precisamente que *"baseline clinicopathologic variables were well balanced between the AC and T arms"* — sem p-values. Isto é o padrão. Citar isso mostra que leste o paper.

**Correção.**
1. **Pilar 2:** Tabela 1 descritiva com SMD e *love plot*. Sem critério de aceitação. Uma nota: "em ensaio aleatorizado, o SMD documenta o balanceamento realizado; não é um critério de decisão."
2. **Pilar 4:** aí sim, |SMD| < 0,10 como critério de balanceamento **após** ponderação/matching, com love plot antes/depois.
3. **Sempre:** SMD e não p-value; e declara que o SMD é independente de N, logo comparável entre nós de uma rede federada — o que é *especialmente* relevante para a Promptly, onde um nó tem 50 mil doentes e outro 5 milhões.

---

## M6 — O Pilar 4, tal como está desenhado, vai devolver N ≈ 0

**O problema.** Aplicar os critérios de elegibilidade do CALGB 40101 a uma coorte hospitalar MIMIC-IV:

- **MIMIC-IV Demo = 100 doentes.** Não 100 mil. Cem.
- **MIMIC-IV é uma base de UCI** de um único centro (Beth Israel Deaconess). Os doentes do CALGB 40101 são doentes ambulatórios com cancro da mama operável em quimioterapia adjuvante.
- A interseção — mulher com cancro da mama operável, 0–3 gânglios positivos, a receber AC ou paclitaxel adjuvante, **internada numa UCI** — é aproximadamente vazia. Talvez 0. Talvez 1.

**Consequência.** A "avaliação de transportabilidade via SMD" vai comparar distribuições de covariáveis entre 3.871 doentes de ensaio e 0–1 doentes de UCI. Não há SMD que calcular, não há índice de transportabilidade, não há conclusão. O Pilar 4 não falha por má execução — falha por desenho.

**E há um segundo problema estrutural.** MIMIC-IV Demo (100 doentes) é o **menor** dataset do teu portefólio, e estás a usá-lo para o estudo mais exigente. Ao mesmo tempo tens o **eICU Demo com ~2.500 estadias de 20 hospitais** — 25× maior, genuinamente multicêntrico, com scores APACHE — e ele não está em pilar nenhum. Isso é uma alocação de recursos difícil de defender.

**Três reformulações viáveis — escolhe uma:**

**Opção A — Aceitar o N≈0 e fazer disso o resultado.** O deliverable passa a ser: "o protocolo é computável, a coorte-alvo é definida, e **demonstro que esta fonte não pode responder a esta pergunta**, com o número exato em cada passo de atrição". Isto é um sinal de senioridade genuíno: saber dizer que os dados não chegam é mais raro do que saber correr um modelo. **Requisito:** tem de estar declarado *a priori* como objectivo, senão parece desculpa.

**Opção B — Mudar o alvo para o eICU.** Reformula a pergunta para algo que o eICU *pode* responder (ex.: um trial de estratégia de sedação, ou de transfusão, ou de ventilação precoce), e faz a TTE dentro do eICU. Ganhas duas coisas: N real (~2.500 estadias) e **20 hospitais**, o que te permite estimar **heterogeneidade entre centros** — que é o argumento federado que a Promptly compra. Perdes: a ligação ao CALGB.

**Opção C — Mudar o par trial→alvo para VITAL → NHANES.** Esta é a mais elegante e a que eu recomendaria como estudo *adicional*. O VITAL é uma população de adultos dos EUA sem cancro/CVD prévio; o NHANES é uma amostra representativa de adultos dos EUA. É o par natural para um estudo de **generalização/transportabilidade** a sério. Ver M7 para o método.

**Correção mínima, independentemente da opção:** declarar explicitamente na arquitetura que o Pilar 4 usa o MIMIC-IV Demo de 100 doentes e que a inferência é **de viabilidade, não de efeito**.

---

## M7 — "Transportabilidade via SMD" não é transportabilidade

**O problema.** O SMD entre a população do ensaio e a população-alvo diz-te **que as populações diferem**. Não te diz **se o efeito do tratamento se transporta**. São afirmações diferentes, e confundi-las é um erro conceptual que um epidemiologista deteta imediatamente.

**O que a transportabilidade exige**, na literatura (Degtiar & Rose; Dahabreh; Tipton):

| Passo | O que fazer |
|---|---|
| 1. Definir a população-alvo | Um *frame* explícito, não "os doentes reais" |
| 2. Modelar a participação no ensaio | P(S=1 \| X) — regressão logística ou *random forest* sobre covariáveis medidas em ambos |
| 3. **Índice de generalização de Tipton** | Métrica limitada em [0,1], sem pressupostos distribucionais |
| 4. **Ponderação pelas odds inversas de participação** | Reponderar a amostra do ensaio para a distribuição de modificadores de efeito do alvo |
| 5. **Avaliar modificação de efeito** | Não basta o balanceamento — é preciso mostrar que o efeito **varia** com o que difere |
| 6. **Pressuposto de cobertura (positividade)** | O intervalo de cada modificador de efeito no alvo tem de estar **dentro** do intervalo do ensaio; senão precisas de extrapolação explícita ou *trimming* |

**As bandas interpretáveis do índice de Tipton** — úteis porque são citáveis e concretas:

| Índice | Categoria | Interpretação |
|---|---|---|
| [0,9 – 1,0] | Muito alto | Generalizável; o ensaio é quase uma amostra aleatória do alvo |
| [0,8 – 0,9) | Alto | Generalizável; viés pequeno e ligeiro aumento de SE por reponderação |
| [0,5 – 0,8) | Médio | Generalização possível, com viés e SE inflacionados |
| [0 – 0,5) | Baixo | Não generalizável |

**O detalhe que quase todos falham, e que tu podes usar como diferencial:** podes calcular o **índice de Tipton** e a **reponderação**, mas só podes transportar o **efeito** se os modificadores de efeito relevantes estiverem medidos em **ambas** as fontes. Se o NHANES não mede o desfecho (incidência de cancro a 5 anos), podes calcular a *semelhança das populações* e o *TATE para um desfecho co-medido*, mas não o transporte do efeito oncológico. **Declarar esta fronteira é o que distingue um estudo de transportabilidade de uma regressão com pesos.**

**Correção.**
1. Substituir "transportabilidade via SMD" por "**generalização/transportabilidade com índice de Tipton + ponderação por odds inversas + modificação de efeito**".
2. Reportar sempre: índice de Tipton com banda, % da amostra do ensaio efetivamente utilizada após *trimming*, e a lista de modificadores de efeito com a sua distribuição nas duas fontes.
3. Se o SMD aparecer, aparece **como diagnóstico descritivo de sobreposição**, não como conclusão de transportabilidade.

---

## M8 — Licenças e DUAs: não podes publicar microdados derivados

**O problema.** A arquitetura prevê publicar os resultados, e o portefólio é público. Mas:

| Fonte | Regime | O que **não** podes fazer |
|---|---|---|
| **CALGB 40101** | DUA do detentor (Alliance / Project Data Sphere) | Redistribuir linhas, incluindo conversões OMOP ou extractos `.duckdb` |
| **VITAL** | Pedido ao Data Coordinating Center (BWH) + DUA | Redistribuir; e **WOMAC exige licença de utilizador final** |
| **MIMIC-IV** (demo e completo) | PhysioNet — demo é open access sob licença; completo exige credenciação | Redistribuir; tentar reidentificar |
| **eICU demo** | **ODbL v1.0** | Redistribuir sem manter a mesma licença e atribuição |
| **NHANES** | Domínio público (governo dos EUA) | Nada de relevante |

**A consequência prática, e é dura:** se publicares no GitHub um `calgb40101.duckdb` convertido para OMOP, estás provavelmente em violação do DUA — e isso, numa empresa cujo produto é a governação de dados de saúde, é desqualificante. A Promptly é uma empresa em que *"o Data Partner é sempre o responsável pelo tratamento"* e onde a governação é o produto. Um candidato que publica microdados licenciados não é contratável, independentemente da qualidade técnica.

**Correção.**
1. **Publica código, especificações e resultados agregados. Nunca linhas.**
2. Estrutura de repositório: `data/` no `.gitignore`; `results/` com agregados; `protocol/` e `specs/` versionados.
3. **Uma matriz de licenças no README** de cada repositório — fonte, licença, o que é publicável, o que não é. Isto é, em si, um artefacto de governança que impressiona.
4. `.gitignore` e um `DATA_PROVENANCE.md` com a cadeia completa.

---

# 3. QUESTÕES MENORES

## m1 — Dois repositórios que produzem OMOP duplicam tudo
`CDISC-to-OMOP` e `FHIR-to-OMOP` vão ambos carregar vocabulários, correr DQD e manter uma configuração de CDM. Vão divergir. **Correção:** extrair uma biblioteca `omop-core` (DDL, carga de vocabulário, `vocabulary_signature`, política DQD, template de estudo) e deixar em cada repositório apenas o *adaptador de ingestão*. É uma correção de arquitetura, não de estilo — e mostra que pensas em plataforma.

## m2 — Nomenclatura inconsistente
`Clinical-data-to-CDISC`, `Cdisc-to-Omop`, `FHIR-to-OMOP`. Consistência: `CDISC-to-OMOP`, `FHIR-to-OMOP`. Detalhe trivial; num portefólio é sinal de descuido.

## m3 — VITAL: 25.871 participantes, e os 1.398 são uma subcoorte selecionada
O subestudo WOMAC não é uma amostra aleatória do VITAL: são os participantes com **dor crónica frequente do joelho no baseline** que devolveram pelo menos um questionário. Mais: **218 elegíveis não devolveram nenhum** — e esses tinham **IMC mais alto (34 vs 32)** e eram **mais frequentemente mulheres (74% vs 66%)**. Há seleção. Isso não invalida o estudo — mas tem de ser declarado, e é exatamente o tipo de raciocínio que o teu Pilar 3 deveria demonstrar.

## m4 — VITAL: seguimento de 5,3 anos, não 4
Média de 5,3 anos (DP 0,7), 6 questionários anuais, **média de 3,8 devolvidos**. O "4 anos" está errado e a adesão incompleta (3,8/6) é material para um modelo misto com omissão — não um detalhe.

## m5 — "Delta-Score e taxa de resposta MCID" precisa de pré-especificação
Duas armadilhas:
- **Dicotomizar um score contínuo** perde potência e cria um resultado que depende do limiar escolhido. Se escolheres o limiar depois de ver os dados, é *p-hacking* com outro nome.
- **O MCID do WOMAC não é um número único.** Varia entre abordagens ancoradas e distributivas, e entre populações. Tens de pré-especificar qual usas e porquê.
- E o mais importante: **o resultado do VITAL foi nulo**. Não houve efeito na dor, função ou rigidez. Uma análise de respondedores MCID sobre um efeito nulo, sem hipótese pré-especificada, vai produzir ruído interpretado como sinal. **Correção:** pré-especificar o MCID e a hipótese, e reportar o resultado nulo como resultado. Um estudo que confirma um nulo com boa metodologia é um estudo bem-sucedido.

## m6 — NHANES: os pesos amostrais não são opcionais
O NHANES tem desenho amostral complexo: estratos, *primary sampling units* e pesos. **Ignorar os pesos produz estimativas pontuais enviesadas e, pior, erros-padrão e intervalos de confiança errados.** Tens de usar `survey`/`srvyr` em R ou `svy` equivalente, com o desenho completo. E o Ciclo L tem uma estrutura de pesos própria, por vezes com ficheiros combinados.
Além disso: **o NHANES não está em pilar nenhum.** Está na lista de dados e não na arquitetura. Ou o integras (é o teu *frame* populacional — a peça que torna a transportabilidade possível), ou o removes.

## m7 — eICU está órfão, e é o teu melhor ativo para a história federada
~2.500 estadias, **20 hospitais**, scores APACHE. É o único dataset do portefólio com **variação sistemática entre centros** — que é exatamente o que uma rede federada enfrenta. E é 25× maior do que o MIMIC Demo.
**Correção:** dar-lhe pilar (ou substituir o MIMIC Demo no Pilar 4, ver M6 Opção B) e usá-lo para **heterogeneidade entre centros** — o argumento que a Promptly vende.

## m8 — APACHE tem cobertura parcial
Na literatura sobre o eICU completo, a exigência de APACHE IVa disponível reduz a coorte de 200.859 para ~50.106 estadias — cerca de 25%. **Não assumas que o APACHE está disponível para todos os doentes do demo.** Quantifica a cobertura antes de o usares como variável de ajustamento, e se faltar muito, não o uses como se fosse completo.

## m9 — MedDRA é o problema de mapeamento mais difícil do teu stack, e não está mencionado
Isto é uma omissão séria, porque é o teu ponto de contacto direto com o trabalho do João Fonseca.

**O que é verdade sobre MedDRA em OMOP:**
- Os conceitos MedDRA são, em grande parte, **conceitos de classificação** e **não têm "Maps to" completo** para conceitos standard.
- A OHDSI adicionou aproximadamente **13.672 relações "Maps to"/"Maps to value"** para os códigos MedDRA mais frequentes em RWD — cobertura **parcial**, não total.
- Um trabalho publicado de SDTM→OMOP teve de mapear manualmente **5.856 códigos MedDRA**, gerando **6.613 registos de `source_to_concept_map`**.
- **Regra crítica:** as hierarquias MedDRA e SNOMED **não devem ser fundidas**. *"Because of the MedDRA/SNOMED hierarchy discrepancies, we don't merge the hierarchies in any way."*

**O que tens de fazer, e dizer em voz alta:**
1. `condition_source_concept_id` = conceito **MedDRA** (não-standard), `condition_source_value` = o termo MedDRA original.
2. `condition_concept_id` = conceito **SNOMED standard** mapeado.
3. Onde não há mapeamento: `condition_concept_id = 0`, **nunca `NULL`** — a distinção entre "desconhecido" e "não registado" é uma convenção que o teu repo já respeita.
4. **Relatar a cobertura do mapeamento**: % de termos MedDRA com standard, % só com source, % sem nada. E a cobertura **ponderada por frequência de eventos**, que é a que importa — 95% dos termos distintos podem cobrir 99% dos eventos, ou o contrário.
5. **Nunca atravessar MedDRA com `CONCEPT_ANCESTOR`** como se fosse a hierarquia clínica. Se precisas de agregar por SOC, agrega no vocabulário MedDRA de origem, não em OMOP.
6. Revisão clínica dos mapeamentos não-triviais, com registo de decisões — que é exatamente o padrão *proposta → revisão humana → registo* que a Promptly descreve no seu poster de 2024.

**Porque isto importa tanto:** o João Fonseca passou de USAGI para uma ferramenta de mapeamento assistida por IA, com revisão por especialistas clínicos. Um candidato que chega com um problema MedDRA real, uma taxa de cobertura medida, e um processo de revisão documentado, está a falar a língua dele.

## m10 — CohortMethod num ensaio aleatorizado é um erro de categoria
"Alinhado com o CohortMethod do OHDSI HADES" no Pilar 2: o `CohortMethod` faz **propensity scores de larga escala** para ajustar confundimento em estudos observacionais. Num ensaio aleatorizado **não há confundimento para ajustar** — a aleatorização já o resolve. Aplicar PS a um RCT não é neutro: pode introduzir viés e reduzir precisão.
**Correção:** invocar o HADES onde ele é apropriado — no **Pilar 4** (coorte emulada, onde o confundimento é real) e, se quiseres um estudo observacional comparativo, no `eICU`/`MIMIC` com `CohortMethod` a sério. No Pilar 2, o que é apropriado é **Cox estratificado + análise de estimandos + não-inferioridade**. Mencionar HADES de forma decorativa é pior do que não o mencionar.

## m11 — A estrutura fatorial 2×2 tem de ser respeitada
O CALGB 40101 tem **quatro** células, não duas. Consequências:
- As duas questões (T vs AC; 6 vs 4 ciclos) combinam braços para a segunda análise.
- O fecho dos braços de 6 ciclos em 2008 produziu **alocação desequilibrada** (2.293 no total de 4 ciclos vs 1.578 no de 6 ciclos).
- A comparação 4 vs 6 está **subalimentada** por causa desse fecho. Reportar um p-value sem reconhecer isto é enganador.
- Há uma interação a testar (não significativa no paper, mas tem de ser reportada).

## m12 — Reprodutibilidade e assinatura de vocabulário
O teu repo já tem uma `vocabulary_signature()` que calcula um sha256 sobre versão de schema, vocabulários, domínio, contagem e um `BIT_XOR(HASH(...))`. **Usa-a em todos os relatórios.** Quase nenhum estudo OMOP liga os resultados a uma assinatura de vocabulário; sem isso, um resultado não é reproduzível daqui a dois anos, quando o Athena publicar outra versão.

---

# 4. RESPOSTAS DIRETAS ÀS QUATRO PERGUNTAS

## Pergunta 1 — A separação em 4 pilares respeita a melhor governança?

**Resposta curta: o princípio é correto, o eixo é discutível, e a execução contradiz o princípio.**

**O que está certo.** Separar zonas de dados é a intuição correta. Ensaio e hospital têm DUAs diferentes, vocabulários diferentes, granularidades diferentes e regimes de reidentificação diferentes. Misturar num único CDM sem fronteira é o erro clássico.

**O que está errado — três observações.**

**(a) O eixo não é governança; é proveniência.** Quatro repositórios não criam governança. Governança cria-se com: isolamento de zona de dados, conformidade com DUA, controlo de acesso, verificação de desidentificação, cadeia de proveniência, e — sobretudo — **uma regra explícita sobre o que pode atravessar a fronteira**. O repositório é um contentor; a regra é a governança.

**(b) A regra que falta, e que deves articular em voz alta na entrevista.** A distinção que importa não é "ensaio vs hospital". É:

> **Empréstimo ao nível da especificação — legítimo. Junção ao nível da linha — proibida sem base legal.**

O Pilar 4 pode legitimamente usar os **critérios de elegibilidade** do CALGB 40101 e comparar **distribuições agregadas** com o MIMIC. O que não pode, nunca, é ligar um doente do CALGB a um doente do MIMIC. Se articulares esta regra, respondes à pergunta 1 a um nível que o entrevistador não espera — porque é exatamente a regra que uma rede federada aplica.

**(c) A execução contradiz o princípio.** O módulo WOMAC (ver M1) mete um ensaio no pipeline hospitalar. É a violação que a arquitetura diz prevenir.

**Veredicto:** a separação é uma boa *primeira aproximação*, mas a fronteira correta é **Zona de Ensaio / Zona Hospitalar / Zona Populacional**, com uma camada de método partilhada. Ver §6.

---

## Pergunta 2 — O CALGB 40101 é a melhor escolha para demonstrar rigor regulamentar e bioestatístico?

**Resposta curta: é a melhor escolha possível — mas não pelos motivos que indicaste, e o enquadramento atual desperdiça-a.**

**Porque é excelente:**

| Propriedade | Porque importa |
|---|---|
| **Não-inferioridade com margem declarada (HR 1,30)** | Obriga-te a trabalhar com limites unilaterais e a comparar com uma margem. É o que os reguladores fazem, e quase nenhum portefólio o demonstra |
| **Desenho fatorial 2×2** | Duas questões, quatro células, uma interação a testar. Muito mais rico do que um braço vs outro |
| **Três emendas de protocolo** | q3w→q2w (2003), elegibilidade (2005), trastuzumabe (2005), fecho de braços (2008). Material para análise de *versão de tratamento* |
| **Resultado negativo** | A não-inferioridade não foi demonstrada. Um resultado nulo é **muito** melhor para demonstrar rigor do que um positivo, porque não podes esconder-te atrás da significância |
| **CRF anotado disponível** | O mapeamento raw→SDTM é auditável contra a especificação do promotor |
| **MedDRA + 437 eventos RFS + 266 mortes** | Escala suficiente para Cox com ~40 covariáveis pela regra dos 10 EPV |
| **Toxicidade diferencial** | AC mais tóxico, todas as mortes relacionadas com tratamento no AC → eventos intercorrentes informativos, não aleatórios |

**Porque o enquadramento atual a desperdiça:** "replicação de DFS com KM e Cox" transforma um ensaio de não-inferioridade com desenho fatorial e emendas numa regressão de sobrevivência banal. É a diferença entre mostrar que sabes correr um modelo e mostrar que sabes ler um protocolo.

**O que eu faria:** o estudo passa a ter **quatro** componentes, não um:
1. **Replicação da não-inferioridade** — Cox estratificado, UCB unilateral, comparação com 1,30, ITT + per-protocol.
2. **Estimand lab** — quatro estratégias de evento intercorrente (tratamento, hipotético, compósito, while-on-treatment) × conclusão face à margem.
3. **Segurança MedDRA** — incidência por SOC/PT com denominador de exposição, e a **cobertura do mapeamento MedDRA→SNOMED** medida e reportada.
4. **Sensibilidade à emenda** — o efeito muda se analisares só o período pós-2003 (q2w)? E se incluíres/excluíres os 1–3 gânglios positivos?

Isto é um estudo de nível de submissão. O atual é um exercício de aula.

---

## Pergunta 3 — O enquadramento das ferramentas OHDSI está correto e valoriza a candidatura perante o João Fonseca?

**Resposta curta: metade está. Uma está errada, a outra está bem invocada e mal aplicada.**

**Achilles no Pilar 3 — errado e desatualizado (M2).** Achilles é caracterização (~250 análises); o DQD é validação (>3.500 checks, framework de Kahn); o Achilles Heel foi **removido** e substituído pelo DQD. Chamar "validação de qualidade" ao Achilles é o tipo de imprecisão que um Data Lead cujo trabalho é DQD deteta na primeira frase. **Custo:** alto, porque é uma pergunta de verificação fácil. **Correção:** trivial (uma tarde).

**HADES/CohortMethod no Pilar 2 — bem invocado, mal aplicado (M10).** O `CohortMethod` faz propensity scores de larga escala para confundimento observacional. Num RCT não há confundimento. Usá-lo ali não é só inútil — pode introduzir viés. **Correção:** mover o HADES para o Pilar 4 e para um estudo observacional a sério; no Pilar 2 usar Cox estratificado e estimandos.

**O que *realmente* impressionaria o João Fonseca** — e que já tens ou podes ter em duas semanas:

1. **Mapeamento MedDRA → SNOMED com cobertura medida e revisão documentada.** É literalmente o problema que ele resolve todos os dias com USAGI e com a ferramenta assistida por IA. Um candidato que chega com "mapeei 100% dos termos MedDRA do CALGB, 87% obtiveram standard, e aqui está o registo de decisões dos 13% ambíguos revistos clinicamente" está a falar da dor dele, não de uma abstração.

2. **DQD com política e com consequências.** O teu repo já tem `quality/dqd_policy.json` com `max_errors: 0` e `max_failed_checks: 0`. Isso é forte. O passo seguinte é ligar cada falha a um viés — que é o que ele descreve como "testes unitários de alto nível".

3. **O teu reranker LOINC com fail-closed e abstenção.** Já tens. É o teu melhor argumento técnico e não aparece em nenhum dos quatro pilares. **Isto é um erro de arquitetura de portefólio**: o teu trabalho mais diferenciador está fora da arquitetura que o vai apresentar.

4. **Escala.** A rede dele tem 70 milhões de vidas. O teu maior dataset hospitalar tem 100 doentes. Os 25.871 do VITAL e os 3.871 do CALGB ajudam, mas a narrativa hospitalar é de demo. **Correção:** ou usas o eICU (2.500 estadias, 20 hospitais), ou assumes explicitamente que o demo é para validar o método e o completo virá com credenciação.

**Veredicto:** o enquadramento OHDSI está a **60%**. Corrigir o Achilles e o CohortMethod é rápido; acrescentar MedDRA e DQD-com-consequências é o que muda a conversa.

---

## Pergunta 4 — Recomendações e armadilhas metodológicas

### As dez armadilhas, por ordem de gravidade

| # | Armadilha | Consequência | Correção |
|---|---|---|---|
| 1 | **WOMAC (ensaio) no pipeline FHIR (hospital)** | Contradiz a governança; perde credibilidade em bloco | Mover para a Zona de Ensaio (M1) |
| 2 | **Achilles como validação de qualidade** | Erro técnico verificável em 1 frase | DQD valida, Achilles caracteriza (M2) |
| 3 | **Não-inferioridade tratada como superioridade** | Desperdiça o melhor ativo; erro regulatório | UCB unilateral vs margem 1,30 (M3) |
| 4 | **"Estimand Lab" sem os 5 atributos** | Inflação de nome; expectativa não cumprida | Implementar E9(R1) a sério (M4) |
| 5 | **SMD < 0,10 como gate num RCT** | Erro conceptual; implica ajustar pós-randomização | Descritivo no RCT; gate só no observacional (M5) |
| 6 | **Pilar 4 devolve N≈0** | Estudo vazio | Reformular (A/B/C) (M6) |
| 7 | **Transportabilidade = SMD** | Erro conceptual detetável | Tipton + odds inversas + modificação de efeito (M7) |
| 8 | **Publicar microdados licenciados** | Violação de DUA; desqualificante numa empresa de governação | Código e agregados apenas (M8) |
| 9 | **MedDRA sem plano de mapeamento** | Perde o ponto de contacto com o João | `_source_concept_id` + cobertura medida + revisão (m9) |
| 10 | **NHANES sem pesos amostrais** | Erros-padrão errados; conclusões inválidas | Desenho survey completo (m6) |

### As cinco recomendações estruturais

**R1 — Muda o eixo de "fontes" para "afirmações".** Ver §6. A arquitetura atual descreve o que carregas; deve descrever o que provas.

**R2 — Uma camada de método partilhada.** `omop-core` com DDL, vocabulários, `vocabulary_signature`, política DQD, template de estudo, concept sets versionados. Dois repositórios que produzem OMOP sem isto vão divergir em três meses.

**R3 — Cada estudo tem de ter um protocolo escrito antes do código.** É o artefacto mais impressionante de todos e o mais barato. Um protocolo de 2–3 páginas, datado, com hipótese, estimando, análise primária, sensibilidade, e uma secção **"o que me faria abandonar esta análise"**.

**R4 — Mete o teu melhor trabalho dentro da arquitetura.** O reranker LOINC e o fail-closed são o teu diferencial técnico e não estão em pilar nenhum. Ou entram (como um estudo de mapeamento no Pilar 2/3), ou a arquitetura está a esconder o que tens de melhor.

**R5 — Declara a escala, sempre.** Cada estudo deve abrir com uma linha do tipo: "N=100, demo, inferência de viabilidade" ou "N=3.871, ensaio completo, inferência de efeito". A honestidade sobre o que cada base pode responder é o sinal mais forte de senioridade que podes dar — e é o que os dados de demo tornam inevitável.

---

# 5. O QUE JÁ É FORTE E NÃO DEVE MUDAR

Para equilibrar, e porque é verdade:

- **A escolha do CALGB 40101 é excelente.** Não a troques. Só a reenquadra.
- **O CRF anotado** é um ativo que a maioria não tem.
- **A disciplina de repositório por domínio** é correta em princípio.
- **O instinto de cruzar ensaio e mundo real (P4)** é exatamente o que uma rede federada faz. O problema é a execução, não a ambição.
- **A combinação SDTM + ADaM + OMOP + FHIR + MedDRA + LOINC** é invulgarmente completa.
- **Ter o DQD com política de falhas** já te põe acima da média dos candidatos.

---

# 6. ARQUITETURA REVISTA — PROPOSTA

## 6.1 Três zonas de dados, uma camada de método, N estudos

```
┌─────────────────────────────────────────────────────────────┐
│  ZONA A — ENSAIO                     DUA estrito, sem redistribuição
│  CALGB 40101 (raw + CRF anotado + MedDRA) → SDTM → ADaM → OMOP
│  VITAL (25.871 + WOMAC 1.398)            → SDTM → ADaM → OMOP
├─────────────────────────────────────────────────────────────┤
│  ZONA B — HOSPITAL                   PhysioNet / ODbL
│  MIMIC-IV FHIR + Relacional (100) → OMOP
│  eICU Demo (~2.500 estadias, 20 hospitais) → OMOP
├─────────────────────────────────────────────────────────────┤
│  ZONA C — POPULAÇÃO                  Domínio público
│  NHANES L (11.393) → OMOP   ← o frame que torna a transportabilidade possível
└─────────────────────────────────────────────────────────────┘
              ▲
              │  fronteira: empréstimo de ESPECIFICAÇÃO sim
              │              junção de LINHA não
              ▼
┌─────────────────────────────────────────────────────────────┐
│  CAMADA DE MÉTODO PARTILHADA (omop-core)
│  DDL · vocabulários · vocabulary_signature · política DQD
│  concept sets versionados · template de estudo · testes
└─────────────────────────────────────────────────────────────┘
              ▲
              ▼
┌─────────────────────────────────────────────────────────────┐
│  ESTUDOS — cada um com UMA afirmação, um protocolo, um pacote
└─────────────────────────────────────────────────────────────┘
```

## 6.2 Os seis estudos, com a afirmação que cada um prova

| ID | Estudo | Zona | Afirmação que prova | Músculo |
|---|---|---|---|---|
| **S1** | Replicação da não-inferioridade | A | "Sei ler um protocolo e replicar o teste que ele define, incluindo a margem" | Regulatório |
| **S2** | Estimand lab (4 estratégias) | A | "Sei que o resultado depende da definição do estimando, e sei demonstrá-lo" | Estatística |
| **S3** | Segurança MedDRA + cobertura de mapeamento | A | "Sei medir o que não mapeia, e sei o que isso faz ao resultado" | Vocabulário |
| **S4** | Validação diferencial da conversão FHIR | B | "Sei medir se o meu ETL está correto, não só se corre" | Engenharia |
| **S5** | Heterogeneidade entre 20 hospitais | B | "Sei estimar o que varia entre nós de uma rede — e é isso que uma federação compra" | Federado |
| **S6** | Generalização VITAL → NHANES | A→C | "Sei quando um resultado transporta, e quando não" | Epidemiologia |

E o **WOMAC / VBHC** passa a ser **S3-b na Zona A** — é um ensaio, mede-se com modelos mistos, e o resultado é nulo, o que é perfeitamente respeitável se o pré-especificares.

## 6.3 Como isto se mapeia nos 4 pilares para a entrevista

Podes **manter a apresentação em 4 pilares** — é memorável e o entrevistador retém melhor quatro caixas do que seis estudos. Mas internamente:

- **Pilar 1** (raw→CDISC) = o substrato de S1, S2, S3
- **Pilar 2** (CDISC→OMOP) = **S1 + S2 + S3** ← aqui está o peso científico
- **Pilar 3** (FHIR→OMOP) = **S4 + S5** ← aqui está o peso de engenharia e de rede
- **Pilar 4** (integrador) = **S6** ← aqui está o peso estratégico

A diferença é que agora cada pilar tem **afirmações** e não **tarefas**.

---

# 7. PLANO DE CORREÇÃO PRIORIZADO

## Semana 1 — as correções que impedem danos

| # | Ação | Esforço | Porque é primeiro |
|---|---|---|---|
| 1 | **Mover WOMAC para a Zona A** e preencher a lacuna do Pilar 3 com outcomes hospitalares reais | 1 dia | Resolve a contradição de governança |
| 2 | **Substituir "Achilles" por "DQD (validação) + Achilles (caracterização)"** | 2 horas | Erro técnico mais fácil de corrigir e mais provável de ser detetado |
| 3 | **Reenquadrar o Pilar 2 como não-inferioridade** (UCB unilateral, margem 1,30, ITT + PP) | 1 dia | Recupera o melhor ativo |
| 4 | **Escrever o estimando com os 5 atributos** do E9(R1) | 3 horas | Barato; muda o nível da conversa |
| 5 | **Matriz de licenças + `.gitignore` + política de publicação** | 3 horas | Risco jurídico; e é um artefacto de governança em si |

## Semana 2–3 — as correções que elevam

| # | Ação | Esforço |
|---|---|---|
| 6 | Implementar 3–4 estratégias de evento intercorrente e reportar a conclusão face à margem sob cada uma | 3 dias |
| 7 | Mapeamento MedDRA→SNOMED com **taxa de cobertura mitigada por frequência** e registo de revisão | 3 dias |
| 8 | Ligar cada falha DQD a uma consequência analítica (tabela falha→viés) | 1 dia |
| 9 | Reformular o Pilar 4 (Opção A, B ou C) com o protocolo escrito antes do código | 2 dias |
| 10 | Extrair `omop-core` como biblioteca partilhada | 2 dias |

## Semana 4 — consolidação

| # | Ação |
|---|---|
| 11 | Tabela 1 descritiva com SMD (sem gate) no RCT; love plot com gate no observacional |
| 12 | Plano de survey para o NHANES, ou remoção do portefólio |
| 13 | eICU integrado com análise de heterogeneidade entre centros |
| 14 | Reescrever os READMEs: cada um abre com a **afirmação**, não com a stack |

---

# 8. O QUE LEVAR PARA A ENTREVISTA

## 8.1 As cinco frases que a arquitetura revista te permite dizer

1. *"O CALGB 40101 é um ensaio de não-inferioridade com margem de HR 1,30. Reproduzi o teste: HR 1,26 com limite superior unilateral de 1,48. A não-inferioridade não é demonstrável — e o ponto interessante é que essa conclusão **muda** conforme a estratégia de evento intercorrente que adoto."*

2. *"Mapeei 100% dos termos MedDRA para SNOMED; 87% obtiveram conceito standard. Os 13% restantes ficaram com `concept_id = 0` e source concept preservado — e **não** atravessei as hierarquias, porque MedDRA e SNOMED não são compatíveis nesse eixo."*

3. *"O DQD corre com política de falhas zero, mas a pergunta que interessa não é se passa — é o que cada falha faz ao estimando. Esta tabela liga as duas coisas."*

4. *"O MIMIC Demo tem 100 doentes. Tudo o que digo a partir dele é inferência de viabilidade, não de efeito. Para efeito tenho o CALGB com 3.871 e 437 eventos."*

5. *"A transportabilidade não se mede com SMD. Mede-se com um índice de generalização, ponderação por odds inversas de participação, e modificação de efeito — e só é identificável se os modificadores estiverem medidos nas duas fontes."*

## 8.2 A pergunta que vais receber e para a qual precisas de resposta pronta

> *"Porque é que tens quatro bases de dados em vez de uma bem feita?"*

Resposta preparada: *"Porque cada uma responde a uma pergunta que as outras não conseguem. O CALGB dá-me um estimando regulatório com verdade conhecida. O MIMIC dá-me dados hospitalares reais para testar o ETL. O eICU dá-me 20 hospitais, que é a única forma de estimar heterogeneidade entre centros sem uma rede federada verdadeira. E o NHANES dá-me o frame populacional sem o qual a transportabilidade é uma afirmação sem referente. O que **não** faço é juntá-las ao nível da linha — só ao nível da especificação."*

## 8.3 A armadilha final

Se te perguntarem qual é a maior fraqueza da arquitetura, **não digas "os dados são pequenos"**. Diz:

> *"A maior fraqueza é que três dos quatro pilares demonstram engenharia, e só um demonstra julgamento científico. Estou a corrigir isso movendo o peso para os estudos de estimando e de transportabilidade, onde o que se prova não é que o pipeline corre — é que sei o que o resultado significa e o que ele não significa."*

Essa resposta demonstra exatamente a competência que o cargo exige, e transforma a fraqueza em evidência de autoconhecimento.

---

# APÊNDICE — VERIFICAÇÕES FACTUAIS

Tudo o que este parecer afirma sobre os datasets foi verificado. Registo para que possas defender cada ponto.

| Afirmação | Fonte |
|---|---|
| CALGB 40101: 3.871 aleatorizados; AC4 1.142 / AC6 789 / T4 1.151 / T6 789; AC 1.931 vs T 1.940 | Shulman et al., *J Clin Oncol* 2014;31:2311 |
| Não-inferioridade, margem HR 1,30, poder 89%, RFS 88% vs 84,7% a 5 anos | Idem; editorial *JCO* 2014 (doi 10.1200/JCO.2014.55.9344) |
| HR RFS 1,26 (UCB unilateral 1,48); OS 1,27 (1,56); 437 eventos RFS; 266 mortes; seguimento mediano 6,1 anos | Idem |
| Emenda q2w após CALGB 9741; elegibilidade alargada a 1–3 gânglios em 2005; braços de 6 ciclos fechados em fev/2008 | Idem + ASCO Post, set/2012 |
| Todas as mortes relacionadas com tratamento no braço AC | Shulman et al. 2014 |
| VITAL: 25.871, 2×2, vitamina D 2000 UI + ómega-3 1 g | VITAL (NCT01169259); prevmed.bwh.harvard.edu |
| Subestudo WOMAC: n=1.398 com dor crónica do joelho; seguimento médio 5,3 anos (DP 0,7); 3,8 de 6 questionários devolvidos; resultado nulo | MacFarlane et al., *Arthritis Rheumatol* 2020; PMC7874905 |
| **WOMAC exige licença de utilizador final** | Idem: *"A WOMAC end-user license was obtained for this study"* |
| 218 elegíveis não devolveram questionário; IMC 34 vs 32; 74% vs 66% feminino | Idem |
| eICU Demo: >2.500 estadias, 20 hospitais; ODbL v1.0 | PhysioNet eicu-crd-demo 2.0.1 |
| eICU completo: 200.859 estadias, 139.367 doentes, 335 unidades, 208 hospitais | Pollard et al., *Sci Data* 2018 |
| APACHE IVa disponível em ~50.106 de 200.859 estadias | arXiv 1812.02275 |
| Achilles: ~250 análises de caracterização; **Achilles Heel removido**; DQD substitui-o; >3.500 checks; Kahn | OHDSI Software Tools; Book of OHDSI cap. 15; fórum OHDSI (2022) |
| MedDRA: conceitos de classificação sem "Maps to" completo; ~13.672 relações adicionadas para termos frequentes; hierarquias não devem ser fundidas | Fórum OHDSI; Kallfelz et al., SDTM→OMOP (6.613 `source_to_concept_map`) |
| Índice de Tipton: [0,1]; bandas 0,9/0,8/0,5; ponderação por odds inversas; pressuposto de cobertura | Degtiar & Rose, arXiv 2102.11904; *Epidemiology* 2023 (doi 10.1097/EDE.0000000000001633) |
| MIMIC-IV Demo: 100 doentes; sem tabelas de questionários/PROs | PhysioNet; esquema MIMIC-IV 2.2 |

---

*Parecer emitido no âmbito da preparação para a entrevista técnica na Promptly Health. Documentos relacionados: `RWE_ANALYTICS_STRATEGY.md` (plano diretor analítico), `RWE_ANALYTICS_DOSSIER.md` (métodos estudo-a-estudo), `PROMPTLY_HEALTH_INTERVIEW_PREP.md` (preparação de entrevista).*
