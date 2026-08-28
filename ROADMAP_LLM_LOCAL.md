# Roadmap — Harmonização Hospitalar com LLM Local

## Objetivo

Transformar o FHIR-to-OMOP num laboratório reproduzível para harmonizar FHIR e,
progressivamente, HL7 v2, CSV hospitalar, códigos locais e texto clínico para
OMOP CDM. O LLM é um adjudicador local e governado: nunca inventa concept IDs,
nunca publica diretamente no CDM e pode sempre abster-se.

## Princípios obrigatórios

1. Executar código de origem, vocabulário e `Maps to` antes de qualquer IA.
2. Fornecer ao LLM apenas candidatos OMOP Standard, válidos e compatíveis com
   o domínio esperado.
3. Exigir uma resposta estruturada: candidato selecionado ou `ABSTAIN`, score,
   motivo e sinais clínicos usados.
4. Associar cada proposta ao evento, `run_id`, modelo, digest, prompt,
   configuração, vocabulário e índice de retrieval.
5. Publicar apenas mappings aprovados segundo a política de revisão humana.
6. Avaliar em dados held-out; exemplos few-shot nunca podem contaminar o teste.
7. Manter processamento local e definir controlos explícitos para PHI.

## Arquitetura-alvo

```text
FHIR / HL7 v2 / CSV / códigos locais / texto
                    |
                    v
          normalização determinística
                    |
                    v
          retrieval terminológico top-k
                    |
                    v
     LLM local: seleciona ou responde ABSTAIN
                    |
                    v
      domínio + validade + unidade + contexto
                    |
                    v
             revisão humana
                    |
                    v
          mapping set aprovado -> OMOP
```

## Plano de trabalhos

### Fase 1 — benchmark antes do modelo

- Criar um dataset versionado de casos sujos para Condition, Measurement, Drug,
  Procedure e Observation.
- Incluir abreviaturas, erros ortográficos, códigos locais, unidades
  inconsistentes, texto bilingue, códigos expirados, ambiguidades e exemplos
  cuja resposta correta seja `ABSTAIN`.
- Preservar o contexto necessário: sistema/código, texto, unidade, espécime,
  dose, via, datas e recurso/segmento de origem.
- Separar `development`, usado para regras e few-shot, de `held_out`, usado uma
  única vez para avaliação.
- Guardar ground truth com concept ID, domínio, decisão de abstenção, autor da
  revisão e justificação.
- Produzir a baseline determinística antes de testar embeddings ou LLM.

Critério de saída: benchmark validado, sem leakage, com esquema estável e
relatório de baseline reproduzível.

### Fase 2 — semântica FHIR e temporalidade

- Usar primeiro referências FHIR `Encounter/{id}` para associar eventos a
  VISIT_OCCURRENCE.
- Aplicar fallback temporal apenas quando a referência não existe, com desempate
  determinístico e registo do método.
- Derivar OBSERVATION_PERIOD de coverage, registration ou disponibilidade de
  dados quando possível, mantendo uma política explícita para o fallback.
- Separar Type Concepts de eventos diretamente observados dos registos derivados
  por algoritmo.

Critério de saída: nenhuma ligação ambígua silenciosa e todos os eventos cobertos
por um período de observação justificável.

Estado técnico em 2026-08-26: concluído. As referências FHIR são prioritárias e
validadas por pessoa/data; o fallback temporal só aceita um candidato único;
ambiguidades e inconsistências ficam sem link, em quarentena e com auditoria por
`run_id`. A derivação de OBSERVATION_PERIOD regista separadamente cobertura por
Encounter, envelope de eventos e a combinação de ambos.

### Fase 3 — mapping service mult domínio

- Generalizar retrieval, propostas e revisão para Procedure, Observation e
  Device, além de Condition, Drug e Measurement.
- Suportar domain routing explícito quando um recurso FHIR pertence a outro
  domínio OMOP.
- Quarentenar inconsistências de unidade, espécime, dose ou contexto semântico.

Critério de saída: todos os domínios suportados usam o mesmo contrato de decisão
e nenhuma mensagem promete um fallback inexistente.

Estado técnico em 2026-08-27: concluído. O motor comum suporta Condition, Drug,
Measurement, Procedure, Observation e Device. Procedure e Device usam retrieval
SNOMED limitado ao domínio correto; Observation combina apenas conceitos
Standard válidos de SNOMED e LOINC. Todas as propostas são registadas por evento
sem publicação automática, e a aprovação humana preserva o vocabulário real do
conceito selecionado.

### Fase 4 — contrato do LLM local

- Limitar a escolha aos IDs dos candidatos recebidos.
- Exigir JSON validado por schema com `selected_concept_id`, `decision`,
  `confidence`, `reason` e `clinical_signals`.
- Rejeitar IDs externos, respostas inválidas e explicações incompatíveis com o
  domínio.
- Implementar `ABSTAIN` como resultado normal, não como erro.
- Registar digest do modelo Ollama, parâmetros de geração e versão do prompt.

Critério de saída: nenhuma resposta livre consegue alterar um mapping ou o CDM.

Estado técnico em 2026-08-27: concluído. O contrato JSON `mapping-json-v2` está
ativo nos seis adaptadores. IDs fora do top-k, campos adicionais, JSON
inválido e combinações SELECT/ABSTAIN incoerentes falham fechadas. Confiança,
motivo, sinais clínicos, digest do modelo, parâmetros de geração, versão do
prompt e assinatura do índice ficam associados à decisão. Timeout e limites de
geração impedem bloqueio indefinido ou JSON truncado sem validação.

### Fase 5 — comparação experimental

Comparar no mesmo held-out:

1. código + `Maps to` e regras determinísticas;
2. fuzzy lexical;
3. embeddings/retrieval;
4. retrieval + `qwen2.5-coder:7b`;
5. retrieval + `llama3.1`.

Medir por domínio:

- cobertura;
- top-k recall do retrieval;
- precisão e recall das propostas aceites;
- precisão em função da cobertura;
- taxa e correção de `ABSTAIN`;
- calibração do score;
- erros de domínio e validade;
- tempo, memória e custo computacional local.

Critério de saída: escolha do modelo e threshold sustentada por resultados
held-out, não por exemplos demonstrativos.

Estado técnico em 2026-08-27: concluído para o protocolo `phase5-v1`. O runner
cegou `expected`, curadoria e família e executou os cinco braços no held-out
congelado. Ao threshold 0,90, baseline, embeddings, Qwen e Llama obtiveram 50%
de accuracy, 25% de cobertura, 100% de precisão aceite e 33,3% de recall
mapeável. Fuzzy atingiu 52,5% de accuracy e 30% de cobertura, mas com uma
proposta errada. O top-5 recall dos embeddings foi 56,7% global e apenas 35%
nos casos mapeáveis que exigiam fallback. Não houve erros de domínio, conceitos
inválidos nem violações do contrato JSON. Qwen foi mais rápido (97,1 s contra
105,9 s), mas absteve-se em todos os fallbacks; Llama mostrou maior utilidade na
curva diagnóstica. A decisão governada é manter 0,90 e não promover qualquer
ganho do LLM. Llama fica como candidato para calibração futura exclusivamente em
development, depois de melhorar o retrieval; o held-out não será reutilizado
para afinar o sistema.

### Fase 6 — revisão clínica, escala e privacidade

- Calibrar thresholds separados por domínio.
- Medir concordância entre revisores e manter adjudicação cega.
- Executar uma população Synthea de volume e testes de carga.
- Definir redaction, retenção, acesso e logging para futura utilização de PHI.

Critério de saída: desempenho, segurança e workflow humano adequados a um piloto
hospitalar controlado.

Estado técnico em 2026-08-27: marcos 6A e 6B concluídos. O caminho de aprovação única
foi desativado. Duas revisões independentes e cegas, com rationale obrigatório,
são necessárias antes de uma terceira pessoa distinta poder adjudicar. Nem a
concordância entre revisores publica automaticamente. O portal separa filas de
revisão e adjudicação sem expor identidades, votos ou rationales prévios e mede
acordo bruto e kappa de Cohen global e por domínio. A execução por profissionais
clínicos continua pendente. A fronteira de privacidade bloqueia endpoints LLM
externos, redige identificadores diretos antes do prompt e novamente antes de
persistir texto do modelo, e recusa raw source/prompt/response no audit log. PHI
falha fechado sem ativação explícita, aprovação institucional, retenção positiva,
identidade autenticada e allowlist por papel. Nomes e identificadores não
detetáveis por padrões continuam a exigir minimização/DLP upstream; o portal
standalone não é autenticação. A calibração em development e os testes de escala
continuam pendentes; as etiquetas permanecem `PROVISIONAL_TECHNICAL`.

O marco 6C está tecnicamente concluído: thresholds
por domínio maximizam recall sob restrições pré-fixadas de pelo menos 95% de
precisão aceite e zero false maps. O runner recusa split ou hash diferentes,
mantém `deployment_authorized=false` e não altera a configuração de produção.
A execução com Llama teve zero falhas de contrato, mas o top-5 recall foi apenas
20% nos 30 fallbacks mapeáveis. O threshold provisório foi 0,70 para Condition e
1,00 para Drug, Measurement, Observation e Procedure; estes quatro resultados
não autorizam qualquer fallback semântico. Nenhum valor foi promovido.

O marco 6D está tecnicamente concluído. O runner reprodutível mantém geração,
FHIR, staging, manifestos, DuckDB, logs e relatório num diretório isolado sob
`benchmark_results/scale/` e verifica que a base publicada não mudou. Com seed
`6062026` e população-alvo 250, Synthea produziu 282 Patient (250 vivos e 32
mortos) e 284 bundles. A pipeline completa passou todos os gates no commit
`12c2703`, processou 13.404 visitas e 151.888 eventos clínicos em 647,3 s
(683,8 s incluindo geração), produzindo uma base de 1,39 GB. Ficaram por mapear
34/3.796 Conditions (0,90%) e 266/21.099 Procedures (1,26%); Drug,
Measurement, Observation e Device tiveram zero concept IDs não resolvidos. O
relatório local tem SHA-256
`12FB4261956015D4B5826F7189C3AAF621B09933713F213D86F1C8DC217DDD66`.

Os marcos técnicos internos da fase 6 estão concluídos, mas isto ainda não é
autorização para piloto clínico. Continuam obrigatórias a revisão por
profissionais clínicos reais, integração com identidade institucional,
minimização/DLP upstream e aprovação formal de privacidade/retenção. Os
thresholds continuam `PROVISIONAL_TECHNICAL` e não foram promovidos.

### Fase 7 — produto e portabilidade

- Adicionar CI, `renv.lock`, licença, changelog e releases.
- Centralizar configuração de caminhos, modelos e serviços.
- Extrair componentes reutilizáveis para um futuro `clinical-mapping-core`.
- Gerar relatórios imutáveis por execução com testes, DQD e métricas do mapping.

Estado 7A: tecnicamente concluído. A configuração tem perfis versionados
`development`, `benchmark` e `hospital`, precedência explícita de ambiente,
caminhos portáteis ancorados no repositório e validação fail-closed de tipos,
thresholds, classificação PHI e endpoint LLM local. O perfil hospital não pode
reduzir a classificação PHI, threshold 1,0, quality gate completo nem ativar
ruído LIS. A configuração efetiva não-secreta entra no manifesto de execução e
é validada na CI; 145 testes passaram no fecho do marco.

Estado 7B: tecnicamente concluído. Cada publicação bem-sucedida produz JUnit
por run e um relatório JSON v1 metadata-only com configuração segura, agregado
dos inputs, passos e tempos, estado pytest/DQD, cobertura de mapping por domínio,
contagens de governance e SHA-256 exato da DuckDB. O payload é validado
fail-closed, selado com SHA-256, nomeado pelo conteúdo e publicado em duas fases
sem overwrite. Development e benchmark declaram DQD `NOT_RUN`; hospital não
pode desativar o DQD run-linked nem publicar se este falhar a política aprovada.
Os relatórios mantêm `deployment_authorized=false` e revisão clínica obrigatória.
O marco fechou com 154 testes aprovados.

Estado 7C: tecnicamente concluído. A versão inicial é `0.1.0`; Python 3.12 tem lock
universal transitivo com hashes e o stack R 4.6.1/OHDSI tem `renv.lock`. A CI
instala exclusivamente o lock verificado, valida metadados e mantém um workflow
de tags que só publica uma release quando tag, `VERSION`, changelog, testes e
licença coincidirem. O changelog e o checklist de release mantêm explicitamente
o estatuto `PROVISIONAL_TECHNICAL` e bloqueiam qualquer inferência de autorização
clínica. O código e a documentação própria são publicados sob Apache-2.0, com
atribuição preservada em `NOTICE`; dependências, modelos, vocabulários e dados
mantêm os seus próprios termos. O marco fechou com 157 testes aprovados.

Estado 7D.1: tecnicamente concluído. Os contratos `Candidate`, `MappingRequest`,
`MappingDecision` e `ModelProvenance`, o parser fail-closed e o renderer de
prompt foram isolados em `src/clinical_mapping_core/`, sem dependências de
DuckDB, Chroma, Ollama, Streamlit, configuração ou adaptadores hospitalares.
Os seis adaptadores mantêm a API e o comportamento existentes, mas delegam no
novo núcleo e transportam explicitamente a identidade do modelo até à
persistência. FHIR/OMOP, retrieval, PHI, governance e publicação permanecem fora
da fronteira. O pacote não será separado nem publicado antes de sobreviver a um
segundo formato de origem. O marco fechou com 163 testes aprovados.

## Primeiro passo aprovado

Implementar a Fase 1: esquema do benchmark, primeiros casos multi domínio,
separação development/held-out e runner da baseline determinística. Não ajustar
prompts nem thresholds até existir esta medição independente.

### Estado do primeiro marco — concluído em 2026-08-26

- Fixture v1 com 100 casos: 20 por domínio e decisões `MAP`/`ABSTAIN`.
- Separação sem leakage: 60 development e 40 held-out, sem famílias nem concept
  IDs partilhados.
- Conceitos de referência verificados como Standard e válidos no Athena oficial.
- Predictor recebe uma cópia cega do caso sem o objeto `expected`.
- Baseline code-only: 25% cobertura, 100% accepted precision, 33,3% recall dos
  casos mapeáveis, zero false maps e 50% accuracy global.
- Contrato, geração determinística, leakage e baseline cobertos por testes.

As etiquetas permanecem `PROVISIONAL_TECHNICAL`; a revisão clínica documentada
continua obrigatória antes de chamar ao conjunto “gold standard”. As Fases 2 a
5 estão tecnicamente concluídas. O próximo marco é a Fase 6: revisão clínica,
melhoria de retrieval em development, escala e controlos formais de privacidade.
