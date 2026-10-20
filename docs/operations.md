# Operação, dados e recuperação

## Inicialização e verificação

```sh
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=100 migrate backend-api outbox worker-ingest
docker compose exec backend-api python -m pytest -q -m "not integration"
docker compose --profile test run --rm integration-tests
```

O Compose exige `.env`. Gere-o com os scripts do README; nenhuma credencial real está nas imagens. A criação de roles e as migrations ocorre no processo único `migrate`. Dependências usam `service_healthy` e `service_completed_successfully`, conforme [ordenação oficial do Compose](https://docs.docker.com/compose/how-tos/startup-order/).

O LLM padrão usa `LLM_PROVIDER=groq`, `LLM_MODEL=openai/gpt-oss-120b` e `LLM_BASE_URL=https://api.groq.com/openai/v1`. Preencha `GROQ_API_KEY` no `.env` e execute `docker compose up --build -d` para aplicar a integração. A chave OpenAI não autentica chamadas Groq. Sem a chave Groq, a análise real retorna `INCONCLUSIVE` com a limitação registrada. A demonstração usa anotações sintéticas para o parecer.

Embeddings locais ficam habilitados com `EMBEDDING_PROVIDER=ollama`, modelo `bge-m3:567m`, 1.024 dimensões e versão `2`. O serviço Ollama incluído no Compose usa CPU, com limites de 4 GB de memória e quatro CPUs, e não publica porta no host. `ollama-pull` aguarda o servidor e baixa os pesos antes do início de `vector-service`; a primeira execução exige acesso ao catálogo de modelos e pode demorar. Os pesos ficam em `ollama-data` e são reaproveitados nas próximas inicializações. Não é necessária chave paga para embeddings.

API e workers reutilizam pool de conexão com `pool_pre_ping`; operações sem conectividade falham/repetem dentro dos limites configurados. Workers usam publicação confirmada, ACK tardio, rejeição em perda de processo e prefetch 1. Não há result backend: o fluxo coordena etapas pelo PostgreSQL, sem chord. Consulte [tarefas Celery 5.5.3](https://docs.celeryq.dev/en/v5.5.3/userguide/tasks.html).

Healthchecks de workers usam ping dirigido ao nome do próprio worker via broker. Outbox grava heartbeat a cada ciclo; seus erros de publicação ficam no evento. Os liveness/readiness HTTP são independentes de disponibilidade de chaves pagas.

## Artigos diretos e chave do OpenAlex

OpenAlex fica habilitado com `OPENALEX_ENABLED=true`. Ele busca registros por DOI ou título e oferece candidatos a PDF após corroborar os metadados. PDFs públicos de acesso aberto têm prioridade; o acervo do próprio OpenAlex é uma alternativa quando houver conteúdo disponível e uma chave válida.

1. Crie uma conta em [openalex.org](https://openalex.org) e confirme seu e-mail.
2. Em **Settings → API key**, copie a chave da conta.
3. Salve no `.env`:

```dotenv
OPENALEX_ENABLED=true
OPENALEX_API_KEY=sua_chave_openalex
```

4. Aplique a configuração e a implementação:

```sh
docker compose up -d --build web-search-service
docker compose ps web-search-service
```

Inicie uma nova análise para buscar fontes com OpenAlex. Gerar apenas outro relatório não consulta novas fontes nem recalcula pareceres anteriores. Para desativar essa integração, use `OPENALEX_ENABLED=false` e recrie `web-search-service`; `WEB_PROVIDER=none` desativa somente a busca web geral.

Metadados básicos admitem consultas anônimas; downloads do acervo OpenAlex precisam de chave e consomem a cota da conta. Uma chave ainda não ativada por e-mail pode receber HTTP 403. Consulte [autenticação](https://help.openalex.org/api/authentication/) e [acesso ao conteúdo](https://help.openalex.org/access/fulltext/) para os limites atuais. Falhas de acesso ou de identidade são registradas como limitações. O processamento aceita PDF e aplica os filtros de prosa existentes; a integração não importa TEI XML.

## Busca web local e chave do SearXNG

O padrão é `WEB_PROVIDER=searxng` e `SEARXNG_BASE_URL=http://searxng:8080`. O contêiner fica na rede interna do Compose e oferece `/search` com saída JSON habilitada. Não é necessária uma conta paga de busca para essa instância local.

`SEARXNG_SECRET_KEY` é o segredo criptográfico interno da instância, mapeado pelo Compose para `SEARXNG_SECRET`. Em uma instalação nova, `python scripts/setup_env.py` ou `powershell -ExecutionPolicy Bypass -File scripts/setup-env.ps1` gera e salva esse segredo no `.env` junto com os demais. Os scripts preservam um `.env` já existente; se a variável faltar, gere um valor novo e acrescente-o manualmente:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Salve a saída como `SEARXNG_SECRET_KEY=<valor gerado>` no `.env`. O comando cria somente um segredo novo, sem ler uma chave existente. Para aplicá-lo:

```sh
docker compose up -d --force-recreate searxng
docker compose ps searxng web-search-service
```

Se também alterou `WEB_PROVIDER` ou `SEARXNG_BASE_URL`, recrie `web-search-service` com `docker compose up --build -d web-search-service`. Um HTTP 403 ao pedir JSON pode indicar que `json` não está em `search.formats`; confira a configuração montada na instância. Veja a [Search API](https://docs.searxng.org/dev/search_api.html) e o [segredo do servidor](https://docs.searxng.org/admin/settings/settings_server.html).

SearXNG agrega resultados de mecanismos externos, que recebem a consulta bibliográfica. A busca continua exigindo internet e pode enfrentar CAPTCHAs, bloqueios de IP, limites e indisponibilidade. Snippets não entram como evidência: a aplicação precisa obter o conteúdo da fonte e validar sua identidade. Para usar Brave opcionalmente, configure `WEB_PROVIDER=brave` e `BRAVE_API_KEY` da conta Brave; `SEARXNG_SECRET_KEY` não serve como credencial Brave.

Em `services/searxng/settings.yml`, `keep_only` carrega Mwmbl, arXiv e PubMed,
com categorias `general` e `science`. Eles retornaram resultados em consultas
reais neste ambiente. DuckDuckGo, Brave, Google CSE e Wikipedia foram removidos
da seleção por apresentarem CAPTCHA, HTTP 429 ou HTTP 400; Ahmia/Torch e os
demais motores fora dessa lista também não são inicializados. Mwmbl mantém a
busca web com um índice menor; arXiv e PubMed complementam a descoberta
acadêmica. Crossref/OpenAlex continuam como integrações diretas e independentes.
O adaptador opcional da API Brave é separado do mecanismo que consulta
`search.brave.com` dentro do SearXNG. A disponibilidade dos serviços externos
continua sujeita a limites e falhas.

O Compose monta `services/searxng/limiter.toml` porque essa versão inicializa a
configuração de botdetection mesmo com `server.limiter: false`. Apenas loopback
é confiável para cabeçalhos encaminhados. O healthcheck consulta `/healthz` e
informa seu endereço real, `127.0.0.1`. Não se deve inventar um IP público de
usuário nem confiar em todas as redes para eliminar um aviso. Requisições
diretas sem cabeçalhos de proxy usam o endereço do cliente recebido pelo
servidor; nessa versão, o SearXNG ainda pode registrar uma vez por processo o
aviso sobre `X-Forwarded-For`/`X-Real-IP`, sem impedir a busca. Consulte o
[limiter](https://docs.searxng.org/admin/searx.limiter.html) e a
[seleção de mecanismos](https://docs.searxng.org/admin/settings/settings.html#use-default-settings).

A imagem fixada do SearXNG apresenta um erro na reescrita de DOI em resultados
comuns (`MainResult` sem atributo `doi`). A configuração define
`default_doi_resolver: ""` e bloqueia a preferência `doi_resolver`, desativando
essa reescrita. Apenas `active: false` no plugin não basta nessa versão, pois
a preferência pode reativá-lo. A resolução de DOI do CitaLens continua pelas
APIs Crossref/OpenAlex. Consulte a [configuração de preferências](https://docs.searxng.org/admin/settings/settings_preferences.html).

## Migrations e rollback

```sh
docker compose run --rm migrate
docker compose run --rm migrate python -m alembic current
docker compose run --rm migrate python -m alembic upgrade head
```

A revisão inicial é SQL congelado, não importa models atuais para criar tabelas. `scripts/freeze_initial_schema.py` foi usado durante a implementação inicial; **não o use para alterar uma revisão já aplicada**. Mudanças futuras exigem nova revisão Alembic, revisão humana e ensaio em banco vazio/cópia de teste. Consulte [autogenerate e revisão](https://alembic.sqlalchemy.org/en/latest/autogenerate.html).

`docker compose run --rm migrate python -m alembic downgrade base` remove todos os schemas de aplicação e seus dados. Pare as APIs/workers e faça backup antes. As roles de cluster permanecem, pois podem ser usadas por outros bancos. O teste de integração exercita upgrade/downgrade em banco temporário exclusivo, nunca recria o banco do usuário.

`docker compose down` mantém volumes. `down -v` apaga os volumes e deve ser usado somente quando a exclusão de todos os documentos e dados for intencional.

## Backup, retenção e exclusão

PostgreSQL contém os resultados, textos, proveniência e snapshots. O volume `file-data` contém PDFs originais, fontes e relatórios. Faça backups coordenados de ambos com aplicações paradas ou estratégia consistente de snapshots. Weaviate pode ser reconstruído a partir dos chunks SQL. `ollama-data` armazena os pesos locais; conserve-o para evitar novo download. Backups devem ser criptografados e ter política de acesso/retenção definida pelo operador.

Por padrão, o ambiente local retém os dados até exclusão explícita. Não há job automático de expiração. DELETE do documento:

1. Recusa enquanto houver análise ativa ou relatório pendente.
2. Marca documento indisponível para novas operações.
3. Remove vetores em todas as coleções conhecidas por dono/documento.
4. Remove artefatos via upload-service.
5. Remove dados relacionais com cascatas.

Repetir a operação é seguro. Em falha, o documento mantém estado de exclusão; após esgotar retentativas, `deletion_error` fica visível. Repita DELETE após restabelecer os serviços. Backups já produzidos não são apagados pela API e seguem a retenção administrativa.

Uma falha entre upload de arquivo e commit do documento pode deixar artefato sem registro público. O armazenamento é idempotente por hash/dono/documento, mas não há coletor automático de órfãos nessa versão. A reconciliação administrativa deve comparar `storage.files` aos IDs das versões/fontes/relatórios antes de apagar qualquer arquivo.

## Embeddings e reconstrução

Configuração local padrão:

```dotenv
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=bge-m3:567m
EMBEDDING_DIMENSIONS=1024
EMBEDDING_VERSION=2
EMBEDDING_BASE_URL=http://ollama:11434
EMBEDDING_BATCH_SIZE=8
EMBEDDING_TIMEOUT=120
```

Confirme a instalação do modelo e a inicialização do índice:

```sh
docker compose ps -a ollama ollama-pull vector-service
docker compose logs --tail=50 ollama-pull vector-service
docker compose exec ollama ollama list
```

Para repetir um download interrompido, execute `docker compose run --rm ollama-pull` e depois `docker compose up -d vector-service`. Se a máquina ficar sem memória ou exceder o timeout, reduza `EMBEDDING_BATCH_SIZE` antes de recriar os serviços. O timeout de embeddings é separado do prazo das consultas web. `truncate=false` impede corte silencioso de textos longos.

O fingerprint da coleção incorpora modelo, dimensão e versão. Atualizar somente a configuração cria um espaço distinto; os dados SQL permanecem disponíveis, com aviso de recuperação lexical até reindexar.

Um operador pode executar a API **interna** de reconstrução no contêiner backend:

```sh
docker compose exec backend-api python -m scripts.reindex DOCUMENT_UUID USER_UUID
```

O comando valida propriedade no knowledge-service, limpa os vetores daquele documento e reinsere os chunks SQL em lotes. O resultado lista IDs falhos e permite repetir. Não muda os pareceres históricos. As coleções antigas vazias podem ser removidas administrativamente após verificar migração completa. O cliente v4 usa HTTP e gRPC, como exige a [documentação oficial Weaviate](https://docs.weaviate.io/weaviate/client-libraries/python).

## Parágrafos e seleção de prosa

Nas novas análises, cada trecho é um parágrafo de texto corrido; não há janelas sobrepostas. A extração nativa e o OCR usam estrutura, geometria e características do texto para excluir tabelas, imagens, diagramas, legendas, títulos e rodapés detectados. A afirmação avaliada usa o parágrafo que contém a citação. A bibliografia e a proveniência continuam usando a extração original preservada.

A detecção desses elementos é heurística. Confira a cobertura e os trechos quando o PDF tiver múltiplas colunas, tabelas sem bordas, diagramas com muito texto ou OCR de baixa qualidade. Um parágrafo longo permanece inteiro; tamanho acima de 24 mil caracteres ou do contexto do modelo pode impedir seu embedding, e orçamento insuficiente pode impedir avaliação pelo LLM. Essas situações são registradas como limitações, sem corte silencioso do parágrafo.

Para aplicar o filtro de prosa e a nova unidade de afirmação aos pareceres existentes, inicie uma **nova análise**. Reconstruir apenas o índice atualiza a recuperação a partir dos chunks disponíveis, sem repetir a avaliação semântica. Gerar outra versão do PDF reapresenta os resultados armazenados. Nenhuma dessas operações transforma os pareceres históricos em uma nova análise.

## Relatórios resumidos e versões anteriores

O PDF novo tem teto de **20 páginas**, contando todas as seções. O resumo mantém totais e cobertura da análise; resultados repetidos são agrupados e o detalhamento prioriza achados que exigem revisão. Um grupo exige diagnóstico, gravidade, justificativa, escopo, ajuste, limitações e evidências iguais; a afirmação exibida é identificada como exemplo. Referências e evidências compartilhadas aparecem uma vez, e limitações recorrentes são consolidadas.

O renderer mede a paginação real e reduz a quantidade de exemplos e fichas até respeitar o teto, sem cortar páginas nem reduzir o tamanho das fontes. Quando o limite exige omitir detalhes, o relatório declara essa seleção e remete à interface, que continua oferecendo os resultados individuais.

A compactação altera a apresentação do PDF, sem apagar pareceres, evidências ou histórico. Relatórios já gerados permanecem snapshots imutáveis: solicite uma nova versão pela interface para obter o formato resumido. Uma nova análise é necessária para recalcular pareceres com outras fontes ou configurações; gerar apenas o PDF não executa a avaliação novamente.

## Dados enviados a terceiros

- Crossref, OpenAlex e busca web: DOI/título/autores/ano da referência, minimizando o texto do manuscrito. O SearXNG local encaminha a consulta aos mecanismos externos configurados; Brave recebe a consulta diretamente quando selecionado.
- Editoras/repositórios: requisição pública à URL da fonte; nenhum contorno de paywall.
- Embeddings: chunks do manuscrito, das fontes e consultas de recuperação. No padrão Ollama do Compose, ficam no ambiente local; só são enviados a terceiros se o operador configurar um provedor externo.
- LLM configurado: afirmação e contexto da ocorrência, metadados bibliográficos e passagens recuperadas da obra.

Não configure provedor externo para material que não possa ser enviado a ele. Retenção externa depende do provedor, contrato e configurações da conta; a aplicação não pode excluir logs mantidos pelo fornecedor. Em Ollama local, configure o endpoint controlado pelo operador.

Logs de aplicação evitam corpos, credenciais e trechos. PDFs, HTML e passagens são tratados como dados não confiáveis; o LLM não recebe ferramentas. Downloads públicos verificam esquema, DNS/IP e redirecionamentos, fixam o IP da conexão, impõem prazo/tamanho/tipo e rejeitam endereços privados. A chave OpenAlex é usada somente pelos endpoints oficiais da integração e não é repassada a editoras, repositórios ou outros destinos. Não há autenticação em sites de editoras nem contorno de paywall.

Orçamentos de consultas e tokens LLM são reservados no banco antes da operação. Após retorno, a reserva é ajustada ao uso relatado; perda de resposta/crash mantém cobrança conservadora do orçamento. Contagens sem confirmação são limites operacionais, não uma fatura. Tokens de embeddings não entram no orçamento LLM: use os limites da conta do provedor para controlar custo total.

## Solução de problemas

| Sintoma | Verificação |
|---|---|
| Docker Engine não encontrado | Inicie Docker Desktop/Engine em modo Linux; CLI isolado só valida configuração |
| `migrate` falhou | Confira senha/volume PostgreSQL e logs; alterar `.env` não redefine senha de volume já inicializado |
| API 401 | Sessão expirada ou token interno ausente; autentique novamente |
| SearXNG não inicia | Gere `SEARXNG_SECRET_KEY`, salve no `.env` e recrie o serviço; confira a configuração sem imprimir segredos |
| SearXNG responde 403 ao JSON | Habilite `json` em `search.formats` da instância e recrie `searxng` |
| Busca web sem resultados | Confira internet e erros dos mecanismos externos; bloqueio/CAPTCHA/limite não significa ausência da obra |
| OpenAlex retorna 401/403 | Confira `OPENALEX_API_KEY`, confirmação do e-mail da conta e recriação de `web-search-service` após editar o `.env` |
| OpenAlex retorna 429 ou não oferece PDF | Confira cota da conta e disponibilidade da obra; metadados localizados não garantem conteúdo acessível |
| OCR incompleto | Confirme idiomas Tesseract, qualidade do scan e limites; páginas restantes são marcadas não verificáveis |
| `INCONCLUSIVE` com chave ausente | No padrão Groq, preencha `GROQ_API_KEY` e execute `docker compose up --build -d`; falta de chave não gera confirmação de teste |
| Weaviate indisponível | Confira HTTP8080/gRPC50051 internos, chave e versões fixadas |
| Embeddings indisponíveis | Confira `ollama-pull`, `ollama list`, modelo/dimensão no `.env`, memória e prazo de inferência; reindexe documentos anteriores após corrigir |
| Relatório antigo continua longo | Gere uma nova versão do relatório; arquivos históricos não são sobrescritos |
| Trecho antigo contém tabela ou legenda | Inicie nova análise para reaplicar a seleção de prosa; reindexação ou novo PDF não recalculam pareceres |
| Parágrafo sem embedding | Confira limite de caracteres, contexto do modelo e erro de indexação; o parágrafo inteiro é preservado |
| Fonte restrita | Anexe cópia autorizada e confirme a identidade/edição; execute nova análise |
| Análise `FAILED` | Consulte erro/etapas; após corrigir dependência, inicie nova análise preservando o histórico |
| Mensagem `DEAD_LETTER` | Verifique broker/worker; novas análises/relatórios criam novos eventos |
| PDF sem texto | OCR tenta as páginas necessárias; ausência persistente fica explícita na cobertura |

O ambiente local não inclui TLS, gestão multiusuário administrativa, quotas de armazenamento por usuário, auditoria externa, RLS PostgreSQL, alta disponibilidade nem serviço S3. O isolamento da aplicação é feito por autorização por objeto, filtros obrigatórios e privilégios por domínio; as APIs internas confiam nos processos que possuem o token interno. Para exposição fora de localhost, implemente os controles operacionais correspondentes antes de publicar.
