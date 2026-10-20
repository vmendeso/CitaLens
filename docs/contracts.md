# Contratos HTTP e eventos

OpenAPI é gerado por cada API em `/openapi.json` e `/docs`; as APIs internas exigem token também nessas rotas. Versão de contrato: `1`. Os schemas Pydantic não herdam models SQLAlchemy. A API pública usa Bearer JWT emitido após autenticação por senha Argon2id.

## API pública

Prefixo `/api/v1`. UUIDs identificam objetos. Todos os acessos verificam dono do documento/análise, inclusive arquivos, evidências, fontes e relatórios.

| Método e caminho | Entrada | Saída |
|---|---|---|
| POST `/auth/register` | email, password (10–128 caracteres), name | 201, access_token, user |
| POST `/auth/login` | email, password | access_token, token_type, user |
| GET `/auth/me` | Bearer | perfil autenticado |
| POST `/documents` | multipart `file` PDF | 201, documento e current_version_id |
| GET `/documents` | limit 1–100, offset | envelope paginado |
| GET `/documents/{id}` | UUID | documento e versão |
| GET `/documents/{id}/file` | UUID | PDF autorizado |
| DELETE `/documents/{id}` | UUID | 202, DELETING; repetir recupera exclusão que esgotou tentativas |
| POST `/documents/{id}/analyses` | AnalysisOptions | 202, id/status/progress_url |
| GET `/documents/{id}/analyses` | paginação | histórico |
| GET `/analyses/{id}` | UUID | estado, configuração, avisos, resumo e relatórios |
| GET `/analyses/{id}/progress` | UUID | etapas e contagens reais |
| POST `/analyses/{id}/cancel` | UUID | 202, solicitação cooperativa |
| GET `/analyses/{id}/references` | paginação | catálogo original, problemas e ocorrência detectada |
| GET `/analyses/{id}/pages` | paginação | extração e cobertura por página |
| GET `/analyses/{id}/citations` | paginação, page, status | ocorrências, candidatos, revisões |
| GET `/analyses/{id}/findings` | paginação, page, reference_id, severity, status | pareceres e quatro dimensões |
| GET `/findings/{id}/evidence` | UUID | citações literais, texto armazenado, fonte e localização |
| POST `/citations/{id}/review` | reference_id, comment | ação auditada; aplicar em nova análise |
| POST `/findings/{id}/review` | decision, comment | ação auditada; original preservado |
| POST `/analyses/{id}/sources` | multipart file, reference_id, version, identity_confirmed | fonte anexada; aplicar em nova análise |
| GET `/sources/{id}/file` | UUID | PDF da fonte quando disponível |
| POST `/analyses/{id}/reports` | UUID | 202, nova versão e rota de acompanhamento |
| GET `/reports/{id}/download` | UUID | PDF autorizado |
| GET `/demo/manuscript` | Bearer, demonstração habilitada | fixture PDF sintética |

`AnalysisOptions`:

```json
{
  "mode": "real",
  "citation_style": "unspecified",
  "language": "pt",
  "bibliography_page_start": null,
  "bibliography_page_end": null,
  "copy_sources_from": null
}
```

As páginas de intervalo são físicas, começando em 1; os dois limites devem ser informados juntos. Estilos aceitos: `unspecified`, `ABNT`, `APA`, `Vancouver`; a seleção registra a intenção, sem validar normas não implementadas. Um run já ativo é devolvido em vez de criar outro concorrente. Por padrão, a nova análise reaplica fontes anexadas e decisões humanas da análise anterior do mesmo documento.

```json
{
  "code": "VALIDATION_ERROR",
  "message": "Verifique os dados enviados.",
  "correlation_id": "006aa379-29ad-4cbb-a531-ff50e28936fc",
  "details": [{"location": ["body", "password"], "message": "String should have at least 10 characters"}]
}
```

Respostas de erro omitem corpos de provedores e dados secretos. A correlação também aparece em `X-Correlation-ID`. Listagens usam `{items,total,limit,offset}`. Objetos de outro usuário retornam 404; token ausente/expirado retorna 401; pré-requisito/estado incompatível, 409.

## APIs internas

Todas ficam na rede interna e exigem `X-Internal-Token`. Identidade do usuário vem do backend autenticado, nunca diretamente do navegador.

| Serviço | Rotas principais |
|---|---|
| upload | POST `/files`, POST `/artifacts`, GET `/files/{id}?user_id=…`, DELETE `/documents/{id}?user_id=…` |
| OCR | POST `/extract`: file_id, user_id, document_version_id |
| chunks | POST `/chunk`: pages, origin, document/user/version/work/source IDs |
| vector | POST `/index`, POST `/search`, DELETE `/documents/{id}?user_id=…` |
| knowledge | POST `/runs/{id}/stages/{stage}`, GET `/runs/{id}/summary`, GET `/runs/{id}/snapshot`, revisões/fontes, POST `/documents/{id}/reindex?user_id=…` |
| web-search | POST `/resolve`: referência, dono/documento, mode, max_external_queries |
| RAG | POST `/retrieve`: claim, chunks persistidos e escopo obrigatório |
| LLM | POST `/assess`: afirmação/ocorrência/referência, fonte, evidências, cobertura e orçamento |
| report | POST `/render`: snapshot; retorna bytes application/pdf |

`SOURCE` e `MANUSCRIPT` são origens distintas. Embeddings, índice e resultados de busca não concedem autoridade: knowledge verifica novamente cada passagem contra seu chunk SQL e a mesma obra/versão/dono/documento. `packages/contracts/assessment.py` define os contratos estruturados.

Liveness em `/health/live`; readiness em `/health/ready`. APIs com banco verificam conectividade; vector verifica Weaviate; serviços sem estado ficam prontos em modo degradado sem chaves pagas e retornam a limitação na operação.

## Eventos e estados

O outbox persiste a intenção na mesma transação de criação/conclusão da etapa:

```json
{
  "event_id": "UUID",
  "run_id": "UUID",
  "stage": "MAPPING_CITATIONS",
  "event_version": 1
}
```

| Task | Fila | Payload adicional |
|---|---|---|
| `citalens.stage` | ingest, analysis, sources, evaluation ou reports | run_id, stage |
| `citalens.report` | reports | report_id |
| `citalens.delete` | maintenance | document_id, user_id |

PDFs, texto extraído e contextos grandes não transitam na fila. Cada evento tem ID e chave únicos; `processed_events` registra a conclusão. `PENDING`, `PUBLISHED` e `DEAD_LETTER` descrevem a entrega do evento, independentes do estado do negócio. Falha de broker não é registrada como publicação.

As etapas seguem: `QUEUED → INGESTING → EXTRACTING_REFERENCES → MAPPING_CITATIONS → RESOLVING_SOURCES → EVALUATING → GENERATING_REPORT → COMPLETED / COMPLETED_WITH_WARNINGS`. Também há `FAILED` e `CANCELLED`.

Quatro tentativas de execução por etapa; dispatcher usa backoff e limite de 20 tentativas de entrega/recuperação. Ao esgotar, grava erro visível e falha a análise ou relatório pendente. Exclusão incompleta mantém documento inacessível e registra `deletion_error`; repetir DELETE reinicia a entrega após resolver a causa.
