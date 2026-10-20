import React, { useEffect, useState } from 'react';
import { api, safeUrl } from './api.js';
import { Badge, Empty, Icon, Modal, Notice, Pagination, Spinner, asText, label } from './components.jsx';
import { useCollection } from './hooks.js';
import { formatReviewedScope } from './reviewedScope.js';

const SEMANTIC_STATES = ['SUPPORTED', 'PARTIALLY_SUPPORTED', 'CONTRADICTED', 'NOT_SUPPORTED_BY_REVIEWED_CONTENT', 'INCONCLUSIVE', 'NOT_EVALUATED'];

export default function Findings({ id, document, references, refresh, notify }) {
  const [filters, setFilters] = useState({ page: '', reference_id: '', severity: '', status: '' });
  const data = useCollection(`/analyses/${id}/findings`, filters, refresh);
  const [selected, setSelected] = useState(null);
  function filter(name, value) { setFilters((current) => ({ ...current, [name]: value })); }
  return <>
    <section className="panel findings-panel"><div className="section-heading"><div><h2>Afirmações sob análise <span className="count">{data.total}</span></h2><p>Uma mesma obra pode apoiar uma afirmação e não apoiar outra.</p></div></div>
      <form className="filters" onSubmit={(event) => event.preventDefault()}><label>Página<input type="number" min="1" placeholder="Todas" value={filters.page} onChange={(event) => filter('page', event.target.value)} /></label><label>Referência<select value={filters.reference_id} onChange={(event) => filter('reference_id', event.target.value)}><option value="">Todas as referências</option>{references.map((reference) => <option key={reference.id} value={reference.id}>{reference.title || reference.raw_text}</option>)}</select></label><label>Gravidade<select value={filters.severity} onChange={(event) => filter('severity', event.target.value)}><option value="">Todas</option>{['HIGH', 'MEDIUM', 'LOW', 'INFO'].map((value) => <option key={value} value={value}>{label(value)}</option>)}</select></label><label>Resultado<select value={filters.status} onChange={(event) => filter('status', event.target.value)}><option value="">Todos os resultados</option>{SEMANTIC_STATES.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select></label>{Object.values(filters).some(Boolean) && <button className="text-button clear-filters" type="button" onClick={() => setFilters({ page: '', reference_id: '', severity: '', status: '' })}>Limpar</button>}</form>
      {data.loading ? <Spinner /> : data.error ? <Notice error>{data.error}</Notice> : data.items.length ? <><div className="finding-list">{data.items.map((finding) => <button key={finding.id} className={`finding-card severity-${finding.severity?.toLowerCase()}`} onClick={() => setSelected(finding)}><div className="finding-card-top"><span className="page-label">PÁG. {finding.page_start || finding.physical_page_number || '—'} {finding.printed_label ? `· IMPRESSA ${finding.printed_label}` : ''}</span><Badge value={finding.semantic_status} /></div><p className="finding-claim">“{finding.claim_text}”</p><div className="finding-reference"><Icon name="book" size={16} /><span>{finding.reference_text || finding.reference_title || finding.marker || 'Referência não identificada'}</span></div><p className="finding-rationale">{finding.rationale || 'Parecer ainda não disponível.'}</p><div className="finding-card-bottom"><span className="severity-label"><span />Gravidade {label(finding.severity).toLowerCase()}</span><span className="finding-open">Examinar evidências<Icon name="arrow" size={16} /></span></div></button>)}</div><Pagination {...data} onChange={data.setOffset} /></> : <Empty title={Object.values(filters).some(Boolean) ? 'Nenhum achado com estes filtros' : 'Os achados aparecerão aqui'}>Os pareceres são disponibilizados após o mapeamento das citações e a consulta das fontes. Resultados inconclusivos também serão apresentados.</Empty>}
    </section>
    {selected && <Modal title="Afirmação, fonte e evidência" wide onClose={() => setSelected(null)}><FindingDetail finding={selected} document={document} notify={notify} /></Modal>}
  </>;
}

function FindingDetail({ finding, document, notify }) {
  const data = useCollection(`/findings/${finding.id}/evidence`);
  const [reviewing, setReviewing] = useState(false);
  const [review, setReview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [reviewError, setReviewError] = useState('');
  const [pdf, setPdf] = useState(null);
  const [pdfType, setPdfType] = useState('');
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState('');
  const [pdfPage, setPdfPage] = useState(1);
  useEffect(() => () => { if (pdf) URL.revokeObjectURL(pdf); }, [pdf]);
  async function openPdf(type, sourceId, page) {
    setPdfBusy(true); setPdfError('');
    try {
      const blob = await api(type === 'manuscript' ? `/documents/${document.id}/file` : `/sources/${sourceId}/file`, { blob: true });
      setPdf(URL.createObjectURL(blob)); setPdfType(type); setPdfPage(page || 1);
    } catch (error) { setPdfError(type === 'source' ? `PDF da fonte indisponível para visualização: ${error.message} Consulte a passagem registrada abaixo.` : error.message); } finally { setPdfBusy(false); }
  }
  async function submit(event) {
    event.preventDefault(); setBusy(true); setReviewError('');
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    try { await api(`/findings/${finding.id}/review`, { method: 'POST', body: fields }); setReview(fields); setReviewing(false); notify('Revisão humana registrada. O parecer original foi preservado.'); } catch (error) { setReviewError(error.message); } finally { setBusy(false); }
  }
  return <div className="finding-detail">
    <div className="detail-status-grid">{[['Vínculo', finding.binding_status], ['Identidade', finding.identity_status], ['Acesso', finding.access_status], ['Apoio semântico', finding.semantic_status]].map(([title, value]) => <div key={title}><span>{title}</span><Badge value={value} /></div>)}</div>
    <div className="comparison-grid"><section className="comparison-side manuscript"><div className="comparison-heading"><span className="eyebrow">NO MANUSCRITO</span><span className="page-label">Pág. {finding.page_start || '—'}</span></div><blockquote className="claim-block">{finding.claim_text}</blockquote><p className="detail-marker">{finding.marker}</p><button className="text-button" disabled={pdfBusy} onClick={() => openPdf('manuscript', null, finding.page_start)}><Icon name="file" size={16} />Abrir página do manuscrito</button><div className="reference-detail"><span className="eyebrow">REFERÊNCIA CITADA</span><p>{finding.reference_text || 'Consulte a entrada correspondente no catálogo de referências.'}</p><span className="subtle-id">{finding.reference_id ? `Ref. ${finding.reference_id.slice(0, 8)}` : 'Sem referência vinculada'}</span></div></section>
      <section className="comparison-side evidence"><div className="comparison-heading"><span className="eyebrow">NA FONTE CONSULTADA</span><Icon name="book" size={18} /></div>{data.loading ? <Spinner /> : data.error ? <Notice error>{data.error}</Notice> : data.items.length ? <>{data.items.map((evidence) => <article className="evidence-passage" key={evidence.id}><blockquote>{evidence.quote || evidence.text}</blockquote><p className="evidence-location">Página {evidence.page_start || 'não identificada'}{evidence.page_end && evidence.page_end !== evidence.page_start ? `–${evidence.page_end}` : ''} {evidence.source_version ? `· ${evidence.source_version}` : ''}</p><div className="evidence-links">{safeUrl(evidence.origin_url) && <a className="text-button" href={safeUrl(evidence.origin_url)} target="_blank" rel="noopener noreferrer">Abrir origem<Icon name="arrow" size={14} /></a>}{evidence.source_id && evidence.source_file_available && <button className="text-button" disabled={pdfBusy} onClick={() => openPdf('source', evidence.source_id, evidence.page_start)}>Ver PDF da fonte</button>}</div><span className="subtle-id">Evidência {evidence.id.slice(0, 8)}</span></article>)}<Pagination {...data} onChange={data.setOffset} /></> : <div className="no-evidence"><Icon name="search" size={26} /><h3>Sem passagem verificável</h3><p>A ausência de evidência acessível não demonstra que a afirmação esteja incorreta. Confira as limitações do parecer.</p></div>}</section></div>
    {pdfBusy && <Spinner>Preparando visualização autorizada…</Spinner>}{pdfError && <Notice error>{pdfError}</Notice>}{pdf && <div className="pdf-inline"><div className="section-heading"><strong>{pdfType === 'manuscript' ? 'Manuscrito' : 'Fonte'} · página {pdfPage}</strong><button className="text-button" onClick={() => setPdf(null)}>Fechar PDF</button></div><iframe src={`${pdf}#page=${pdfPage}&view=FitH`} title={`${pdfType === 'manuscript' ? 'Manuscrito' : 'Fonte'}, página ${pdfPage}`} /><p className="field-help">Posição aproximada por página. O trecho não recebe destaque quando faltam coordenadas confiáveis.</p></div>}
    <section className="assessment"><div className="section-heading"><h3>Parecer fundamentado</h3><Badge value={finding.severity} /></div><p>{finding.rationale || 'A avaliação deste par ainda não foi concluída.'}</p><div className="assessment-meta"><div><strong>Escopo consultado</strong><p>{formatReviewedScope(finding.reviewed_scope)}</p></div><div><strong>Limitações</strong><p>{asText(finding.limitations) || 'Consulte os estados de identidade e acesso acima.'}</p></div></div>{finding.suggested_adjustment && <div className="suggested-adjustment"><Icon name="link" size={18} /><div><strong>Ajuste sugerido</strong><p>{asText(finding.suggested_adjustment)}</p></div></div>}</section>
    <section className="human-review"><div><h3>Seu julgamento faz parte da revisão</h3><p>Comentários humanos são preservados junto ao parecer original.</p></div><button className="button secondary" onClick={() => setReviewing(!reviewing)}>{reviewing ? 'Fechar formulário' : 'Registrar minha revisão'}</button></section>
    {(finding.reviews || []).map((item) => <Notice key={item.id}>Revisão humana registrada: {label(item.decision)}. {item.comment}</Notice>)}
    {review && <Notice>Revisão registrada: {label(review.decision)}. {review.comment}</Notice>}
    {reviewing && <form onSubmit={submit} className="human-review-form"><label>Sua avaliação<select name="decision" required><option value="NEEDS_REVIEW">Solicito revisão</option><option value="AGREE">Concordo com o parecer</option><option value="DISAGREE">Discordo do parecer</option></select></label><label>Comentário<textarea name="comment" required minLength={3} maxLength={4000} rows={4} placeholder="Registre sua justificativa e, quando houver, a localização da evidência." /></label>{reviewError && <Notice error>{reviewError}</Notice>}<button className="button primary" disabled={busy}>{busy ? 'Salvando…' : 'Salvar revisão humana'}</button></form>}
  </div>;
}
