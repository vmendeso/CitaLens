# Provedores, proveniência e limites

As APIs internas da aplicação usam `X-Internal-Token`. Não devem ser publicadas diretamente.
Os provedores externos são adaptadores HTTP reais; sem configuração a análise
real registra limitações e não gera confirmações simuladas.

## Configuração

| Função | Variáveis | Comportamento |
|---|---|---|
| Descoberta bibliográfica | `CROSSREF_MAILTO` | Crossref REST `/works/{doi}` ou `query.bibliographic`; e-mail opcional para identificação no polite pool. |
| Busca direta de artigos | `OPENALEX_ENABLED=true`, `OPENALEX_API_KEY` | OpenAlex por DOI ou título; metadados corroborados descobrem PDFs públicos de acesso aberto. O PDF do acervo OpenAlex é alternativa com chave válida. |
| Busca web local (padrão) | `WEB_PROVIDER=searxng`, `SEARXNG_BASE_URL=http://searxng:8080` | SearXNG `/search` com `format=json`; consulta Mwmbl, arXiv e PubMed, sem chave de API paga na instância local. URLs são candidatas; snippets não são evidência. |
| Segredo da instância de busca | `SEARXNG_SECRET_KEY` | Segredo criptográfico interno, gerado localmente; o Compose o fornece ao SearXNG como `SEARXNG_SECRET`. Não autentica chamadas a uma API de busca externa. |
| Busca Brave opcional | `WEB_PROVIDER=brave`, `BRAVE_API_KEY` | Brave `/res/v1/web/search`; requer credencial da conta Brave. Resultados descobrem URLs, snippets são descartados. |
| Modelo Groq (padrão) | `LLM_PROVIDER=groq`, `GROQ_API_KEY`, `LLM_MODEL=qwen/qwen3.8-27b`, `LLM_BASE_URL=https://api.groq.com/openai/v1` | `/chat/completions` com JSON Schema estrito (`strict=true`), `max_completion_tokens=1800`, sem `store` e sem ferramentas. |
| Modelo OpenAI | `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, `LLM_MODEL=gpt-4.1-mini-2025-04-14`, `LLM_BASE_URL=https://api.openai.com/v1` | Chat Completions com JSON Schema estrito e `store=false`. O snapshot é configurável; disponibilidade depende da conta. |
| Embeddings locais (padrão) | `EMBEDDING_PROVIDER=ollama`, `EMBEDDING_BASE_URL=http://ollama:11434`, `EMBEDDING_MODEL=bge-m3:567m`, `EMBEDDING_DIMENSIONS=1024` | BGE-M3 no Ollama do Compose; `/api/embed`, `truncate=false`, sem chave paga. Dimensão retornada precisa corresponder à configuração. |
| Embeddings OpenAI | `EMBEDDING_PROVIDER=openai`, `OPENAI_API_KEY`, `EMBEDDING_MODEL=text-embedding-3-small`, `EMBEDDING_DIMENSIONS=1536`, `EMBEDDING_BASE_URL=https://api.openai.com/v1` | Endpoint `/embeddings`; opção externa que recebe os textos enviados para indexação e consulta. |
| Modelo de chat local | `LLM_PROVIDER=ollama`, `LLM_BASE_URL=http://ollama:11434`, `LLM_MODEL=<modelo instalado>` | `/api/chat`, `stream=false`, schema no campo `format`; requer instalar um modelo de chat, pois BGE-M3 produz somente embeddings. |
| Lotes e prazo dos embeddings | `EMBEDDING_BATCH_SIZE=8`, `EMBEDDING_TIMEOUT=120` | Lotes pequenos e timeout de 120 segundos por requisição para inferência em CPU. |
| Índice | `WEAVIATE_HOST`, `WEAVIATE_HTTP_PORT=8080`, `WEAVIATE_GRPC_PORT=50051`, `WEAVIATE_API_KEY` | Weaviate separado; cliente Python 4.16.9 e servidor 1.32.9. |
| Versão de embeddings | `EMBEDDING_VERSION=2` | Integra a identidade da coleção; alterar modelo/dimensão/versão exige reindexação. |

O padrão é `LLM_PROVIDER=groq`, com `GROQ_API_KEY` inicialmente vazia. Preencha
essa variável no `.env` e execute `docker compose up --build -d`. Sem a chave,
o parecer real é `INCONCLUSIVE` com limitação explícita; nenhuma chave OpenAI
é reaproveitada na chamada Groq. O modelo padrão é **Qwen3.8-27B**
(`qwen/qwen3.8-27b`), listado como preview na Groq; a inferência ocorre no endpoint
Groq com JSON Schema estrito. Consulte os
[modelos disponíveis](https://console.groq.com/docs/models), o suporte a
[Structured Outputs](https://console.groq.com/docs/structured-outputs)
e a [configuração da API compatível](https://console.groq.com/docs/openai).

Os embeddings estão habilitados por padrão com BGE-M3 local, multilíngue, de
licença MIT, com 1.024 dimensões e contexto de até 8.192 tokens, conforme o
[modelo BAAI](https://huggingface.co/BAAI/bge-m3). A aplicação usa os vetores densos;
não utiliza a saída esparsa nem ColBERT do modelo. A tag
[`bge-m3:567m`](https://ollama.com/library/bge-m3) ocupa aproximadamente 1,2 GB.
O serviço `ollama-pull` baixa os pesos na inicialização e os preserva em
`ollama-data`; o servidor `ollama/ollama:0.40.6` executa em CPU dentro do Compose.
Os lotes são limitados a oito textos por padrão; entradas acima do contexto
retornam erro em vez de serem truncadas silenciosamente.

Cada entrada de indexação é um parágrafo completo de prosa. Não há janelas
sobrepostas nem corte por quantidade de caracteres durante a segmentação.
O adaptador rejeita textos acima de 24 mil caracteres e Ollama usa
`truncate=false`; quando um parágrafo ultrapassa esses limites ou o contexto
do modelo, a falha de indexação é declarada. O texto original permanece
persistido e a recuperação lexical continua disponível dentro de seus limites.

`EMBEDDING_PROVIDER=none` desativa embeddings: o índice vetorial fica pendente
e a recuperação lexical usa chunks persistidos. Para desativar o chat, use
`LLM_PROVIDER=none`. OpenAI continua opcional; configure explicitamente provedor,
URL, modelo, dimensão e credenciais conforme a tabela. Escolher o modo real
pode consultar Crossref e metadados OpenAlex sem chave, dentro dos limites desses serviços. O SearXNG incluído consulta mecanismos
externos pela internet; Brave é alternativa que exige credencial própria.

SearXNG é um [metabuscador open source](https://github.com/searxng/searxng).
A configuração local habilita JSON em `search.formats`, necessário para a
[Search API](https://docs.searxng.org/dev/search_api.html). Seu segredo interno
é criado pelos scripts `scripts/setup_env.py` ou `scripts/setup-env.ps1` ao
gerar o `.env`. Para criar um valor manualmente, use
`python -c "import secrets; print(secrets.token_urlsafe(48))"` e salve-o como
`SEARXNG_SECRET_KEY`. O campo
[`server.secret_key`](https://docs.searxng.org/admin/settings/settings_server.html)
tem finalidade criptográfica interna; não é uma chave contratada de busca.
Após alterar esse valor, recrie `searxng`.

O arquivo `services/searxng/settings.yml` restringe os mecanismos a Mwmbl,
arXiv e PubMed usando `keep_only`. A configuração evita inicializar motores
Tor não utilizados e consultar scrapers que retornaram bloqueios neste
ambiente. Mwmbl oferece busca web em um índice menor, enquanto arXiv/PubMed
usam suas APIs acadêmicas; nenhuma dessas fontes garante cobertura universal.
Crossref e OpenAlex seguem integrados diretamente ao CitaLens. Veja a
[seleção de mecanismos](https://docs.searxng.org/admin/settings/settings.html#use-default-settings)
e o [motor Mwmbl](https://docs.searxng.org/dev/engines/online/mwmbl.html).

Para desativar somente a descoberta web geral, use `WEB_PROVIDER=none`;
Crossref, OpenAlex e fontes já identificadas continuam disponíveis. Para desativar
OpenAlex, use `OPENALEX_ENABLED=false`. Falha do
SearXNG ou de seus mecanismos gera limitação de descoberta, sem confirmação
inventada da existência ou do conteúdo da obra.

## Artigos pelo OpenAlex

A integração consulta `api.openalex.org` por DOI ou título e compara os metadados
com a referência antes de aceitar candidatos. Prioriza PDFs públicos de acesso
aberto identificados pelo OpenAlex; se necessário, pode obter o PDF do acervo
em `content.openalex.org`. A busca web geral permanece como alternativa.
Encontrar um registro não confirma a sustentação da afirmação: o conteúdo
baixado ainda precisa passar pela verificação de identidade e pela extração.

Crie uma conta em [openalex.org](https://openalex.org), confirme o e-mail e copie
a chave em **Settings → API key**. Salve-a em `OPENALEX_API_KEY` no `.env` e recrie
`web-search-service`. Consultas básicas de metadados admitem acesso anônimo;
o acervo de PDFs exige chave válida. Consulte [autenticação](https://help.openalex.org/api/authentication/)
e [conteúdo disponível](https://help.openalex.org/access/fulltext/) para requisitos e cotas.
A chave fica no servidor e é enviada somente aos endpoints oficiais previstos
pela integração; não acompanha URLs de editoras, repositórios ou buscas gerais.

Esta implementação extrai os PDFs pelo processamento existente, com seleção
de parágrafos de prosa. Não processa os arquivos TEI XML do OpenAlex. Ausência
de PDF, erro de autenticação, limite de cota ou falha de download gera limitação
explícita, sem inventar evidência. Novas análises usam a integração; os pareceres
históricos não são recalculados ao mudar a chave.

## Identidade e acesso

`POST /resolve` recebe `reference`, `user_id`, `document_id`, `mode` e opcionalmente
`max_external_queries` com o orçamento restante da análise. Retorna as dimensões
`identity_status`, `access_status`, `work`, `source`, `attempts`, `limitations` e
`external_queries`. O último campo conta chamadas HTTP externas realmente
tentadas, incluindo retentativas e redirecionamentos; cache não consome consulta.

Um DOI que resolve para título, autoria ou ano divergente produz
`METADATA_MISMATCH`. Título e autoria ou DOI corroborado são necessários.
Diferenças de edição e ano são conservadoramente encaminhadas à revisão humana.
Ausência de registro nunca prova que uma obra foi inventada. As referências
originais não são sobrescritas pelo enriquecimento externo.

Fontes PDF precisam de título e autoria compatíveis nas páginas iniciais.
Esse filtro é conservador e ainda precisa de revisão de edição e de que o arquivo
contém a obra original. O serviço marca PDFs baixados `PARTIAL_TEXT`: download
completo do arquivo não comprova completude da obra. HTML só fornece evidência
quando há identidade bibliográfica em metatags e resumo explicitamente marcado;
páginas de resultados, comentários e texto de navegação não são considerados
o texto da obra. Resumos do depositante no Crossref são `ABSTRACT_ONLY`.

Fontes escaneadas da web sem texto suficiente são rejeitadas como evidência;
o usuário pode anexar cópia autorizada, processada pelo serviço de OCR.
Anexo e confirmação humana de identidade não comprovam edição ou completude.
Não há contorno de paywalls, autenticação ou controles de acesso.

Cada fonte tem IDs determinísticos por usuário/documento/obra/conteúdo, versão,
hash SHA-256, URL inicial/final e data UTC. PDFs e artefatos textuais são arquivados
por API do upload-service. Falha de arquivamento vira limitação explícita;
texto extraído e hash permanecem no catálogo. Dados de demonstração têm
`is_demo=true`, origem `fixture://` e nunca são aceitos na avaliação real.

## Download e limites

Somente HTTP/HTTPS e portas 80/443. Credenciais em URL, hosts locais, endereços
privados, loopback, link-local, metadados, ranges reservados e certas traduções
IPv6 são bloqueados. Todos os resultados DNS precisam ser públicos. A conexão
usa o IP validado e, em TLS, mantém hostname/SNI e validação do certificado.
Cada redirecionamento passa pela mesma validação, impedindo rebinding entre
validação e conexão. Não são usados proxies do ambiente ou cookies. Downloads
públicos não recebem credenciais; a exceção é a chamada dedicada ao acervo
oficial do OpenAlex, que usa sua chave sem a repassar a outros destinos.

O downloader impõe teto total de tempo, DNS limitado, no máximo quatro
redirecionamentos, tipo MIME, tamanho de streaming e rejeita compressão não
solicitada. `MAX_SOURCE_BYTES`, `MAX_SOURCE_PAGES`, `REQUEST_TIMEOUT` e
`MAX_EXTERNAL_QUERIES` são configuráveis. Extração ocorre no contêiner com limites
de memória/CPU definidos no Compose. Não renderiza JavaScript de páginas externas.

Os adaptadores Crossref, OpenAlex, SearXNG e Brave usam cache em memória de até 128 consultas por processo, TTL de
15 minutos, intervalo mínimo de 0,5 s por host e até duas retentativas com backoff
para transporte/429/5xx. O limite é por processo; uma implantação com múltiplas
réplicas deve usar rate limiter compartilhado conforme os limites dos serviços.
A contagem de consultas da aplicação inclui a chamada ao SearXNG; cada chamada
pode produzir várias consultas aos mecanismos externos configurados na instância.
CAPTCHAs, bloqueios de IP, limites e falhas desses mecanismos podem reduzir a
cobertura da descoberta. O SearXNG local não transforma a busca web em operação
offline nem garante acesso ilimitado às fontes.

## Recuperação e avaliação

Manuscritos e fontes passam por seleção de prosa antes da criação dos chunks.
No PDF, blocos nativos e linhas do OCR preservam coordenadas e posições no
texto extraído; sinais de estrutura e geometria identificam tabelas, imagens,
diagramas, legendas, títulos e rodapés que ficam fora dos trechos semânticos.
No HTML aceito como fonte, a estrutura é usada para separar a prosa de tabelas,
figuras e elementos de apresentação. A detecção é heurística: layout complexo,
OCR incorreto e elementos sem separação visual clara podem produzir erros.

A extração original continua disponível para proveniência e catálogo
bibliográfico. O filtro não apaga referências nem altera o PDF enviado.
Cada chunk corresponde a um parágrafo, e a afirmação vinculada a uma citação
usa o parágrafo que contém o marcador, sem a janela sobreposta anterior.
O LLM continua avaliando cada vínculo entre afirmação, ocorrência e obra.
Um parágrafo longo que não cabe no orçamento de avaliação gera limitação
explícita; não é abreviado silenciosamente para obter uma conclusão.

`POST /index` recebe chunks persistidos, usuário e documento; valida hash,
proveniência e posições, mantém origens `SOURCE`/`MANUSCRIPT` em coleções diferentes
e IDs determinísticos. Resultados `indexed_ids`/`failed_ids` reconciliam o estado
SQL. Falhas de lote Weaviate são inspecionadas; não se presume sucesso do lote.
IDs já existentes são idempotentes. Não são fabricados embeddings quando o
modelo está indisponível ou faltam credenciais de um provedor externo.

`POST /search` exige usuário/documento/obra/fonte/versão e consulta exclusivamente
`SOURCE`. Usa busca híbrida lexical+vetorial (`alpha=0.5`), ou BM25 quando
embeddings estão indisponíveis. Cada versão de configuração usa coleção distinta.
`DELETE /documents/{id}?user_id=...` exclui também coleções de configurações antigas.
Readiness verifica a conexão real com Weaviate.

`POST /retrieve` recebe todos os chunks SQL persistidos da fonte e sua identidade.
Hits vetoriais precisam existir nesse conjunto com hash e identidade compatíveis.
Ausência em top-10 amplia a busca a top-30 e à leitura lexical local; passagens
vizinhas são incluídas. Até 24 mil caracteres e 50 passagens entram no contexto.
Se couber, todo o conteúdo disponível é lido. `coverage_sufficient` só informa
cobertura desse conjunto disponível, e não completude bibliográfica da obra.
Misturar manuscrito, outra fonte, versão, documento ou usuário é rejeitado.

`POST /assess` trabalha por afirmação/ocorrência/obra. O prompt versionado em
`services/llm_service/prompts/assessment-v2.txt` e o schema
`packages/contracts/assessment.py` exigem resposta estruturada. A entrada
documental é dado não confiável e não habilita ferramentas. Após a resposta,
validações determinísticas rejeitam IDs inventados, origem/tenant incompatível,
citações não contíguas ou inexistentes, conclusões sem evidência e ausência de
apoio sem texto integral e cobertura suficiente. Não há matching aproximado de
citações: somente espaços podem ser normalizados. A persistência realiza uma
segunda verificação literal.

O prompt `assessment-v2` explicita que cobertura parcial pode fundamentar uma
afirmação cujo alcance esteja limitado à passagem disponível, com identidade
confirmada e evidência direta. `coverage_sufficient=false` não obriga por si só
um resultado inconclusivo para esse caso. A limitação de cobertura continua
registrada e não autoriza concluir ausência de apoio no restante da obra.
O prompt anterior permanece preservado para o histórico das análises.

`max_tokens_budget` recebe o saldo da análise. Antes da chamada, o adaptador usa
uma estimativa conservadora por bytes UTF-8 do contexto/schema, margem de protocolo
e até 1.800 tokens de saída. Se não couber, registra inconclusão sem chamar o
modelo. POSTs de inferência não são repetidos automaticamente; o consumo retornado
é registrado mesmo quando a saída é recusada pela validação. Esse orçamento cobre
a avaliação LLM; embeddings têm chamadas próprias e não entram nesse contador.
Timeouts de rede podem impedir receber contagens de uso do provedor: configure
também limites de gasto na conta e consulte seus registros de faturamento.

Ausência de modelo, fonte ou identidade produz `INCONCLUSIVE`. `ABSTRACT_ONLY`
pode fundamentar afirmação explícita limitada ao resumo, mas não autoriza
condenação por conteúdo que só o texto integral permitiria verificar.
O schema não garante que o raciocínio semântico do modelo esteja correto:
validação humana continua necessária.

## Dados externos, exclusão e reindexação

Crossref, OpenAlex e o provedor de busca recebem somente metadados da referência, nunca
parágrafos do manuscrito. O SearXNG local repassa a consulta bibliográfica aos
mecanismos externos habilitados; hospedar o agregador localmente não mantém
essas consultas fora da internet. Embeddings recebem textos de chunks e consultas, inclusive chunks
do manuscrito quando esse provedor está habilitado. Na configuração padrão,
esses textos são processados pelo Ollama dentro da rede local do Compose;
o download dos pesos não envia documentos. Se o operador trocar a URL ou o
provedor, o processamento passa a ocorrer no destino configurado. O LLM recebe a afirmação,
ocorrência/contexto, referência e passagens da fonte pertinentes. Não recebe PDFs,
credenciais, outros documentos ou acesso a ferramentas.

O adaptador Groq não envia o campo `store`; o adaptador opcional OpenAI envia
`store=false` para desativar armazenamento opcional de completions. Isso não
representa retenção zero contratual em nenhum provedor. Verifique as condições
e controles da conta antes de enviar documentos confidenciais. Ollama mantém
a inferência no endpoint administrado pelo operador; isso depende da
localização/configuração desse endpoint.

PostgreSQL é a fonte de verdade. A exclusão de documento remove arquivos e índices
por usuário/documento, incluindo gerações antigas de embeddings. Alterar modelo,
dimensão ou versão não mistura espaços vetoriais: use a rota interna de reindexação no
knowledge-service, por `scripts/reindex.py`, que limpa o índice e reconstrói a partir dos chunks SQL. Backup, retenção
de logs e políticas de provedores externos são responsabilidades da implantação.

A reindexação não recalcula pareceres nem modifica snapshots anteriores.
Para aplicar a seleção de prosa e as afirmações por parágrafo a uma análise
antiga, execute uma nova análise; gerar novamente o relatório apenas muda
a apresentação dos resultados já persistidos.

## Demonstração e validação observada

A fixture usa DOI sintético `10.0000/citalens.demo`, título
“Efeito de pausas na atenção: estudo sintético”, Silva (2024). Três afirmações
exatas possuem anotações explícitas: apoiada, parcialmente apoiada e contradita.
A correspondência é por fixture; não existe classificador lexical fingindo LLM.
Fora dessas anotações o modo demo retorna inconclusão.

Execute `python -m pytest tests/test_providers.py -q`. Os testes cobrem DNS/SSRF,
redirecionamentos, limite de download, DOI divergente, isolamento de proveniência,
IDs/citações forjados, negação alterada, cobertura, expansão de recuperação,
limites de embeddings e ausência de confirmação sem modelo. Resultados completos
de execução ficam no relatório de validação do repositório.

Após a migração para Groq, a execução local desse arquivo teve **67 aprovados e
1 ignorado**: o ignorado é a integração Weaviate, habilitada com
`RUN_INTEGRATION=1` e um servidor real configurado. A suíte cobre o contrato HTTP
Groq/OpenAI com transporte simulado, chave dedicada, schema estrito, recusa,
truncamento e erros sem repetição automática. Na validação inicial, também foram
executadas duas leituras de rede reais pelo código:

- Crossref `GET /works/10.1038%2Fnphys1170`: retornou DOI
  `10.1038/nphys1170`, título “Measured measurement”, uma requisição.
- Downloader DNS/IP fixado em `https://example.com/`: HTTP 200,
  `text/html`, 577 bytes, TLS validado.

Essas leituras não enviaram manuscritos nem credenciais. A primeira tentativa
de rede no sandbox foi bloqueada por permissões de socket; a repetição de leitura
fora do sandbox foi autorizada pela revisão automática. Naquela validação inicial,
Brave, Groq, OpenAI e Ollama não tiveram chamadas reais executadas por falta de
configuração/credenciais. Os resultados posteriores, incluindo a ativação dos
embeddings locais, ficam em [validation.md](validation.md).

As APIs dos adaptadores foram conferidas na documentação e nas assinaturas do
cliente instalado. Testes com HTTP simulado não validam credenciais, cotas,
qualidade semântica ou disponibilidade do serviço externo. Weaviate/PostgreSQL/
RabbitMQ requerem execução da integração Compose; não considerar essa integração
aprovada somente por testes unitários.

## Documentação oficial consultada

Consultada em 20/10/2026; versões adotadas são fixadas no repositório, não alegadas
como as mais recentes:

- [Cliente Python Weaviate e matriz de compatibilidade](https://docs.weaviate.io/weaviate/client-libraries/python): série servidor 1.32.x com cliente 4.16.x, configuração `Configure.Vectors.self_provided`, gRPC e inspeção de falhas.
- [Busca híbrida Weaviate](https://docs.weaviate.io/weaviate/search/hybrid): consulta com vetor fornecido e filtros.
- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/): identidade bibliográfica e consulta de registros.
- [Autenticação OpenAlex](https://help.openalex.org/api/authentication/): chave gratuita, ativação por e-mail e consultas básicas anônimas.
- [Conteúdo OpenAlex](https://help.openalex.org/access/fulltext/): disponibilidade de PDFs e download autenticado do acervo.
- [SearXNG Search API](https://docs.searxng.org/dev/search_api.html): `/search`, formato JSON habilitado e consultas enviadas aos mecanismos externos.
- [Configuração de servidor SearXNG](https://docs.searxng.org/admin/settings/settings_server.html): `server.secret_key` e variável `SEARXNG_SECRET` para uso criptográfico interno.
- [SearXNG em contêiner](https://docs.searxng.org/admin/installation-docker.html): implantação local do serviço open source.
- [Brave Search API](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started): autenticação `X-Subscription-Token` e descoberta web.
- [Groq Structured Outputs](https://console.groq.com/docs/structured-outputs): `qwen/qwen3.8-27b` aceita `response_format` com JSON Schema e `strict=true`; o adaptador não usa streaming nem ferramentas.
- [Modelos Groq](https://console.groq.com/docs/models): identificador `qwen/qwen3.8-27b` disponível no catálogo da Groq.
- [Compatibilidade da API Groq](https://console.groq.com/docs/openai): URL base `https://api.groq.com/openai/v1` e autenticação com chave Groq.
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat): `response_format` com schema estrito.
- [OpenAI embeddings](https://developers.openai.com/api/reference/resources/embeddings/methods/create): `/embeddings`, dimensão e formato float.
- [BGE-M3 da BAAI](https://huggingface.co/BAAI/bge-m3): licença MIT, múltiplos idiomas, vetores de 1.024 dimensões e contexto de 8.192 tokens.
- [BGE-M3 no Ollama](https://ollama.com/library/bge-m3): pacote `bge-m3:567m`, aproximadamente 1,2 GB.
- [Ollama chat](https://docs.ollama.com/api/chat) e [embeddings](https://docs.ollama.com/api/embed): APIs HTTP reais; `truncate=false` rejeita entrada maior que o contexto.
- [Ollama com Docker](https://docs.ollama.com/docker): execução em CPU e persistência dos modelos em volume.

Também foi aplicada a skill OpenAI Docs para confirmar os contratos da integração.
