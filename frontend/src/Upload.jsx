import React, { useRef, useState } from 'react';
import { api, download } from './api.js';
import { Icon, Notice } from './components.jsx';

export function AnalysisOptions({ mode, setMode }) {
  return <>
    <fieldset className="mode-options"><legend>Como deseja analisar?</legend>
      <label className={`mode-option ${mode === 'real' ? 'selected' : ''}`}><input type="radio" name="mode" value="real" checked={mode === 'real'} onChange={() => setMode('real')} /><span><strong>Análise real</strong><small>Consulta os provedores configurados e registra as fontes acessíveis.</small></span></label>
      <label className={`mode-option ${mode === 'demo' ? 'selected' : ''}`}><input type="radio" name="mode" value="demo" checked={mode === 'demo'} onChange={() => setMode('demo')} /><span><strong>Demonstração</strong><small>Fontes e pareceres sintéticos. Use o manuscrito de exemplo.</small></span></label>
    </fieldset>
    {mode === 'demo' && <Notice>DEMONSTRAÇÃO: os resultados usam fixtures locais e não representam uma pesquisa na web.</Notice>}
    {mode === 'real' && <p className="field-help">Na análise real, metadados bibliográficos e trechos relevantes podem ser enviados aos provedores configurados pelo administrador.</p>}
    <div className="form-row"><label>Estilo de citação<select name="citation_style" defaultValue="unspecified"><option value="unspecified">Não especificado</option><option value="ABNT">ABNT</option><option value="APA">APA</option><option value="Vancouver">Vancouver</option></select></label><label>Idioma do documento<select name="language" defaultValue="pt"><option value="pt">Português</option><option value="en">Inglês</option></select></label></div>
    <details className="advanced"><summary>Indicar páginas da bibliografia <span>opcional</span></summary><p className="field-help">Use a numeração física do arquivo PDF, começando em 1.</p><div className="form-row"><label>Página inicial<input name="bibliography_page_start" type="number" min="1" placeholder="Detecção automática" /></label><label>Página final<input name="bibliography_page_end" type="number" min="1" placeholder="Detecção automática" /></label></div></details>
  </>;
}

export function analysisBody(form, mode) {
  const fields = Object.fromEntries(new FormData(form));
  const start = fields.bibliography_page_start ? Number(fields.bibliography_page_start) : null;
  const end = fields.bibliography_page_end ? Number(fields.bibliography_page_end) : null;
  if (Boolean(start) !== Boolean(end)) throw new Error('Informe as páginas inicial e final da bibliografia, ou deixe ambas vazias.');
  if (start && end && end < start) throw new Error('A página final deve ser maior ou igual à página inicial.');
  return { mode, citation_style: fields.citation_style, language: fields.language, bibliography_page_start: start, bibliography_page_end: end };
}

export default function Upload({ onComplete, onClose, document }) {
  const [file, setFile] = useState(null);
  const [mode, setMode] = useState('real');
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState('');
  const [error, setError] = useState('');
  const [uploaded, setUploaded] = useState(document || null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef(null);
  function choose(candidate) {
    setError('');
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith('.pdf')) { setError('Selecione um arquivo em formato PDF.'); return; }
    if (candidate.size > 30 * 1024 * 1024) { setError('O arquivo excede o limite de 30 MB desta interface.'); return; }
    if (!candidate.size) { setError('Este arquivo está vazio.'); return; }
    setFile(candidate); setUploaded(null);
  }
  async function submit(event) {
    event.preventDefault();
    setError(''); setBusy(true);
    try {
      const body = analysisBody(event.currentTarget, mode);
      let doc = uploaded;
      if (!doc) {
        if (!file) throw new Error('Selecione um PDF antes de iniciar.');
        setPhase('Enviando e validando o PDF…');
        const multipart = new FormData(); multipart.append('file', file);
        doc = await api('/documents', { method: 'POST', body: multipart });
        setUploaded(doc);
      }
      setPhase('Criando análise…');
      const run = await api(`/documents/${doc.id}/analyses`, { method: 'POST', body });
      onComplete(doc, run);
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  }
  return <form onSubmit={submit} className="upload-form">
    <p className="muted">{document ? 'Uma nova análise preserva os resultados e revisões anteriores.' : 'Envie seu manuscrito. Primeiro, vamos catalogar a bibliografia; depois, examinar cada ocorrência.'}</p>
    {!document && <><div className={`dropzone ${dragging ? 'dragging' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); if (!busy) choose(event.dataTransfer.files[0]); }}><div className="upload-icon"><Icon name={file ? 'file' : 'upload'} size={29} /></div><strong>{file ? file.name : 'Seu próximo documento começa aqui'}</strong><span>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB · PDF selecionado` : 'Arraste um PDF ou selecione no seu computador'}</span><button type="button" className="button secondary small" disabled={busy} onClick={() => fileRef.current.click()}>{file ? 'Trocar arquivo' : 'Selecionar PDF'}</button><small>PDF · até 30 MB · português ou inglês</small><input className="sr-only" ref={fileRef} type="file" accept=".pdf,application/pdf" aria-label="Selecionar manuscrito PDF" onChange={(event) => choose(event.target.files[0])} disabled={busy} /></div><button type="button" className="text-button demo-download" onClick={() => download('/demo/manuscript', 'citalens-manuscrito-demonstracao.pdf').catch((failure) => setError(failure.message))}><Icon name="download" size={16} />Baixar manuscrito de demonstração</button></>}
    <AnalysisOptions mode={mode} setMode={setMode} />
    {uploaded && !document && error && <Notice>O PDF já foi salvo. Ao tentar novamente, apenas a criação da análise será repetida.</Notice>}
    {error && <Notice error>{error}</Notice>}
    <div className="modal-actions"><button type="button" className="button secondary" onClick={onClose} disabled={busy}>Voltar</button><button className="button primary" disabled={busy || (!file && !uploaded)}>{busy ? phase : 'Iniciar análise'}{!busy && <Icon name="arrow" size={17} />}</button></div>
  </form>;
}
