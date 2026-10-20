import React, { useEffect, useRef } from 'react';

export function Icon({ name, size = 20, ...props }) {
  const paths = {
    book: <><path d="M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H4z" /><path d="M13 7a3 3 0 0 1 3-3h4v15h-3a4 4 0 0 0-4 2" /></>,
    file: <><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z" /><path d="M14 3v5h5M8 13h8M8 17h5" /></>,
    upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5" /></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    search: <><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6" /></>,
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    link: <><path d="m10 13 4-4M9 16l-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0M15 8l2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(0 -1) scale(.95)" /></>,
    download: <path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4" />,
    plus: <path d="M12 5v14M5 12h14" />,
    alert: <><path d="m12 3 10 18H2zM12 9v5" /><path d="M12 17h.01" /></>,
    logout: <><path d="M9 3H4v18h5M10 12h12m-5-5 5 5-5 5" /></>,
    chevron: <path d="m9 5 7 7-7 7" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z" /><path d="m8 12 3 3 5-6" /></>,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    trash: <><path d="M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7M14 10v7" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name] || paths.file}</svg>;
}

export const LABELS = {
  QUEUED: 'Na fila', INGESTING: 'Preparando PDF', EXTRACTING_REFERENCES: 'Catalogando referências',
  MAPPING_CITATIONS: 'Mapeando citações', RESOLVING_SOURCES: 'Identificando fontes', EVALUATING: 'Avaliando afirmações',
  GENERATING_REPORT: 'Gerando relatório', COMPLETED: 'Concluída', COMPLETED_WITH_WARNINGS: 'Concluída com ressalvas',
  FAILED: 'Falha no processamento', CANCELLED: 'Cancelada', CANCEL_REQUESTED: 'Cancelamento solicitado',
  SUPPORTED: 'Apoiada', PARTIALLY_SUPPORTED: 'Apoio parcial', CONTRADICTED: 'Contradita',
  NOT_SUPPORTED_BY_REVIEWED_CONTENT: 'Sem apoio no conteúdo consultado', INCONCLUSIVE: 'Inconclusiva', NOT_EVALUATED: 'Não avaliada',
  RESOLVED: 'Vínculo resolvido', AMBIGUOUS: 'Vínculo ambíguo', MISSING_REFERENCE: 'Sem referência',
  MATCHED: 'Identidade confirmada', METADATA_MISMATCH: 'Metadados divergentes', UNRESOLVED: 'Identidade não resolvida',
  FULL_TEXT: 'Texto completo', PARTIAL_TEXT: 'Texto parcial', ABSTRACT_ONLY: 'Somente resumo', METADATA_ONLY: 'Somente metadados',
  RESTRICTED: 'Acesso restrito', NOT_FOUND: 'Fonte não localizada', FETCH_ERROR: 'Falha de acesso',
  HIGH: 'Alta', MEDIUM: 'Média', LOW: 'Baixa', INFO: 'Informativa', CRITICAL: 'Crítica',
  PROCESSED: 'Processada', PENDING: 'Pendente', OCR: 'OCR', NATIVE: 'Texto nativo',
  MAPPED: 'Mapeada', NO_CITATIONS: 'Sem citações', UNVERIFIABLE: 'Não verificável', PARTIALLY_READABLE: 'Parcialmente legível',
  native: 'Texto nativo', ocr: 'OCR', mixed: 'Extração mista', NONE: 'Sem extração',
  AGREE: 'Concordo', DISAGREE: 'Discordo', NEEDS_REVIEW: 'Solicito revisão', READY: 'Disponível',
};
export const label = (value) => LABELS[value] || value || 'Não informado';
export const isTerminal = (status) => ['COMPLETED', 'COMPLETED_WITH_WARNINGS', 'FAILED', 'CANCELLED'].includes(status);
export const formatDate = (value) => value ? new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : 'Data não informada';
export const asText = (value) => Array.isArray(value) ? value.map((item) => typeof item === 'string' ? item : item.message || item.name || JSON.stringify(item)).join(' · ') : typeof value === 'object' && value ? JSON.stringify(value) : value;

export function Badge({ value, children }) {
  const tone = ['SUPPORTED', 'MATCHED', 'RESOLVED', 'COMPLETED', 'PROCESSED', 'FULL_TEXT', 'READY'].includes(value) ? 'green' :
    ['HIGH', 'CRITICAL', 'CONTRADICTED', 'FAILED', 'MISSING_REFERENCE', 'METADATA_MISMATCH'].includes(value) ? 'red' :
    ['PARTIALLY_SUPPORTED', 'MEDIUM', 'AMBIGUOUS', 'COMPLETED_WITH_WARNINGS', 'INCONCLUSIVE', 'RESTRICTED'].includes(value) ? 'amber' : 'gray';
  return <span className={`badge ${tone}`}><span className="badge-dot" />{children || label(value)}</span>;
}

export function Empty({ title, children, icon = 'search', action }) {
  return <div className="empty"><div className="empty-icon"><Icon name={icon} size={30} /></div><h3>{title}</h3><p>{children}</p>{action}</div>;
}

export function Notice({ children, error = false }) {
  return <div className={`notice ${error ? 'error' : ''}`} role={error ? 'alert' : 'status'}><Icon name={error ? 'alert' : 'shield'} size={18} /><div>{children}</div></div>;
}

export function Spinner({ children = 'Carregando…' }) {
  return <div className="loading" role="status"><span className="spinner" />{children}</div>;
}

export function Pagination({ total, offset, limit, onChange }) {
  if (total <= limit) return null;
  return <div className="pagination"><span>{offset + 1}–{Math.min(offset + limit, total)} de {total}</span><div><button className="button secondary small" disabled={!offset} onClick={() => onChange(Math.max(0, offset - limit))}>Anterior</button><button className="button secondary small" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>Próxima</button></div></div>;
}

export function Modal({ title, children, onClose, wide = false }) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = ref.current;
    dialog.showModal();
    const close = (event) => { event.preventDefault(); closeRef.current(); };
    dialog.addEventListener('cancel', close);
    return () => { dialog.removeEventListener('cancel', close); dialog.close(); previous?.focus(); };
  }, []);
  return <dialog ref={ref} className={`modal ${wide ? 'wide' : ''}`} aria-labelledby="modal-title" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}><div className="modal-header"><h2 id="modal-title">{title}</h2><button className="icon-button" aria-label="Fechar" onClick={onClose}><Icon name="close" /></button></div>{children}</dialog>;
}
