import React, { useCallback, useEffect, useState } from 'react';
import { api, session } from './api.js';
import { Badge, Empty, Icon, Modal, Notice, Pagination, Spinner, formatDate } from './components.jsx';
import { useCollection } from './hooks.js';
import Auth from './Auth.jsx';
import Upload from './Upload.jsx';
import Analysis from './Analysis.jsx';

export default function App() {
  const [auth, setAuth] = useState(session.get);
  const [selected, setSelected] = useState(null);
  const [analysisId, setAnalysisId] = useState(null);
  const [upload, setUpload] = useState(false);
  const [notice, setNotice] = useState('');
  const [mobileMenu, setMobileMenu] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [history, setHistory] = useState([]);
  const [historyError, setHistoryError] = useState('');
  const docs = useCollection(auth ? '/documents' : null);
  const pendingDeletion = docs.items.some((doc) => doc.deleting && !doc.deletion_error);
  useEffect(() => {
    if (!pendingDeletion) return undefined;
    const timer = setInterval(docs.reload, 3000);
    return () => clearInterval(timer);
  }, [pendingDeletion, docs.reload]);
  const logout = useCallback(() => { session.clear(); setAuth(null); setSelected(null); setAnalysisId(null); setHistory([]); }, []);
  useEffect(() => { window.addEventListener('citalens:expired', logout); return () => window.removeEventListener('citalens:expired', logout); }, [logout]);
  useEffect(() => {
    if (!notice) return undefined;
    const timer = setTimeout(() => setNotice(''), 8000); return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    if (!selected) return;
    let live = true;
    setHistory([]); setHistoryError('');
    api(`/documents/${selected.id}/analyses?limit=100&offset=0`).then((result) => {
      if (!live) return;
      setHistory(result.items || []);
      setAnalysisId((current) => (result.items || []).some((run) => run.id === current) ? current : result.items?.[0]?.id || null);
    }).catch((error) => { if (live) setHistoryError(error.message); });
    return () => { live = false; };
  }, [selected?.id]);
  function selectDocument(doc) { if (doc.deleting) return; setSelected(doc); setAnalysisId(null); setMobileMenu(false); }
  function completed(doc, run) {
    setSelected(doc); setAnalysisId(run.id); setHistory((current) => [run, ...current.filter((item) => item.id !== run.id)]); docs.reload(); setUpload(false); setNotice('Análise iniciada. Você pode acompanhar cada etapa abaixo.');
  }
  async function removeDocument(doc = selected) {
    setDeleteBusy(true);
    try { await api(`/documents/${doc.id}`, { method: 'DELETE' }); setSelected(null); setAnalysisId(null); setDeleting(false); docs.reload(); setNotice('Exclusão solicitada. Acompanhe o andamento na lista de documentos.'); } catch (error) { setNotice(error.message); } finally { setDeleteBusy(false); }
  }
  if (!auth) return <Auth onLogin={setAuth} />;
  return <div className="app-shell">
    <a className="skip-link" href="#main">Pular para o conteúdo</a>
    <aside className={`sidebar ${mobileMenu ? 'open' : ''}`}>
      <button className="brand light brand-button" onClick={() => { setSelected(null); setMobileMenu(false); }} aria-label="CitaLens, início"><span className="brand-mark"><Icon name="book" size={24} /></span>CitaLens<span className="brand-period">.</span></button>
      <p className="sidebar-caption">AUTOBIBLIOGRAPH REVISION</p>
      <button className="new-document" onClick={() => { setUpload(true); setMobileMenu(false); }}><Icon name="plus" size={18} />Novo documento</button>
      <div className="sidebar-section-label">MEU ESPAÇO</div>
      <button className={`sidebar-link ${!selected ? 'active' : ''}`} onClick={() => { setSelected(null); setMobileMenu(false); }}><Icon name="grid" size={18} />Todos os documentos<span>{docs.total}</span></button>
      <div className="sidebar-section-label document-label">DOCUMENTOS RECENTES</div>
      <nav className="document-nav" aria-label="Documentos">{docs.items.map((doc) => <button key={doc.id} disabled={doc.deleting} title={doc.deleting ? 'Documento em exclusão; acompanhe na biblioteca.' : doc.title || doc.filename} className={`document-link ${selected?.id === doc.id ? 'active' : ''}`} onClick={() => selectDocument(doc)}><Icon name={doc.deleting ? 'clock' : 'file'} size={17} /><span>{doc.title || doc.filename || 'Documento sem título'}</span>{selected?.id === doc.id && <span className="document-active-dot" />}</button>)}{!docs.items.length && <p className="sidebar-empty">Seus documentos aparecerão aqui.</p>}</nav>
      <div className="sidebar-bottom"><div className="sidebar-principle"><Icon name="shield" size={20} /><p>Uma referência não é uma evidência.<br /><strong>Leia o contexto.</strong></p></div><div className="user-profile"><span className="avatar">{(auth.user?.name || auth.user?.email || 'P').slice(0, 1).toUpperCase()}</span><div><strong>{auth.user?.name || 'Pesquisador'}</strong><small>{auth.user?.email || 'Conta autenticada'}</small></div><button className="icon-button light" onClick={logout} aria-label="Sair da conta" title="Sair"><Icon name="logout" size={18} /></button></div></div>
    </aside>
    {mobileMenu && <button className="sidebar-backdrop" aria-label="Fechar menu" onClick={() => setMobileMenu(false)} />}
    <div className="main-shell">
      <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu-button" aria-label="Abrir menu" onClick={() => setMobileMenu(true)}><Icon name="menu" /></button><span>Meu espaço</span><Icon name="chevron" size={12} /><strong>{selected ? 'Revisão de documento' : 'Documentos'}</strong></div><span className="workspace-tag"><span />Ambiente de revisão</span></header>
      <main id="main" className="main-content">
        {notice && <div className="toast" role="status"><Icon name="check" size={18} /><span>{notice}</span><button className="icon-button" aria-label="Dispensar aviso" onClick={() => setNotice('')}><Icon name="close" size={16} /></button></div>}
        {!selected ? <>
          <div className="page-heading"><div><span className="eyebrow">DA REFERÊNCIA À EVIDÊNCIA</span><h1>Sua biblioteca de revisões</h1><p>Um olhar atento para cada afirmação do seu trabalho.</p></div><button className="button primary" onClick={() => setUpload(true)}><Icon name="plus" size={18} />Novo documento</button></div>
          <section className="welcome-banner"><div className="welcome-text"><span className="eyebrow">RIGOR, COM CONTEXTO</span><h2>O que a fonte realmente diz?</h2><p>Organize referências, acompanhe a cobertura do manuscrito e compare cada afirmação com as evidências disponíveis.</p><button className="text-button" onClick={() => setUpload(true)}>Começar uma revisão <Icon name="arrow" size={17} /></button></div><div className="method-mini"><div><span>01</span>Catalogar referências</div><i /><div><span>02</span>Mapear citações</div><i /><div><span>03</span>Examinar evidências</div></div></section>
          <section className="panel document-library"><div className="section-heading"><div><h2>Seus documentos <span className="count">{docs.total}</span></h2><p>Resultados e histórico de cada manuscrito.</p></div><button className="text-button" onClick={docs.reload}>Atualizar</button></div>
            {docs.loading && !docs.items.length ? <Spinner /> : docs.error ? <Notice error>{docs.error}</Notice> : docs.items.length ? <><div className="documents-table"><div className="documents-table-header"><span>DOCUMENTO</span><span>ADICIONADO EM</span><span /></div>{docs.items.map((doc) => <React.Fragment key={doc.id}><button className="document-row" disabled={doc.deleting} onClick={() => selectDocument(doc)}><span className="document-cell"><span className="pdf-icon"><Icon name="file" size={25} /><small>PDF</small></span><span><strong>{doc.title || doc.filename || 'Documento sem título'}</strong><small>{doc.deleting ? doc.deletion_error ? 'Exclusão não concluída' : 'Exclusão em andamento' : `${doc.page_count ? `${doc.page_count} páginas · ` : ''}Manuscrito original`}</small></span></span><span className="document-date">{formatDate(doc.created_at)}</span><Icon name={doc.deleting ? 'clock' : 'chevron'} size={18} /></button>{doc.deleting && doc.deletion_error && <div className="document-deletion-error"><p role="alert"><Icon name="alert" size={16} /><span>{doc.deletion_error}</span></p><button className="button secondary small" disabled={deleteBusy} onClick={() => removeDocument(doc)}>Tentar excluir novamente</button></div>}</React.Fragment>)}</div><Pagination {...docs} onChange={docs.setOffset} /></> : <Empty title="Sua próxima leitura começa aqui" icon="book" action={<button className="button primary" onClick={() => setUpload(true)}><Icon name="upload" size={17} />Enviar meu primeiro PDF</button>}>Envie um artigo, tese, dissertação ou TCC para começar. Você também pode usar o manuscrito de demonstração.</Empty>}
          </section><p className="workspace-footnote">As conclusões dependem do conteúdo efetivamente acessível. Casos inconclusivos permanecem visíveis.</p>
        </> : <>
          <div className="document-topline"><button className="text-button muted" onClick={() => setSelected(null)}>← Todos os documentos</button><button className="icon-button" title="Excluir documento" aria-label="Excluir documento" onClick={() => setDeleting(true)}><Icon name="trash" size={17} /></button></div>
          <div className="page-heading document-heading"><div><span className="eyebrow">REVISÃO BIBLIOGRÁFICA</span><h1>{selected.title || selected.filename || 'Manuscrito'}</h1><p>Adicionado em {formatDate(selected.created_at)}</p></div><button className="button secondary" onClick={() => setUpload('analysis')}><Icon name="plus" size={17} />Nova análise</button></div>
          {historyError && <Notice error>{historyError}</Notice>}
          {history.length > 1 && <label className="history-selector">Histórico de análises<select value={analysisId || ''} onChange={(event) => setAnalysisId(event.target.value)}>{history.map((run) => <option value={run.id} key={run.id}>{formatDate(run.created_at)} · {run.config?.mode === 'demo' ? 'Demonstração' : 'Análise'} · {run.id.slice(0, 8)}</option>)}</select></label>}
          {analysisId ? <Analysis key={analysisId} id={analysisId} document={selected} notify={setNotice} /> : <section className="panel"><Empty title="Pronto para uma leitura cuidadosa" icon="book" action={<button className="button primary" onClick={() => setUpload('analysis')}>Iniciar análise<Icon name="arrow" size={17} /></button>}>Este documento ainda não tem uma análise selecionada. Inicie uma revisão para catalogar suas referências.</Empty></section>}
        </>}
      </main>
      <footer className="app-footer"><span>CitaLens <span className="footer-separator">/</span> Autobibliograph Revision</span><span>Uma ferramenta de apoio à revisão acadêmica.</span></footer>
    </div>
    {upload && <Modal title={upload === 'analysis' ? 'Nova análise' : 'Adicionar documento'} onClose={() => setUpload(false)}><Upload document={upload === 'analysis' ? selected : null} onComplete={completed} onClose={() => setUpload(false)} /></Modal>}
    {deleting && <Modal title="Excluir este documento?" onClose={() => setDeleting(false)}><p>O manuscrito, seus resultados e artefatos associados serão excluídos. Esta ação não pode ser desfeita.</p><div className="modal-actions"><button className="button secondary" disabled={deleteBusy} onClick={() => setDeleting(false)}>Manter documento</button><button className="button danger" disabled={deleteBusy} onClick={() => removeDocument()}>{deleteBusy ? 'Excluindo…' : 'Excluir documento'}</button></div></Modal>}
  </div>;
}
