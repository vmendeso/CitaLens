import React, { useEffect, useState } from 'react';
import { api, download } from './api.js';
import { Badge, Empty, Icon, Modal, Notice, Pagination, Spinner, asText, formatDate, isTerminal, label } from './components.jsx';
import { useAnalysis, useCollection, useReferenceOptions } from './hooks.js';
import Findings from './Findings.jsx';

const STAGES = [
  { key: 'INGESTING', title: 'Preparar documento', description: 'Texto nativo e OCR quando necessário.' },
  { key: 'EXTRACTING_REFERENCES', title: 'Catalogar referências', description: 'A bibliografia é o ponto de partida.' },
  { key: 'MAPPING_CITATIONS', title: 'Mapear todas as páginas', description: 'Marcadores, afirmações e vínculos.' },
  { key: 'RESOLVING_SOURCES', title: 'Identificar e consultar fontes', description: 'Identidade e disponibilidade registradas.' },
  { key: 'EVALUATING', title: 'Examinar as evidências', description: 'Uma avaliação por afirmação e obra.' },
  { key: 'GENERATING_REPORT', title: 'Preparar relatório', description: 'Resultados, escopo e limitações.' },
];
const TABS = [{ id: 'overview', label: 'Visão geral', icon: 'grid' }, { id: 'references', label: 'Referências', icon: 'book' }, { id: 'pages', label: 'Páginas', icon: 'file' }, { id: 'findings', label: 'Achados', icon: 'search' }];

export default function Analysis({ id, document, notify }) {
  const [tab, setTab] = useState('overview');
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [sourceModal, setSourceModal] = useState(false);
  const [selectedReference, setSelectedReference] = useState('');
  const { run, progress, error, loading } = useAnalysis(id, refresh);
  const references = useCollection(`/analyses/${id}/references`, {}, `${run?.status}-${refresh}`);
  const referenceOptions = useReferenceOptions(id, `${run?.status}-${refresh}`);
  const latestReport = [...(run?.reports || [])].filter((report) => report.file_id || ['COMPLETED', 'READY'].includes(report.status)).sort((a, b) => b.version - a.version)[0];
  async function action(path, success) {
    setBusy(true);
    try { await api(path, { method: 'POST' }); notify(success); setRefresh((value) => value + 1); } catch (failure) { notify(failure.message); } finally { setBusy(false); }
  }
  if (loading && !run) return <Spinner>Carregando análise…</Spinner>;
  if (!run) return <Notice error>{error || 'Análise não encontrada.'}</Notice>;
  return <div className="analysis">
    {run.config?.mode === 'demo' && <div className="demo-banner"><span className="demo-pill">DEMONSTRAÇÃO</span><span>Fontes e avaliações sintéticas · Sem pesquisa web real</span></div>}
    {error && <Notice error>{error} A consulta será repetida automaticamente.</Notice>}
    <div className="analysis-status"><div><Badge value={run.status} /><span className="analysis-id">Análise {id.slice(0, 8)} <span>·</span> {formatDate(run.created_at)}</span></div><div className="analysis-actions">{!isTerminal(run.status) && <button className="text-button muted" disabled={busy || progress?.cancel_requested} onClick={() => action(`/analyses/${id}/cancel`, 'Cancelamento solicitado. As etapas em execução serão interrompidas nos checkpoints.')}>{progress?.cancel_requested ? 'Cancelamento solicitado' : 'Cancelar análise'}</button>}<button className="button secondary small" disabled={!isTerminal(run.status)} onClick={() => setSourceModal(true)}><Icon name="upload" size={15} />Anexar fonte</button>{latestReport && <button className="button primary small" disabled={busy} onClick={() => { setBusy(true); download(`/reports/${latestReport.id}/download`, `citalens-relatorio-${id.slice(0, 8)}-v${latestReport.version}.pdf`).catch((failure) => notify(failure.message)).finally(() => setBusy(false)); }}><Icon name="download" size={16} />Baixar relatório</button>}</div></div>
    <nav className="tabs" aria-label="Seções da análise">{TABS.map((item) => <button key={item.id} className={tab === item.id ? 'active' : ''} aria-current={tab === item.id ? 'page' : undefined} onClick={() => setTab(item.id)}><Icon name={item.icon} size={17} />{item.label}{item.id === 'references' && references.total > 0 && <span>{references.total}</span>}</button>)}</nav>
    {tab === 'overview' && <Overview run={run} progress={progress} onFindings={() => setTab('findings')} onReport={() => action(`/analyses/${id}/reports`, 'Uma nova versão do relatório foi solicitada.')} busy={busy} />}
    {referenceOptions.error && <Notice error>Não foi possível carregar todas as opções de referência: {referenceOptions.error}</Notice>}
    {tab === 'references' && <References data={references} options={referenceOptions.items} analysisId={id} canAttach={isTerminal(run.status)} onAttach={(refId) => { setSelectedReference(refId); setSourceModal(true); }} notify={notify} />}
    {tab === 'pages' && <Pages id={id} refresh={run.status} document={document} notify={notify} />}
    {tab === 'findings' && <Findings id={id} document={document} references={referenceOptions.items} refresh={run.status} notify={notify} />}
    {sourceModal && <Modal title="Anexar fonte complementar" onClose={() => setSourceModal(false)}><AttachSource analysisId={id} references={referenceOptions.items} selectedReference={selectedReference} onClose={() => setSourceModal(false)} onSaved={() => { setSourceModal(false); notify('Fonte salva. Inicie uma nova análise deste documento para utilizá-la; a análise atual foi preservada.'); }} /></Modal>}
  </div>;
}

function Overview({ run, progress, onFindings, onReport, busy }) {
  const counters = [
    { title: 'Páginas processadas', numerator: progress?.pages_processed, denominator: progress?.pages_total, note: 'cobertura do arquivo', icon: 'file' },
    { title: 'Referências identificadas', numerator: progress?.references_identified, denominator: progress?.references_total, note: 'identificadas / extraídas', icon: 'book' },
    { title: 'Ocorrências vinculadas', numerator: progress?.occurrences_resolved, denominator: progress?.occurrences_total, note: 'vinculadas / detectadas', icon: 'link' },
    { title: 'Pares avaliados', numerator: progress?.pairs_evaluated, denominator: progress?.pairs_total, note: 'avaliados / identificados', icon: 'search' },
  ];
  const current = STAGES.findIndex((item) => item.key === (progress?.stage || run.status));
  const completed = ['COMPLETED', 'COMPLETED_WITH_WARNINGS'].includes(run.status);
  const summary = run.summary || {};
  const semantic = summary.semantic_statuses || summary.semantic_counts || summary.by_semantic_status || {};
  return <>
    {summary.inconclusive !== undefined && <div className="inconclusive-summary"><Icon name="search" size={16} /><strong>{summary.inconclusive}</strong> pares inconclusivos ou ainda não avaliados <span>Os limites da evidência permanecem visíveis.</span></div>}
    <div className="stats-grid">{counters.map((counter) => <section className="stat-card" key={counter.title}><div className="stat-title">{counter.title}<Icon name={counter.icon} size={17} /></div><div className="stat-value">{counter.numerator ?? '—'}<span>/ {counter.denominator ?? '—'}</span></div><div className="stat-footer"><span>{counter.note}</span><div className="tiny-progress"><i style={{ width: `${counter.denominator ? Math.min(100, (counter.numerator || 0) / counter.denominator * 100) : 0}%` }} /></div></div></section>)}</div>
    <div className="overview-grid"><section className="panel workflow-panel"><div className="section-heading"><div><span className="eyebrow">PROCESSO RASTREÁVEL</span><h2>Uma análise, etapa por etapa</h2></div><Icon name="clock" size={20} /></div><ol className="timeline">{STAGES.map((stage, index) => {
      const persisted = (progress?.steps || []).find((step) => step.name === stage.key || step.stage === stage.key);
      const done = completed || persisted?.status === 'COMPLETED' || (current > index && !['FAILED', 'CANCELLED'].includes(run.status));
      const active = current === index && !isTerminal(run.status);
      return <li className={`${done ? 'done' : ''} ${active ? 'current' : ''}`} key={stage.key}><span className="timeline-dot">{done ? <Icon name="check" size={14} /> : String(index + 1).padStart(2, '0')}</span><div><strong>{stage.title}</strong><p>{stage.description}</p></div>{active && <span className="active-step">Em andamento</span>}</li>;
    })}</ol>{run.error && <Notice error>{asText(run.error)}</Notice>}{run.status === 'CANCELLED' && <Notice>A análise foi cancelada. Os resultados já persistidos permanecem disponíveis.</Notice>}</section>
      <section className="panel outcome-panel"><div className="section-heading"><div><span className="eyebrow">LEITURA DOS RESULTADOS</span><h2>O contexto vem primeiro</h2></div></div><p className="muted">Cada parecer separa quatro dimensões. Uma identidade confirmada não significa que a afirmação esteja apoiada.</p><div className="dimension-list"><div><span>01</span><p><strong>Vínculo bibliográfico</strong>Qual referência corresponde ao marcador?</p></div><div><span>02</span><p><strong>Identidade da obra</strong>A fonte encontrada é a obra citada?</p></div><div><span>03</span><p><strong>Acesso ao conteúdo</strong>Qual parte da fonte foi consultada?</p></div><div><span>04</span><p><strong>Apoio à afirmação</strong>O conteúdo sustenta o que foi escrito?</p></div></div>{Object.keys(semantic).length > 0 && <div className="summary-counts">{Object.entries(semantic).map(([status, count]) => <div key={status}><Badge value={status} /><strong>{count}</strong></div>)}</div>}<button className="button secondary full" onClick={onFindings}>Examinar os achados<Icon name="arrow" size={17} /></button></section></div>
    {(run.warnings || []).length > 0 && <section className="panel limitations-panel"><div className="section-heading"><h2><Icon name="alert" size={19} />Limitações e ressalvas</h2></div><ul className="plain-list">{run.warnings.map((warning, index) => <li key={index}>{asText(warning)}</li>)}</ul></section>}
    <section className="report-callout"><span className="report-icon"><Icon name="file" size={29} /></span><div><h3>Uma leitura que pode ser compartilhada</h3><p>O relatório reúne evidências, cobertura e limitações em um snapshot versionado.</p></div><button className="button secondary" onClick={onReport} disabled={busy || !isTerminal(run.status)}><Icon name="download" size={17} />Gerar nova versão</button></section>
    <p className="workspace-footnote">A análise apoia a revisão. Não certifica veracidade, ausência de plágio ou conformidade normativa integral.</p>
  </>;
}

function References({ data, options, analysisId, onAttach, canAttach, notify }) {
  const citations = useCollection(`/analyses/${analysisId}/citations`, { status: 'AMBIGUOUS' });
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function review(event) {
    event.preventDefault(); setBusy(true); setError('');
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    try { await api(`/citations/${editing.id}/review`, { method: 'POST', body: fields }); setEditing(null); citations.reload(); notify('Correção humana registrada. Inicie uma nova análise para aplicá-la; o vínculo original foi preservado.'); } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  }
  return <>
    <section className="panel"><div className="section-heading"><div><h2>Catálogo de referências <span className="count">{data.total}</span></h2><p>Entradas originais preservadas, inclusive duplicidades.</p></div></div>{data.loading ? <Spinner /> : data.error ? <Notice error>{data.error}</Notice> : data.items.length ? <><div className="reference-list">{data.items.map((reference, index) => <article className="reference-card" key={reference.id}><span className="reference-number">{String(data.offset + index + 1).padStart(2, '0')}</span><div><h3>{reference.title || 'Referência em identificação'}</h3><p className="reference-meta">{asText(reference.authors) || 'Autoria não identificada'}{reference.year ? ` · ${reference.year}` : ''}</p><blockquote>{reference.raw_text}</blockquote>{(reference.issues || []).length > 0 && <p className="reference-issues"><Icon name="alert" size={15} />{asText(reference.issues)}</p>}<div className="reference-footer"><span className="subtle-id">Ref. {reference.id.slice(0, 8)}</span><button className="text-button" disabled={!canAttach} onClick={() => onAttach(reference.id)}><Icon name="upload" size={15} />Anexar fonte</button></div></div></article>)}</div><Pagination {...data} onChange={data.setOffset} /></> : <Empty title="O catálogo está sendo preparado" icon="book">As referências aparecem aqui após a etapa de catalogação. Se não forem localizadas, indique manualmente as páginas da bibliografia em uma nova análise.</Empty>}</section>
    <section className="panel"><div className="section-heading"><div><h2>Vínculos que precisam de leitura humana</h2><p>Corrija uma associação ambígua sem substituir o registro original.</p></div></div>{citations.loading ? <Spinner /> : citations.error ? <Notice error>{citations.error}</Notice> : citations.items.length ? <>{citations.items.map((citation) => <article className="citation-row" key={citation.id}><div><Badge value={citation.binding_status || citation.status || 'AMBIGUOUS'} /><strong>{citation.marker}</strong><p>{citation.context || citation.claim_text || citation.raw_text}</p><small>Página {citation.page_start || citation.physical_page_number || '—'}</small></div><button className="button secondary small" onClick={() => { setEditing(citation); setError(''); }}>Revisar vínculo</button></article>)}<Pagination {...citations} onChange={citations.setOffset} /></> : <p className="quiet-empty">Nenhum vínculo ambíguo retornado nesta análise.</p>}</section>
    {editing && <Modal title="Revisar vínculo bibliográfico" onClose={() => setEditing(null)}><form onSubmit={review}><blockquote className="claim-block">{editing.context || editing.claim_text || editing.marker}</blockquote><label>Referência correta<select name="reference_id" required defaultValue=""><option value="" disabled>Selecione uma referência</option>{options.map((reference) => <option key={reference.id} value={reference.id}>{reference.title || reference.raw_text}</option>)}</select></label><label>Comentário<textarea name="comment" required minLength={3} maxLength={4000} rows={4} placeholder="Explique por que esta é a referência correspondente." /></label><Notice>A correção é registrada como revisão humana. O vínculo e o parecer originais permanecem no histórico. Inicie uma nova análise para aplicar a correção.</Notice>{error && <Notice error>{error}</Notice>}<div className="modal-actions"><button className="button secondary" type="button" onClick={() => setEditing(null)}>Voltar</button><button className="button primary" disabled={busy}>{busy ? 'Salvando…' : 'Registrar correção'}</button></div></form></Modal>}
  </>;
}

function Pages({ id, refresh, document, notify }) {
  const data = useCollection(`/analyses/${id}/pages`, {}, refresh);
  const [pdf, setPdf] = useState(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  useEffect(() => () => { if (pdf) URL.revokeObjectURL(pdf); }, [pdf]);
  async function view(number) {
    setPage(number);
    if (pdf) return;
    setBusy(true);
    try { const blob = await api(`/documents/${document.id}/file`, { blob: true }); setPdf(URL.createObjectURL(blob)); } catch (error) { notify(error.message); } finally { setBusy(false); }
  }
  return <section className="panel"><div className="section-heading"><div><h2>Cobertura página a página <span className="count">{data.total}</span></h2><p>A numeração do arquivo começa em 1; o rótulo impresso é mostrado separadamente.</p></div></div>{data.loading ? <Spinner /> : data.error ? <Notice error>{data.error}</Notice> : data.items.length ? <><div className="table-scroll"><table><thead><tr><th>Página do arquivo</th><th>Rótulo impresso</th><th>Extração</th><th>Estado</th><th>Observações</th><th /></tr></thead><tbody>{data.items.map((item) => <tr key={item.id || item.physical_page_number}><td><strong>{item.physical_page_number}</strong></td><td>{item.printed_label || 'Não identificado'}</td><td>{label(item.extraction_method || item.method)}</td><td><Badge value={item.status} /></td><td>{item.error || item.quality_warning || '—'}</td><td><button className="text-button" disabled={busy} onClick={() => view(item.physical_page_number)}>Ver página</button></td></tr>)}</tbody></table></div><Pagination {...data} onChange={data.setOffset} /></> : <Empty title="Nenhuma página processada ainda" icon="file">A extração registra todas as páginas, inclusive aquelas sem citações ou com falhas de leitura.</Empty>}{pdf && <div className="pdf-inline"><div className="section-heading"><strong>Página {page} do manuscrito</strong><button className="text-button" onClick={() => setPdf(null)}>Fechar visualizador</button></div><iframe key={page} src={`${pdf}#page=${page}&view=FitH`} title={`Manuscrito, página ${page}`} /><p className="field-help">Navegação por página; a localização do trecho é aproximada quando não há coordenadas confiáveis.</p></div>}</section>;
}

function AttachSource({ analysisId, references, selectedReference, onSaved, onClose }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const body = new FormData(event.currentTarget);
      const file = body.get('file');
      if (!file?.size || !file.name.toLowerCase().endsWith('.pdf')) throw new Error('Selecione um PDF da fonte.');
      if (file.size > 20 * 1024 * 1024) throw new Error('A fonte excede o limite de 20 MB.');
      body.set('identity_confirmed', body.get('identity_confirmed') ? 'true' : 'false');
      await api(`/analyses/${analysisId}/sources`, { method: 'POST', body }); onSaved();
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  }
  return <form onSubmit={submit}><p className="muted">Disponibilize o PDF da obra citada, com a edição e versão correspondentes. A fonte será aplicada em uma nova análise.</p><label>Referência associada<select name="reference_id" required defaultValue={selectedReference || ''}><option value="" disabled>Selecione a referência</option>{references.map((reference) => <option key={reference.id} value={reference.id}>{reference.title || reference.raw_text}</option>)}</select></label><label>PDF da fonte<input name="file" type="file" accept=".pdf,application/pdf" required /></label><label>Edição / versão<input name="version" required maxLength={100} placeholder="Ex.: versão publicada, 2ª edição, 2024" /></label><label className="checkbox-label"><input name="identity_confirmed" type="checkbox" required /><span>Conferi autoria, título e edição. Confirmo que este arquivo corresponde à referência selecionada.</span></label><Notice>A identidade será registrada como confirmada por você. Essa confirmação não significa que o conteúdo apoie a afirmação.</Notice>{error && <Notice error>{error}</Notice>}<div className="modal-actions"><button className="button secondary" type="button" onClick={onClose} disabled={busy}>Voltar</button><button className="button primary" disabled={busy}>{busy ? 'Enviando…' : 'Anexar fonte'}</button></div></form>;
}
