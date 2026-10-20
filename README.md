# Autobibliograph Revision · CitaLens

Aplicação de apoio à revisão de citações em PDFs acadêmicos. Cataloga a bibliografia antes de mapear todas as páginas, consulta as fontes citadas e avalia cada vínculo entre afirmação, ocorrência e obra. Mantém separadas a resolução do marcador, a identidade bibliográfica, a disponibilidade do conteúdo e a sustentação semântica.

Cada trecho de análise e recuperação corresponde a um parágrafo de texto corrido, sem janelas de tamanho fixo ou sobreposição. A extração nativa e o OCR identificam elementos fora da prosa, como tabelas, imagens, diagramas, legendas, títulos e rodapés, para excluí-los desses trechos. A bibliografia continua sendo catalogada a partir do texto original extraído. Essa separação usa estrutura e geometria da página; documentos com diagramação ambígua podem exigir revisão humana.

Frontend React/Vite em JavaScript e português; dez APIs FastAPI, PostgreSQL/SQLAlchemy/Alembic, Weaviate, Celery/RabbitMQ, OCR Tesseract e relatórios ReportLab. O relatório auxilia a revisão humana; não certifica veracidade, ausência de plágio ou conformidade integral com uma norma.

## Executar localmente

Requer Docker Engine com contêineres Linux e Docker Compose v2. Reserve aproximadamente 14 GB de memória para o conjunto e espaço para as imagens, o modelo local e PDFs. O primeiro build instala as dependências; a primeira inicialização baixa aproximadamente 1,2 GB de pesos BGE-M3. Os embeddings rodam em CPU, sem GPU obrigatória. Não é necessário instalar Python, Node, Ollama ou Tesseract no host para usar Compose.

No Windows, com Docker Desktop iniciado no modo Linux:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup-env.ps1
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

Em Linux/macOS, com Python disponível apenas para gerar os segredos:

```sh
python3 scripts/setup_env.py
docker compose config --quiet
docker compose up --build -d
```

Alternativamente copie `.env.example` para `.env` e substitua cada `CHANGE_ME` por um segredo aleatório distinto. Os scripts preservam um `.env` existente. As credenciais de banco/fila e o segredo interno do SearXNG são gerados localmente; nenhuma conta de usuário vem cadastrada.

- Interface: **http://localhost:8080**
- API pública/OpenAPI: **http://localhost:8000/docs**
- Liveness/readiness: `http://localhost:8000/health/live` e `/health/ready`

Apenas frontend e backend publicam portas, vinculadas a `127.0.0.1`. As APIs internas da aplicação exigem `X-Internal-Token`; Ollama e SearXNG ficam acessíveis somente na rede do Compose. O processo `migrate` deve terminar com código zero antes das APIs. O serviço `ollama-pull` instala o modelo no volume `ollama-data` antes de iniciar o serviço vetorial.

## Experimentar sem credenciais pagas

1. Crie uma conta na interface.
2. Baixe o PDF de demonstração pela interface e envie-o.
3. Selecione **Demonstração** ao iniciar a análise.
4. Acompanhe o catálogo, as três páginas e as três ocorrências da mesma obra.
5. Compare afirmação, passagem e parecer; baixe o relatório.

As anotações sintéticas produzem `SUPPORTED`, `PARTIALLY_SUPPORTED` e `CONTRADICTED` para três contextos conhecidos. Não são pesquisa web nem inferência de um modelo. PDFs arbitrários enviados no modo demo não recebem confirmações inventadas: somente as anotações exatas da fixture podem produzir esses pareceres.

Fixtures CC0, anotações e exemplo: [tests/fixtures](tests/fixtures/), [relatório PDF](tests/fixtures/example-report.pdf). O exemplo foi gerado a partir de um snapshot sintético identificado. Os testes medem extração, vinculação e classificação separadamente; os números da fixture não estimam a acurácia em teses reais.

## Configurar análise real

Edite `.env`, preencha `GROQ_API_KEY` com a chave da sua conta Groq e recrie os serviços com `docker compose up --build -d`.

```dotenv
WEB_PROVIDER=searxng
SEARXNG_BASE_URL=http://searxng:8080
CROSSREF_MAILTO=seu_contato@example.org
OPENALEX_ENABLED=true
OPENALEX_API_KEY=sua_chave_openalex
LLM_PROVIDER=groq
GROQ_API_KEY=sua_chave_groq
LLM_MODEL=qwen/qwen3.8-27b
LLM_BASE_URL=https://api.groq.com/openai/v1
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=bge-m3:567m
EMBEDDING_DIMENSIONS=1024
EMBEDDING_VERSION=2
EMBEDDING_BASE_URL=http://ollama:11434
EMBEDDING_BATCH_SIZE=8
EMBEDDING_TIMEOUT=120
```

Crossref confere metadados e **OpenAlex busca os artigos por DOI ou título**, priorizando links públicos de PDF em acesso aberto. Quando disponível, o PDF armazenado pelo OpenAlex é uma alternativa autenticada com `OPENALEX_API_KEY`. A busca web complementar usa **SearXNG open source**, executado localmente no Compose. O conteúdo da fonte é efetivamente obtido e registrado antes de entrar na avaliação; metadados e snippets não servem de evidência. Um DOI válido com título/autores/ano incompatíveis não confirma a obra.

Para obter a chave OpenAlex, crie uma conta em [openalex.org](https://openalex.org), confirme o e-mail e copie a chave em **Settings → API key**. Salve-a em `OPENALEX_API_KEY` no `.env` e execute `docker compose up -d --build web-search-service`. Consultas básicas de metadados podem funcionar sem chave; o download do acervo de PDFs do OpenAlex exige uma chave válida e consome a cota da conta. Veja [autenticação](https://help.openalex.org/api/authentication/), [acesso ao conteúdo](https://help.openalex.org/access/fulltext/) e [operação local](docs/operations.md#artigos-diretos-e-chave-do-openalex). Inicie uma nova análise para usar a integração em documentos já analisados; os resultados anteriores permanecem preservados.

O SearXNG local não exige chave de uma API de busca externa. `SEARXNG_SECRET_KEY` é um segredo interno da instância, gerado pelos scripts de configuração. Para criar manualmente um novo valor, execute `python -c "import secrets; print(secrets.token_urlsafe(48))"`, salve-o em `SEARXNG_SECRET_KEY` no `.env` e recrie o serviço com `docker compose up -d --force-recreate searxng`. O comando apenas gera um segredo novo; não lê chaves existentes. Veja [configuração e operação](docs/operations.md#busca-web-local-e-chave-do-searxng).

Brave continua opcional: use `WEB_PROVIDER=brave` e uma `BRAVE_API_KEY` obtida na sua conta do provedor. O segredo interno do SearXNG não substitui essa credencial.

O padrão é `LLM_PROVIDER=groq` com **Qwen3.8-27B** (`qwen/qwen3.8-27b`), disponível como [modelo preview na Groq](https://console.groq.com/docs/models). As requisições vão à Groq, autenticadas por `GROQ_API_KEY`, com [JSON Schema estrito](https://console.groq.com/docs/structured-outputs). O adaptador Groq não usa `OPENAI_API_KEY`.

`GROQ_API_KEY` começa vazia: sem ela, a análise real registra a limitação e retorna parecer inconclusivo. Os embeddings usam **BGE-M3 local via Ollama**, sem chave de API paga. O modelo tem licença MIT, suporta múltiplos idiomas e produz vetores de 1.024 dimensões; veja o [modelo BAAI](https://huggingface.co/BAAI/bge-m3) e o [pacote Ollama](https://ollama.com/library/bge-m3). Os textos de embeddings permanecem no contêiner local. A avaliação das afirmações continua usando Groq.

Para desativar embeddings, use `EMBEDDING_PROVIDER=none`; a recuperação lexical permanece disponível. Para desativar o LLM, use `LLM_PROVIDER=none`. A demonstração usa somente as anotações sintéticas para os pareceres, sem inferência do LLM.

OpenAI permanece como adaptador opcional de chat e embeddings; o Ollama incluído também pode servir chat após instalar um modelo apropriado. Ao trocar de provedor, configure explicitamente URL, modelo e dimensão conforme [provedores](docs/providers.md). Mudanças de modelo/dimensão/versão de embeddings requerem [reindexação dos documentos existentes](docs/operations.md#embeddings-e-reconstrução).

Uma fonte anexada pode receber confirmação explícita de identidade pelo usuário, registrada como declaração humana. Inicie uma nova análise para aplicar a fonte ou uma correção de vínculo; os pareceres anteriores permanecem acessíveis. A regra de estilo selecionada é registrada, mas não há certificação automática de ABNT/APA/Vancouver.

Parágrafos longos são preservados inteiros. Se excederem o limite de entrada dos embeddings ou do LLM, a análise registra a limitação correspondente, sem cortar o texto silenciosamente. Para aplicar a seleção de prosa a um documento já analisado, inicie uma nova análise: reindexar ou gerar novamente o PDF não recalcula os pareceres históricos.

## Relatório resumido

Novos relatórios PDF têm **no máximo 20 páginas**, incluindo resumo, cobertura e detalhes. Resultados e limitações repetidos são agrupados, e os achados que exigem atenção recebem prioridade. Quando nem todos os detalhes cabem, o PDF informa a seleção e orienta a consulta integral na interface; os resultados persistidos não são excluídos.

Relatórios anteriores continuam disponíveis como snapshots históricos. Gere uma nova versão do relatório para usar o formato resumido; apenas baixar uma versão antiga não altera seu conteúdo.

## Estrutura e documentação

```text
frontend/                   React/Vite, nginx, testes Playwright
services/                   dez APIs; worker e dispatcher no backend
packages/contracts/         schemas Pydantic independentes do ORM
packages/common/            configuração, banco, models, autenticação
packages/document_processing/ extração, catálogo, mapeamento, chunks, PDF
packages/providers/         busca, downloads, embeddings, Weaviate, RAG, LLM
migrations/                 revisão SQL imutável, Alembic, privilégios
tests/                      unidades, contratos, integração e fixtures CC0
scripts/                    configuração, fixtures e validação
docs/                       arquitetura, operação e evidências de validação
```

- [Arquitetura e três diagramas Mermaid](docs/architecture.md)
- [Modelo relacional, cardinalidades e propriedade](docs/data-model.md)
- [Contratos HTTP e eventos](docs/contracts.md)
- [Provedores, prompts e segurança da evidência](docs/providers.md)
- [Operação, migrations, backup, retenção e exclusão](docs/operations.md)
- [Limitações de extração e mapeamento](docs/parser-limitations.md)
- [Comandos executados e resultados observados](docs/validation.md)
- [Frontend e testes de navegador](frontend/README.md)

## Testes

Com Python 3.12:

```sh
python -m pip install -r requirements.lock
python -m pytest -q
python scripts/generate_fixtures.py
```

Os testes que exigem OCR real ou infraestrutura são identificados e pulados quando o requisito falta. Não substituem esses serviços silenciosamente.

Com o Compose em execução:

```sh
docker compose --profile test run --rm integration-tests
```

Essa execução inclui PostgreSQL vazio com upgrade/downgrade, constraints/privilégios, Weaviate real via HTTP/gRPC, e fluxo HTTP com fila/workers até o download. O teste de migrations cria um banco temporário com nome exclusivo. Nunca aponte as credenciais de integração para um banco de produção.

Testes de interface:

```sh
cd frontend
npm ci
npm run build
npx playwright install chromium
npm run test:e2e
```

Os testes padrão do navegador simulam somente a API para verificar a interface. O cenário conectado ao backend é opt-in, conforme o README do frontend.

## Situação desta entrega

Código, fixtures e testes estão implementados. A validação observada está em [docs/validation.md](docs/validation.md), com as verificações não executadas separadas. Builds e execução integral em contêineres dependem de um Docker Engine disponível. APIs pagas e modelos locais dependem de credenciais/modelos configurados.

O parser inicial usa regras conservadoras e preserva ambiguidades. PDFs com colunas complexas, marcadores incomuns, fórmulas, traduções e atribuições indiretas podem exigir revisão humana. O LLM recebe contexto delimitado, sem ferramentas, e sua saída passa por validação de proveniência e citações literais. Esses controles não tornam a avaliação semântica infalível.
