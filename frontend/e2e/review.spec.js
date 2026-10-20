import { expect, test } from '@playwright/test';

// These are explicit API mocks for UI tests. They are never bundled with the application.
const document = { id: 'document-01', title: 'Manuscrito de teste.pdf', created_at: '2026-10-20T12:00:00Z' };
const reference = { id: 'reference-01', title: 'Efeito das intervenções no contexto estudado', authors: ['Silva, A.'], year: '2024', raw_text: 'SILVA, A. Efeito das intervenções no contexto estudado. 2024.', issues: [] };
const finding = {
  id: 'finding-01', page_start: 2, marker: '(Silva, 2024)', reference_id: reference.id, reference_text: reference.raw_text,
  claim_text: 'A intervenção melhorou o resultado no grupo estudado.', binding_status: 'RESOLVED', identity_status: 'MATCHED', access_status: 'FULL_TEXT',
  semantic_status: 'SUPPORTED', rationale: 'A passagem consultada relata melhora no mesmo grupo e condições.', severity: 'INFO',
  reviewed_scope: 'Resultados, página 3 da fonte.', limitations: ['Resultado restrito à população estudada.'], reviews: [],
};
const run = { id: 'analysis-01', document_id: document.id, status: 'COMPLETED', config: { mode: 'demo' }, created_at: document.created_at, warnings: ['Dados sintéticos para teste.'], reports: [{ id: 'report-01', version: 1, status: 'COMPLETED', file_id: 'file-01' }], summary: { inconclusive: 0 } };
const counts = { pages_total: 3, pages_processed: 3, references_total: 1, references_identified: 1, occurrences_total: 1, occurrences_resolved: 1, pairs_total: 1, pairs_evaluated: 1 };
const pdf = Buffer.from('%PDF-1.4\n1 0 obj <</Type/Catalog>> endobj\n%%EOF');
const envelope = (items) => ({ items, total: items.length, limit: 25, offset: 0 });

async function installApi(page, options = {}) {
  let uploaded = Boolean(options.deleting);
  let deletionState = options.deleting ? { deleting: true, deletion_error: options.deletionError || null } : null;
  let deletionComplete = false;
  const recorded = { analysis: null, reviews: [], source: null, headers: [], deletes: 0 };
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1', '');
    recorded.headers.push({ path, authorization: request.headers().authorization });
    let response;
    if (path.startsWith('/auth/')) response = { access_token: 'test-token-never-used-in-production', user: { name: 'Ana Pesquisadora', email: 'ana@example.org' } };
    else if (path === '/documents' && request.method() === 'POST') { uploaded = true; response = document; }
    else if (path === '/documents') response = envelope(uploaded && !deletionComplete ? [{ ...document, ...deletionState }] : []);
    else if (path === `/documents/${document.id}` && request.method() === 'DELETE') {
      recorded.deletes += 1;
      deletionState = { deleting: true, deletion_error: null };
      if (options.completeDeletionAfterRetry) setTimeout(() => { deletionComplete = true; }, 100);
      response = { id: document.id, status: 'DELETING' };
    }
    else if (path === `/documents/${document.id}/analyses` && request.method() === 'POST') { recorded.analysis = request.postDataJSON(); response = run; }
    else if (path === `/documents/${document.id}/analyses`) response = envelope([run]);
    else if (path === `/analyses/${run.id}`) response = run;
    else if (path.endsWith('/progress')) response = { ...counts, status: 'COMPLETED', stage: 'COMPLETED', steps: [] };
    else if (path.endsWith('/references')) response = envelope([reference]);
    else if (path.endsWith('/pages')) response = envelope([{ id: 'page-01', physical_page_number: 2, printed_label: 'ii', extraction_method: 'native', status: 'PROCESSED' }]);
    else if (path.endsWith('/citations')) response = envelope([{ id: 'citation-01', marker: '(Silva, 2024)', binding_status: 'AMBIGUOUS', page_start: 2, context: 'A intervenção melhorou o resultado no grupo estudado.', reviews: [] }]);
    else if (path.endsWith('/findings')) response = envelope(url.searchParams.get('status') === 'CONTRADICTED' ? [] : [{ ...finding, ...options.finding }]);
    else if (path.endsWith('/evidence')) response = envelope([{ id: 'evidence-01', text: 'O grupo estudado apresentou melhora no resultado.', quote: 'O grupo estudado apresentou melhora no resultado.', page_start: 3, page_end: 3, source_id: 'source-01', source_file_available: true, origin_url: 'https://example.org/source', source_version: '1' }]);
    else if (path.endsWith('/review')) { recorded.reviews.push(request.postDataJSON()); response = { id: 'review-01' }; }
    else if (path.endsWith('/sources')) { recorded.source = request.postData(); response = { id: 'source-01', requires_new_analysis: true }; }
    else if (path.endsWith('/download') || path.endsWith('/file') || path === '/demo/manuscript') return route.fulfill({ contentType: 'application/pdf', body: pdf });
    else return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ message: `Unexpected test route ${path}` }) });
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(response) });
  });
  return recorded;
}

async function login(page) {
  await page.goto('/');
  await page.getByLabel('E-mail', { exact: true }).fill('ana@example.org');
  await page.getByLabel('Senha', { exact: true }).fill('test-password-123');
  await page.getByRole('button', { name: 'Entrar no CitaLens' }).click();
  await expect(page.getByRole('heading', { name: 'Sua biblioteca de revisões' })).toBeVisible();
}

async function uploadDemo(page) {
  await page.getByRole('button', { name: 'Enviar meu primeiro PDF' }).click();
  await page.getByLabel('Selecionar manuscrito PDF').setInputFiles({ name: 'Manuscrito de teste.pdf', mimeType: 'application/pdf', buffer: pdf });
  await page.getByRole('radio', { name: /Demonstração/ }).check();
  await page.getByRole('button', { name: 'Iniciar análise', exact: true }).click();
  await expect(page.getByText('Fontes e avaliações sintéticas · Sem pesquisa web real')).toBeVisible();
}

test('fluxo de revisão: autenticação, upload explícito demo, evidência, revisão humana e download', async ({ page }, testInfo) => {
  const recorded = await installApi(page);
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await login(page);
  await page.screenshot({ path: testInfo.outputPath('biblioteca-vazia.png'), fullPage: true });
  await uploadDemo(page);
  expect(recorded.analysis).toEqual({ mode: 'demo', citation_style: 'unspecified', language: 'pt', bibliography_page_start: null, bibliography_page_end: null });
  await expect(page.getByText('Páginas processadas')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('visao-geral.png'), fullPage: true });
  await page.getByRole('button', { name: 'Achados', exact: true }).click();
  await page.getByRole('button', { name: /Examinar evidências/ }).click();
  await expect(page.getByText('O grupo estudado apresentou melhora no resultado.', { exact: true })).toBeVisible();
  await expect(page.getByText('Resultados, página 3 da fonte.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Registrar minha revisão' }).click();
  await page.getByLabel('Sua avaliação').selectOption('AGREE');
  await page.getByLabel('Comentário', { exact: true }).fill('Conferi a passagem e as condições do estudo.');
  await page.getByRole('button', { name: 'Salvar revisão humana' }).click();
  await expect(page.getByText(/Revisão registrada: Concordo/)).toBeVisible();
  expect(recorded.reviews).toEqual([{ decision: 'AGREE', comment: 'Conferi a passagem e as condições do estudo.' }]);
  await page.screenshot({ path: testInfo.outputPath('comparacao-evidencia.png'), fullPage: true });
  await page.getByRole('button', { name: 'Fechar', exact: true }).click();
  await page.getByRole('combobox', { name: 'Resultado', exact: true }).selectOption('CONTRADICTED');
  await expect(page.getByRole('heading', { name: 'Nenhum achado com estes filtros' })).toBeVisible();
  await page.getByRole('button', { name: 'Limpar', exact: true }).click();
  await expect(page.getByRole('button', { name: /Examinar evidências/ })).toBeVisible();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Baixar relatório' }).click();
  const report = await downloadPromise;
  expect(report.suggestedFilename()).toContain('citalens-relatorio-');
  expect(recorded.headers.filter((entry) => !entry.path.startsWith('/auth/')).every((entry) => entry.authorization === 'Bearer test-token-never-used-in-production')).toBeTruthy();
  expect(errors).toEqual([]);
});

test('telas acessíveis em dispositivo móvel e validação de arquivo', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installApi(page);
  await page.goto('/');
  await page.screenshot({ path: testInfo.outputPath('login-mobile.png'), fullPage: true });
  await login(page);
  await page.getByRole('button', { name: 'Abrir menu' }).click();
  await expect(page.getByRole('button', { name: 'Todos os documentos' })).toBeVisible();
  await page.getByRole('button', { name: 'Fechar menu' }).click();
  await page.getByRole('button', { name: 'Enviar meu primeiro PDF' }).click();
  await page.getByLabel('Selecionar manuscrito PDF').setInputFiles({ name: 'texto.txt', mimeType: 'text/plain', buffer: Buffer.from('não é pdf') });
  await expect(page.getByRole('alert')).toContainText('Selecione um arquivo em formato PDF.');
  await page.getByLabel('Selecionar manuscrito PDF').setInputFiles({ name: 'teste.pdf', mimeType: 'application/pdf', buffer: pdf });
  await page.getByRole('radio', { name: /Demonstração/ }).check();
  await page.getByRole('button', { name: 'Iniciar análise', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Achados', exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('analise-mobile.png'), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});

test('expiração de sessão remove dados e volta ao login', async ({ page }) => {
  await installApi(page);
  await login(page);
  await page.unroute('**/api/v1/**');
  await page.route('**/api/v1/**', (route) => route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ message: 'Sessão expirada' }) }));
  await page.getByRole('button', { name: 'Atualizar', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Entrar no CitaLens' })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem('citalens-session'))).toBeNull();
});

test('correção de vínculo e fonte complementar preservam a análise e pedem nova execução', async ({ page }) => {
  const recorded = await installApi(page);
  await login(page);
  await uploadDemo(page);
  await page.getByRole('button', { name: /Referências 1/ }).click();
  await page.getByRole('button', { name: 'Revisar vínculo' }).click();
  await page.getByRole('combobox', { name: 'Referência correta' }).selectOption(reference.id);
  await page.getByLabel('Comentário', { exact: true }).fill('A autoria e o contexto identificam esta entrada.');
  await page.getByRole('button', { name: 'Registrar correção' }).click();
  await expect(page.getByText('Correção humana registrada. Inicie uma nova análise para aplicá-la; o vínculo original foi preservado.')).toBeVisible();
  expect(recorded.reviews[0]).toEqual({ reference_id: reference.id, comment: 'A autoria e o contexto identificam esta entrada.' });
  await page.getByRole('button', { name: 'Anexar fonte', exact: true }).first().click();
  await page.getByRole('combobox', { name: 'Referência associada' }).selectOption(reference.id);
  await page.getByLabel('PDF da fonte').setInputFiles({ name: 'fonte.pdf', mimeType: 'application/pdf', buffer: pdf });
  await page.getByLabel('Edição / versão').fill('Versão publicada, 2024');
  await page.getByRole('checkbox', { name: /Conferi autoria/ }).check();
  await page.getByRole('dialog').getByRole('button', { name: 'Anexar fonte', exact: true }).click();
  await expect(page.getByText('Fonte salva. Inicie uma nova análise deste documento para utilizá-la; a análise atual foi preservada.')).toBeVisible();
  expect(recorded.source).toContain('name="identity_confirmed"');
  expect(recorded.source).toContain('true');
  expect(recorded.source).toContain(reference.id);
});

test('exclusão pendente fica visível, bloqueia abertura e permite repetir falha até conclusão', async ({ page }) => {
  const recorded = await installApi(page, { deleting: true, deletionError: 'Falha temporária ao remover os artefatos.', completeDeletionAfterRetry: true });
  await login(page);
  await expect(page.getByText('Exclusão não concluída', { exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Falha temporária ao remover os artefatos.');
  await expect(page.locator('.document-row')).toBeDisabled();
  await expect(page.getByRole('navigation', { name: 'Documentos' }).getByRole('button', { name: document.title })).toBeDisabled();
  await page.getByRole('button', { name: 'Tentar excluir novamente' }).click();
  await expect(page.getByText('Exclusão em andamento', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tentar excluir novamente' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Sua próxima leitura começa aqui' })).toBeVisible({ timeout: 10000 });
  expect(recorded.deletes).toBe(1);
  expect(recorded.headers.some((entry) => entry.path.endsWith('/analyses'))).toBeFalsy();
});

test('modal apresenta escopo real em linguagem legível em vez de JSON', async ({ page }) => {
  await installApi(page, { finding: { reviewed_scope: JSON.stringify({
    reviewed_pages: [1, 2, 3, 4, 5, 6, 7], reviewed_chunks: 35, available_chunks: 35,
    all_available_content: true, expanded_search: true, source_id: 'internal-source-id', work_id: 'internal-work-id',
  }) } });
  await login(page);
  await uploadDemo(page);
  await page.getByRole('button', { name: 'Achados', exact: true }).click();
  await page.getByRole('button', { name: /Examinar evidências/ }).click();
  const scope = page.getByRole('dialog').locator('.assessment-meta > div').first();
  await expect(scope).toContainText('Páginas consultadas: 1–7.');
  await expect(scope).toContainText('Parágrafos consultados: 35 de 35 disponíveis.');
  await expect(scope).toContainText('não à obra inteira.');
  await expect(scope).not.toContainText('source_id');
  await expect(scope).not.toContainText('internal-source-id');
  await expect(scope).not.toContainText('expanded_search');
});
