# Validação observada — 20/10/2026

O host era Windows, sem Python executável, Node ou Docker no PATH. Foram utilizados runtimes portáteis de Python 3.12.10, Node 22.16.0, Docker CLI 28.2.2/Compose 2.36.2 e PostgreSQL 16.9, sem instalar serviços do Windows. Ferramentas de verificação ficam em `.tools/` ou no diretório temporário e não fazem parte do repositório. As imagens Python usam 3.12.11; essa diferença de patch não foi validada por build de contêiner.

## Resultados executados

| Verificação | Comando/equivalente executado | Resultado observado |
|---|---|---|
| Dependências Python | `.tools/python/python.exe -m pip install -r requirements.txt` | Instalação concluída; versões transitivas congeladas em requirements.lock |
| Consistência de dependências | `python -m pip check` | Sem dependências quebradas |
| Compilação Python | `python -m compileall -q packages services migrations scripts` | Sem erros de sintaxe |
| Models e contratos | imports das 10 APIs, `app.openapi()`, `configure_mappers()` | 10 OpenAPI gerados; 26 tabelas mapeadas |
| Suíte Python padrão | `python -m pytest -q` | **94 passed, 7 skipped**, 1,61 s |
| Integrações PostgreSQL locais | comando abaixo | **4 passed, 1 deselected**, 3,90 s |
| Compose | `docker compose config --quiet` | Código zero; 19 serviços padrão resolvidos |
| Build frontend | `npm run build` | Vite: 36 módulos; bundle JS aproximadamente 245 KB, 76 KB gzip |
| Navegador | `npm run test:e2e`, Chrome local | **5 passed, 1 skipped**, 11,1 s |
| Fixture e relatório | `python scripts/generate_fixtures.py` | PDFs e snapshot gerados; relatório de 4 páginas |
| Geometria do relatório | inspeção de blocos com PyMuPDF + previews PNG | Nenhum bloco textual fora da página; quatro páginas inspecionadas visualmente |
| Crossref real | `JsonProvider.request_json` para DOI público `10.1038/nphys1170` | HTTP real retornou o DOI e o título “Measured measurement”; 1 chamada contabilizada |
| Download HTTPS real | `download_public("https://example.com", ...)` | HTTP 200, text/html, 577 bytes; conexão TLS com DNS/IP fixado |
| Whitespace | `git diff --check` | Código zero |

As durações são observações dessa execução, não benchmarks. Foram emitidos avisos de depreciação de dependências (PyMuPDF/SWIG, AnyIO/Starlette, Authlib e configuração Alembic), sem falha dos testes.

Integração executada no PostgreSQL temporário, que escutava somente em loopback:

```powershell
$env:RUN_INTEGRATION = "1"
$env:INTEGRATION_ADMIN_DATABASE_URL = "postgresql+psycopg://migrator@127.0.0.1:55432/postgres"
.tools/python/python.exe -m pytest tests/test_integration.py -k "not real_queued" -q
```

Os testes criaram e removeram um banco `citalens_test_<uuid>` exclusivo. Validaram:

- Upgrade em PostgreSQL vazio, 26 tabelas alinhadas aos models e downgrade.
- Privilégios por domínio e rejeição de run entre usuários/documentos incompatíveis, página duplicada e evento duplicado.
- Fluxo persistido completo com catálogo antes do mapeamento, fontes depois do mapeamento, três pareceres contextuais, PDF, isolamento entre usuários, replay idempotente, cancelamento e relatório parcial.
- Bloqueio de exclusão durante relatório pendente.
- Exaustão de outbox levando a erro terminal visível; falha de publicação sem registro falso de sucesso.

O processo PostgreSQL temporário foi encerrado após os testes. Os servidores de preview da interface também foram encerrados.

No teste persistido, a lógica das dez APIs, parser, RAG, avaliação demonstrativa, banco e renderizador é real. **O transporte HTTP foi roteado por TestClient e os eventos foram entregues manualmente ao worker**. Weaviate foi mantido indisponível para exercitar fallback lexical. Esse teste não comprova entrega por RabbitMQ nem indexação real em Weaviate.

Os cinco testes Playwright usam mocks HTTP declarados somente nos testes. Cobrem revisão/download, responsividade/arquivo inválido, sessão expirada, correção de vínculo/fonte complementar e falha/retomada de exclusão. A interface foi inspecionada em desktop 1440 px e móvel 390 px. O teste de navegador conectado ao Compose existe e foi ignorado explicitamente.

## Medições das fixtures

`tests/fixtures/observed-metrics.json` registra resultados separados:

- Extração: 3 de 3 frases esperadas recuperadas integralmente.
- Vinculação: 3 de 3 ocorrências esperadas vinculadas.
- Renderização: 4 páginas, zero blocos textuais fora da página.
- Classificação por modelo: **não medida**. Os três estados semânticos do demo são anotações determinísticas da fixture.

O conjunto é pequeno, sintético e CC0. Esses números não medem precisão/recall em documentos reais e não sustentam promessa de acurácia.

## Não validado neste ambiente

`docker compose build backend-api` foi executado e falhou antes do build porque não havia Docker Engine: o named pipe `//./pipe/docker_engine` não existia. Não foi possível executar:

- Builds das imagens, startup/healthchecks em contêineres ou fluxo completo via Compose.
- RabbitMQ real com workers Celery, reentrega após queda de processo e recuperação após reinício de contêiner.
- Weaviate real via HTTP/gRPC. O teste opt-in cobre indexação, busca filtrada, idempotência e exclusão, mas não foi executado.
- OCR Tesseract real. Fixtures escaneada/mista, limites e falhas estão testados; o teste com binário real foi ignorado.
- OpenAI, Brave e Ollama reais, por falta de credenciais ou modelos instalados. Contratos e validações foram testados com respostas controladas.
- Fidelidade semântica de LLM em corpus acadêmico real, traduções, versões de livros e estilos normativos completos.

Os sete skips da suíte padrão correspondem a um teste OCR, cinco integrações e um teste Weaviate. Quatro das cinco integrações foram posteriormente executadas com PostgreSQL real, como descrito acima; a integração de fila real permaneceu não executada.

## Versões e documentação consultada

Foram escolhidas versões fixas e compatíveis, sem alegar que sejam as versões mais recentes. Dependências Python completas estão em `requirements.lock`; frontend em `package-lock.json`; imagens no Compose/Dockerfiles.

| Componente | Versão fixada | Referência oficial consultada |
|---|---|---|
| FastAPI | 0.115.14 | [Releases](https://fastapi.tiangolo.com/release-notes/), [tarefas demoradas](https://fastapi.tiangolo.com/tutorial/background-tasks/) |
| SQLAlchemy / Alembic | 2.0.41 / 1.16.2 | [Constraints](https://docs.sqlalchemy.org/en/20/core/constraints.html), [revisão de migrations](https://alembic.sqlalchemy.org/en/latest/autogenerate.html) |
| Pydantic / Settings | 2.11.7 / 2.9.1 | [Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) |
| PostgreSQL | 16.9 | [Distribuição Windows](https://www.postgresql.org/download/windows/), [binários EDB](https://www.enterprisedb.com/download-postgresql-binaries) |
| Celery / RabbitMQ | 5.5.3 / 4.1.2 | [Tasks 5.5.3](https://docs.celeryq.dev/en/v5.5.3/userguide/tasks.html) |
| Weaviate servidor / cliente | 1.32.9 / 4.16.9 | [Tabela de compatibilidade](https://docs.weaviate.io/weaviate/client-libraries/python), [busca híbrida](https://docs.weaviate.io/weaviate/search/hybrid) |
| React / Vite / Node | 19.1.0 / 6.3.5 / 22.16.0 | [React](https://react.dev/reference/react/useEffect), [build Vite](https://vite.dev/guide/build) |
| PyMuPDF / ReportLab | 1.26.1 / 4.4.1 | [Page](https://pymupdf.readthedocs.io/en/latest/page.html), [ReportLab](https://docs.reportlab.com/reportlab/userguide/ch6_paragraphs/) |
| OpenAI | chat/embeddings via HTTP configurável | [Saída estruturada](https://developers.openai.com/api/docs/guides/structured-outputs), [embeddings](https://developers.openai.com/api/docs/guides/embeddings) |
| Pesquisa bibliográfica/web | Crossref REST / Brave Web Search | [Crossref](https://www.crossref.org/documentation/retrieve-metadata/rest-api/), [Brave](https://api.search.brave.com/app/documentation/web-search/get-started) |

GROBID e OpenAlex foram considerados na solicitação, mas não são integrações implementadas nesta versão. O adaptador de armazenamento local é funcional; S3 permanece uma extensão da interface, não uma integração validada.

## Migração do LLM para Groq — 20/10/2026

O provedor padrão e o `.env` local passaram a usar `groq`, modelo
`openai/gpt-oss-120b` e URL `https://api.groq.com/openai/v1`. A chave dedicada é
`GROQ_API_KEY`; a chave OpenAI não é reaproveitada. Embeddings continuam
desativados. O adaptador usa JSON Schema estrito e omite o campo `store`,
não suportado pelo chat Groq.

| Verificação | Resultado observado |
|---|---|
| `python -m pytest tests/test_providers.py -q` | **67 passed, 1 skipped** (Weaviate opt-in); HTTP externo simulado |
| `python -m pytest -q --ignore=tests/test_providers.py` | **45 passed, 6 skipped**; skips de OCR e integrações opt-in |
| `docker compose config --quiet` | Configuração válida |
| `git diff --check` | Sem erros |
| Docker Engine | Disponível nesta etapa, versão 29.9.1; os serviços existentes estavam saudáveis |
| `docker compose up --build --no-deps -d --wait --wait-timeout 60 llm-service` | Imagem reconstruída, contêiner recriado e saudável |
| Configuração no contêiner | Provedor Groq, modelo e URL esperados confirmados sem exibir segredos |
| HTTP local `POST /assess` no contêiner | HTTP 200, `INCONCLUSIVE` por ausência de `GROQ_API_KEY`, zero tokens |

Os testes verificaram autenticação separada, contrato HTTP, limites de orçamento,
schema, evidências, recusa, saída truncada e erros 400/401/429/503 sem repetição
automática. O teste HTTP local usou somente uma passagem sintética, sem persistência.
Não houve chamada real ao Groq porque a chave permanece vazia. Qualidade semântica,
cotas e autenticação externa não foram validadas. O novo build e healthcheck
comprovam somente o serviço de linguagem; as limitações históricas das demais
integrações não foram reavaliadas nesta migração.

## Embeddings locais e relatório resumido — 20/10/2026

Embeddings configurados para Ollama 0.40.6 com `bge-m3:567m`, 1.024 dimensões,
geração de índice `2`, lotes de oito textos e timeout de 120 segundos. O modelo
de licença MIT foi baixado para o volume persistente `ollama-data` (identificador
observado `790764642607`, aproximadamente 1,2 GB). Inferência em CPU, sem chave
de API externa. Chamadas entre serviços foram ajustadas para acomodar esse prazo.

| Verificação | Resultado observado |
|---|---|
| `python -m pytest -q` | **137 passed, 7 skipped**, 4,70 s; skips opt-in de OCR, integrações e Weaviate |
| Novos testes de embeddings Ollama | **17 aprovados**; contrato HTTP, lotes, PT/EN, dimensões, timeout, entradas inválidas e isolamento de gerações |
| Stress de relatório com 1.000 pareceres longos | Paginação inicial de 39 páginas, reconstrução resumida com **19 páginas**, sem cortar páginas do PDF |
| Snapshots reais, com 64 e 85 pareceres | Todos representados em seis grupos por relatório; **35 → 5 páginas** e **68 → 5 páginas** |
| Novas versões pela fila normal do aplicativo | Dois relatórios v2 concluídos; download dos arquivos armazenados confirmou **5 páginas cada**; versões anteriores preservadas |
| PDF local | Texto selecionável, determinismo e limites geométricos verificados; primeira página inspecionada visualmente |
| Inferência real no Ollama | Três frases sintéticas geraram três vetores finitos e não nulos de 1.024 dimensões em 2,21 s |
| Reindexação real do documento existente | **156 chunks indexados, zero falhas**, em 133,19 s; geração BGE-M3 `2` |
| Compose, compilação Python e whitespace | Verificações concluídas sem erros |
| Aplicação no Docker | `report-service`, `knowledge-service`, `rag-service` e `vector-service` reconstruídos e saudáveis; Ollama saudável, download concluído com código zero |

O relatório mede a paginação efetiva e reduz a quantidade de detalhes quando
necessário para respeitar o teto de 20 páginas. Totais e cobertura continuam
incluindo todos os registros; excertos e detalhes omitidos são identificados.
Os testes sintéticos de embeddings verificam funcionamento, não medem qualidade
de recuperação em um corpus acadêmico. As análises semânticas históricas não
foram refeitas durante a atualização dos PDFs.

## Parágrafos e busca SearXNG — 20/10/2026

Cada trecho novo corresponde a um parágrafo de prosa dentro de uma página,
sem janelas de caracteres ou sobreposição. A extração preserva o texto original
e registra intervalos de prosa, excluindo tabelas, imagens, diagramas, legendas,
títulos e fólios por estrutura e heurísticas. Texto de resumos HTML também
preserva parágrafos e ignora elementos não narrativos.

| Verificação | Resultado observado |
|---|---|
| Suíte local completa | **185 passed, 7 skipped**, 2,87 s; skips de Tesseract local e integrações opt-in |
| Extração no contêiner com Tesseract instalado | **51 passed**, incluindo OCR real, PDF nativo, parágrafos e remapeamento de offsets; contêiner de teste sem rede |
| PDF real privado | 15 páginas extraídas, **102 parágrafos e 102 chunks**, offsets exatos e texto original preservado; três páginas sem prosa; nenhum aviso estrutural |
| Adaptador SearXNG | **17 testes aprovados**: JSON, orçamento, cache, falhas, snippets sem valor probatório, URLs públicas e Brave opcional |
| SearXNG real | Versão `2026.10.19-81371861f`, imagem fixada por digest; serviço interno saudável, sem porta pública, segredo gerado no `.env` |
| Busca pública de artigo no SearXNG | HTTP 200, **36 resultados**, incluindo arXiv e PDF do NeurIPS; DuckDuckGo informou CAPTCHA, registrado como limitação |
| Rota real `/resolve` | Crossref e SearXNG consultados; exatamente duas consultas no orçamento; nenhum conteúdo criado a partir de snippet |
| Aplicação no Docker | `ocr-service`, `chunk-service`, `web-search-service` e `knowledge-service` reconstruídos e saudáveis |

As regras de prosa valem para novas análises. Os 156 chunks reindexados antes
dessa alteração e os pareceres históricos permanecem preservados; gerar outro
relatório não refaz a análise. A identificação de estruturas é heurística e
pode exigir revisão em layouts complexos. O SearXNG depende da disponibilidade
dos mecanismos externos, que podem impor CAPTCHA ou limites.

Foi pesquisada a API OpenAlex como possível complemento acadêmico. Uma consulta
pública sem chave retornou HTTP 200 e campos `best_oa_location.pdf_url` e
`content_urls`; **o adaptador OpenAlex ainda não foi implementado**. A existência
de um link não confirma autoria, edição ou acesso ao texto integral. Documentação
consultada: [autenticação](https://help.openalex.org/api/authentication/),
[texto integral](https://help.openalex.org/access/fulltext/) e
[cotas atuais](https://help.openalex.org/access/example-costs/).

## Integração OpenAlex e chave real — 20/10/2026

OpenAlex implementado em `packages/providers/openalex.py`, integrado ao resolvedor
de fontes e habilitado com `OPENALEX_ENABLED=true`. Usa `OPENALEX_API_KEY` para
autenticação Bearer, consulta por DOI ou título e valida a correspondência com
a referência e com os metadados Crossref disponíveis antes de aproveitar links.
PDFs públicos anunciados pelo OpenAlex têm prioridade; o PDF do acervo oficial
é uma alternativa autenticada, seguida pela busca web complementar quando houver
orçamento. A integração processa PDF, não TEI XML.

| Verificação | Resultado observado |
|---|---|
| Suíte local completa | **253 passed, 7 skipped**, 3,37 s; skips de OCR local e integrações opt-in |
| Testes específicos OpenAlex | **30 do adaptador e 38 de integração**; identidade, DOI/título, cache, falhas, cotas e proteção da chave |
| Credencial real | Chave preenchida pelo usuário carregada do `.env`; metadados e PDF autenticado obtidos sem exibir o segredo |
| PDF do acervo OpenAlex | Obra `W3038568908`, **5.018.540 bytes, 7 páginas**; extração passou pela validação de título/autoria no host e no contêiner |
| API real do serviço atualizado | `POST /resolve` retornou HTTP 200, identidade `MATCHED`, provedores Crossref e OpenAlex, exatamente duas consultas externas |
| Docker | `web-search-service` reconstruído, recriado com a chave atual e saudável |
| Whitespace | `git diff --check` sem erros |

O teste da rota limitou o orçamento a metadados e não criou fonte, documento ou
análise no banco. O teste separado de download/extrator manteve o artigo somente
em memória. A extração conserva `PARTIAL_TEXT`: obter um PDF não comprova edição
ou cobertura integral. Não houve chamada adicional ao LLM nem reexecução de
análises históricas.

A credencial de conteúdo só é enviada na primeira requisição ao endpoint HTTPS
oficial de PDF; não segue para editoras ou destinos de redirecionamento. O cache
de metadados distingue credenciais por hash. Erros HTTP registram código e host,
sem corpo de resposta ou chave. Retentativas e redirecionamentos consomem o
orçamento, e falhas anteriores à primeira chamada não duplicam a contagem.

## Falhas dos mecanismos SearXNG — 20/10/2026

Os logs reportados identificaram três problemas independentes: CAPTCHA no
DuckDuckGo, HTTP 429 no mecanismo Brave e erro `MainResult` sem atributo `doi`
no filtro `oa_doi_rewrite` da imagem fixada. A configuração desabilita os dois
mecanismos na seleção padrão e mantém o resolvedor DOI do SearXNG vazio e
bloqueado, preservando as consultas acadêmicas Crossref/OpenAlex do aplicativo.

| Verificação | Resultado observado |
|---|---|
| Preferências efetivas | Resolvedor DOI vazio e preferência bloqueada confirmados no contêiner |
| Seleção padrão | DuckDuckGo e Brave desabilitados; definições de rede compartilhadas preservadas |
| Busca por título | 20 resultados via Google CSE; nenhum mecanismo indisponível informado |
| Busca por DOI | 20 resultados via Google CSE; Wikipedia informou HTTP 400 |
| Logs após recriação e buscas | Sem recorrência do erro `MainResult.doi`, CAPTCHA DuckDuckGo ou HTTP 429 Brave nos testes |
| Compose | Configuração válida; SearXNG recriado e saudável, serviço de busca saudável |

A alteração é de configuração, sem mudança no código dos provedores. A falha
isolada da Wikipedia e avisos de inicialização dos componentes opcionais
permanecem observáveis. A disponibilidade futura dos mecanismos externos não é
garantida; desabilitar dois deles reduz a diversidade da busca geral.

## Auditoria ao vivo e Qwen — 20/10/2026

A aplicação foi exercitada no Chrome contra o Compose, com contas de teste
isoladas e manuscritos sintéticos. Documentos, pareceres e relatórios anteriores
do usuário foram preservados.

Correções encontradas durante a execução:

- Bibliografias em duas colunas agora são lidas por coluna, mantendo texto e
  offsets originais. O documento examinado passou de 392 fragmentos para suas
  353 referências numeradas. Autores com iniciais e sobrenome seguido de vírgula
  são interpretados corretamente, inclusive na comparação com o OpenAlex.
- A pesquisa de títulos no OpenAlex trata pontuação de busca como texto, evitando
  HTTP 400. Obras sem identidade confirmada não gastam consultas web adicionais.
- SearXNG carrega somente Mwmbl, arXiv e PubMed. A configuração de limiter foi
  montada e o healthcheck usa `/healthz`. Busca combinada retornou 117 resultados,
  sem mecanismos indisponíveis. Não reapareceram os erros de Tor, Wikipedia,
  Google CSE, CAPTCHA ou filtro DOI nos testes. A versão fixada ainda pode emitir
  uma mensagem sobre cabeçalho de IP ausente em chamadas internas diretas;
  esse aviso não interrompe as buscas.
- O contexto enviado ao LLM respeita o orçamento considerando prompt, schema,
  metadados e saída. Seleciona parágrafos completos por relevância e registra
  páginas e cobertura parcial, sem aumentar os limites configurados.
- A segunda validação de evidências aceita a mesma normalização de espaços e
  quebras de linha do serviço LLM; alterações de palavras e pontuação continuam
  rejeitadas. Escopos de fallback são serializados como JSON válido.
- A interface apresenta páginas e quantidade de parágrafos em linguagem legível,
  substituindo o JSON técnico no modal de evidências.

Os modelos GPT-OSS 120B e 20B retornaram HTTP 403
`model_permission_blocked_org`. Por escolha do usuário, o modelo foi alterado
para `qwen/qwen3.8-27b`, mantendo Groq e JSON Schema estrito. A API confirmou o
identificador e respondeu com sucesso. O prompt `assessment-v2` esclarece que
acesso parcial pode sustentar afirmações delimitadas e explícitas, preservando
a exigência de cobertura para concluir ausência de apoio. Uma chamada com
conteúdo inteiramente fictício retornou `SUPPORTED`, uma evidência e 993 tokens.

| Verificação | Resultado observado |
|---|---|
| Suíte Python completa | **303 passed, 7 skipped**; skips de Tesseract local e integrações opt-in |
| Suíte Chrome | **10 passed**; inclui fluxo demo real no Compose, demais cenários de interface com transporte simulado e casos do formatador |
| Análise real adicional no Chrome | **Aprovada**: manuscrito sintético de 2 páginas citando o artigo público DOI `10.1585/pfr.15.2402039`; 1 referência, 1 ocorrência, 1 parecer `SUPPORTED` e 1 evidência exibida, sem erros JavaScript |
| Recuperação e Qwen na análise real | 31 de 35 parágrafos selecionados, 7 páginas da fonte, 10.414 tokens; cobertura parcial explícita e 1 consulta externa contabilizada, com metadados em cache |
| Serviços Compose | **20 em execução e saudáveis** após a implantação |
| PDF baixado pela interface | 2 páginas, contendo a evidência verificada, dentro do teto de 20; desktop e viewport móvel de 390 px sem transbordamento horizontal |

A tabela presente no manuscrito sintético não gerou uma segunda ocorrência:
somente o parágrafo de prosa foi avaliado. A análise real terminou como
`COMPLETED_WITH_WARNINGS` por registrar as limitações de extração, edição e
cobertura, com zero pareceres inconclusivos nesse caso de teste.

A qualidade semântica geral não é demonstrada por um único artigo ou pelos
testes sintéticos. Para aplicar as correções a documentos antigos, inicie uma
nova análise; gerar somente outra versão do relatório não recalcula os pareceres.
