# Extração, parser e relatório

As funções puras ficam em `packages/document_processing/`. O OCR baixa o arquivo pelo identificador no upload-service; não compartilha caminhos de outro contêiner. O relatório recebe exclusivamente um snapshot persistido e versionado.

## Ordem e localização

`catalog_references()` retorna `status=COMPLETED` mesmo quando não localiza uma bibliografia: esse resultado contém uma limitação explícita. `map_citations()` recusa catálogo não concluído. O serviço proprietário deve persistir o catálogo antes de chamá-lo. Todas as páginas terminam com cobertura, inclusive falhas. Uma página parcialmente legível nunca recebe estado de sucesso integral.

Índice físico começa em zero; `physical_page_number` e `page_start/end` começam em um. Offsets são locais ao texto normalizado de cada página. Em uma ocorrência que atravessa páginas, o início pertence a `page_start` e o fim a `page_end`. As coordenadas de blocos usam pontos PDF. Uma nova normalização exige nova versão e remapeamento; offsets não são posições em bytes. `scope=analysis_run_id` isola UUIDs determinísticos entre análises.

A bibliografia é mascarada por regiões, preservando os offsets: texto do corpo na mesma página continua sujeito ao mapeamento. Cabeçalhos de capítulos e anexos encerram a região; o parser também aceita intervalo físico informado pelo usuário. Cabeçalhos incomuns, bibliografias sem separadores e múltiplas colunas podem segmentar incorretamente. O texto original de cada entrada e o restante não interpretado são preservados. Campos não reconhecidos permanecem nulos, sem enriquecimento inventado.

## Extração e limites

PyMuPDF extrai primeiro o texto nativo e a geometria. Texto curto, caracteres de substituição ou áreas de imagem relevantes acionam Tesseract, português e inglês, com limite de pixels e timeout por página. Há também prazo total de extração, inferior ao timeout da etapa; páginas restantes são contabilizadas como não verificáveis quando o prazo termina. OCR falho preserva o texto nativo disponível e registra a falha. Qualidade é diagnóstica, não probabilidade calibrada. Uma imagem decorativa grande pode acionar OCR desnecessário. Ordem geométrica de blocos não garante ordem semântica de tabelas e colunas. Páginas sem conteúdo extraível são conservadoramente não verificáveis.

Um rótulo impresso é procurado em cabeçalho/rodapé, incluindo romanos; na ausência dele usa-se o rótulo interno do PDF quando disponível. Esse rótulo interno não comprova a impressão visível. Não se infere numeração a partir da ordem física.

## Detecção e vínculos

O parser cobre formas comuns autor-data, grupos, sufixos de ano, autores institucionais conhecidos, sem data, `apud`, números em colchetes e intervalos, e sobrescritos Unicode. Colchetes numéricos exigem bibliografia numerada e contexto de prosa; referências próximas a termos de equação são ignoradas. Sobrescritos representados exclusivamente por geometria de fonte, numerais em tabelas e notas sem marcadores reconhecíveis podem não ser detectados. Há casos legítimos que exigem revisão humana.

Duplicatas permanecem como entradas diferentes e tornam vínculos ambíguos. Ano divergente pode gerar candidatos de mesmo autor, mas não vínculo forçado. Sem ocorrência detectada não significa referência incorreta.

Afirmações são delimitadas conservadoramente por frase, com relações muitos-para-muitos. Frases compostas recebem sinal de revisão humana: a versão inicial não garante decomposição atômica completa. Grupos de citações não implicam que cada obra apoie toda a frase. `apud` marca que a obra original não foi diretamente consultada; a avaliação deve respeitar a fonte intermediária e explicitar o alcance.

O parser é determinístico e não usa LLM para corrigir ambiguidades nesta versão. Correções humanas e evidências de fonte precisam ser tratadas pelo serviço proprietário, preservando versões.

## Fixtures e medições

Execute `python scripts/generate_fixtures.py` para produzir PDFs textual, escaneado, misto, protegido, corrompido e um relatório anotado de demonstração. Conteúdo sintético é dedicado ao domínio público por CC0-1.0. `annotations.json` contém as expectativas; `example-report-snapshot.json` registra o snapshot. A fonte e o DOI `10.0000/citalens.demo` são fictícios e nunca devem ser apresentados como pesquisa real.

`pytest tests/test_document_processing.py` verifica invariantes e cenários. O teste de OCR real é ignorado quando Tesseract está ausente; testes com OCR substituído medem apenas roteamento e tratamento de falhas. A geração grava `observed-metrics.json` e imprime contagens observadas, separando frases recuperadas, vínculos conhecidos e renderização. Na fixture sintética atual foram recuperadas três de três frases e três de três vínculos esperados; o relatório tem quatro páginas e nenhum bloco textual fora da página. Esses números se restringem à fixture e não estimam desempenho geral. Anotações semânticas do relatório de exemplo não são resultados de um modelo nem uma medição de classificação.

O relatório utiliza texto selecionável, fonte TrueType incorporada quando disponível, links HTTP(S), tabelas com cabeçalho repetido, contagens determinísticas e paginação. O Dockerfile instala DejaVu Sans. Conteúdo não confiável é escapado antes de entrar nos parágrafos ReportLab.

## APIs oficiais consultadas

- [PyMuPDF: Page, get_text, get_pixmap e get_label](https://pymupdf.readthedocs.io/en/latest/page.html).
- [ReportLab: Paragraph e links](https://docs.reportlab.com/reportlab/userguide/ch6_paragraphs/).

Versões adotadas: PyMuPDF 1.26.1, pytesseract 0.3.13, Pillow 11.2.1 e ReportLab 4.4.1. Tesseract é instalado pelo pacote Debian Bookworm da imagem. O parser não aplica regras inventadas de ABNT, APA ou Vancouver.
