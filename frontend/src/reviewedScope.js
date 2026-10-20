const UNKNOWN_SCOPE = 'Escopo não informado; não extrapole esta avaliação para a obra inteira.';

function pageRanges(pages) {
  const ordered = [...new Set(pages.filter((page) => Number.isInteger(page) && page > 0))].sort((a, b) => a - b);
  const ranges = [];
  for (let index = 0; index < ordered.length; index += 1) {
    const first = ordered[index];
    let last = first;
    while (ordered[index + 1] === last + 1) last = ordered[++index];
    ranges.push(first === last ? `${first}` : `${first}–${last}`);
  }
  return { count: ordered.length, text: ranges.join(', ') };
}

export function formatReviewedScope(value) {
  let scope = value;
  if (typeof scope === 'string') {
    if (!scope.trim()) return UNKNOWN_SCOPE;
    try { scope = JSON.parse(scope); } catch { return value; }
    if (typeof scope === 'string') return scope || UNKNOWN_SCOPE;
  }
  if (!scope || typeof scope !== 'object' || Array.isArray(scope)) return UNKNOWN_SCOPE;

  const sentences = [];
  const pages = pageRanges(Array.isArray(scope.reviewed_pages) ? scope.reviewed_pages : []);
  if (pages.count) sentences.push(`${pages.count === 1 ? 'Página consultada' : 'Páginas consultadas'}: ${pages.text}.`);

  const reviewed = Number.isInteger(scope.reviewed_chunks) && scope.reviewed_chunks >= 0 ? scope.reviewed_chunks : null;
  const available = Number.isInteger(scope.available_chunks) && scope.available_chunks >= 0 ? scope.available_chunks : null;
  if (reviewed !== null) {
    sentences.push(available !== null
      ? `Parágrafos consultados: ${reviewed} de ${available} disponíveis.`
      : `Parágrafos consultados: ${reviewed}.`);
  }

  if (scope.all_available_content === false || (reviewed !== null && available !== null && reviewed < available)) {
    sentences.push('A consulta abrange apenas parte do texto disponível.');
  }
  const note = typeof scope.note === 'string' ? scope.note.trim() : '';
  if (note) sentences.push(note.replace(/texto persistido/gi, 'texto disponível'));
  else if (sentences.length || typeof scope.all_available_content === 'boolean') {
    sentences.push('A cobertura se refere ao texto disponível desta fonte, não à obra inteira.');
  }
  return sentences.join(' ') || UNKNOWN_SCOPE;
}
