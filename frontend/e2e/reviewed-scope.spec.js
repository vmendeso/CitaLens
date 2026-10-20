import { expect, test } from '@playwright/test';
import { formatReviewedScope } from '../src/reviewedScope.js';

test('escopo estruturado resume páginas e parágrafos sem expor identificadores', () => {
  const scope = {
    reviewed_pages: [7, 1, 2, 3, 4, 5, 6, 3], reviewed_chunks: 35, available_chunks: 35,
    all_available_content: true, expanded_search: true, source_id: 'source-internal-id', work_id: 'work-internal-id', version: '1',
    note: 'Cobertura refere-se somente ao texto persistido desta fonte, não à integralidade da obra.',
  };
  const expected = 'Páginas consultadas: 1–7. Parágrafos consultados: 35 de 35 disponíveis. Cobertura refere-se somente ao texto disponível desta fonte, não à integralidade da obra.';
  expect(formatReviewedScope(scope)).toBe(expected);
  expect(formatReviewedScope(JSON.stringify(scope))).toBe(expected);
});

test('escopo parcial preserva lacunas nas páginas e explicita limite de cobertura', () => {
  const result = formatReviewedScope({ reviewed_pages: [1, 2, 4, 7, 8, -1, null, 'source-id'], reviewed_chunks: 5, available_chunks: 20, all_available_content: false });
  expect(result).toContain('Páginas consultadas: 1–2, 4, 7–8.');
  expect(result).toContain('Parágrafos consultados: 5 de 20 disponíveis.');
  expect(result).toContain('A consulta abrange apenas parte do texto disponível.');
  expect(result).toContain('não à obra inteira.');
});

test('escopo em texto permanece literal e dados desconhecidos não viram JSON visível', () => {
  const text = 'Resultados, página 3 da fonte.';
  expect(formatReviewedScope(text)).toBe(text);
  expect(formatReviewedScope({ reviewed_pages: [3], reviewed_chunks: 0, available_chunks: 0 })).toContain('Página consultada: 3. Parágrafos consultados: 0 de 0 disponíveis.');
  for (const scope of [null, '', {}, { source_id: 'internal-id' }, '[]']) {
    expect(formatReviewedScope(scope)).toBe('Escopo não informado; não extrapole esta avaliação para a obra inteira.');
  }
});
