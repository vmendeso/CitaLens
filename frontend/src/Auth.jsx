import React, { useState } from 'react';
import { api, session } from './api.js';
import { Icon, Notice } from './components.jsx';

export default function Auth({ onLogin }) {
  const [register, setRegister] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event) {
    event.preventDefault();
    setBusy(true); setError('');
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    try {
      if (register) await api('/auth/register', { method: 'POST', body: fields });
      const result = await api('/auth/login', { method: 'POST', body: { email: fields.email, password: fields.password } });
      session.set(result); onLogin(result);
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  }
  return <main className="auth">
    <section className="auth-story">
      <a className="brand light" href="/"><span className="brand-mark"><Icon name="book" size={25} /></span>CitaLens<span className="brand-period">.</span></a>
      <div className="auth-story-content">
        <span className="eyebrow light">AUTOBIBLIOGRAPH REVISION</span>
        <h1>Uma boa pesquisa<br />merece uma<br /><em>leitura cuidadosa.</em></h1>
        <p>Da referência à evidência. Um espaço para examinar suas citações com contexto, transparência e rigor acadêmico.</p>
        <div className="editorial-illustration" aria-hidden="true">
          <div className="paper paper-back"><span>REFERÊNCIAS</span><i /><i /><i /><i /></div>
          <div className="paper paper-front"><span>MANUSCRITO</span><i /><i /><i className="highlight" /><i /><i className="short" /><div className="paper-check"><Icon name="link" /></div></div>
          <div className="illustration-caption"><span />Cada afirmação, em seu contexto.</div>
        </div>
      </div>
      <div className="auth-footer">Evidências para revisar. Clareza para decidir.</div>
    </section>
    <section className="auth-form-side">
      <div className="auth-form-wrap">
        <span className="eyebrow">SEU ESPAÇO DE PESQUISA</span>
        <h2>{register ? 'Comece uma nova leitura.' : 'Bem-vindo de volta.'}</h2>
        <p className="muted">{register ? 'Crie sua conta para organizar documentos e acompanhar suas revisões.' : 'Acesse seus documentos e continue de onde parou.'}</p>
        <form onSubmit={submit}>
          {register && <label>Nome<input name="name" autoComplete="name" required maxLength={100} placeholder="Como podemos chamar você?" /></label>}
          <label>E-mail<input name="email" type="email" autoComplete="email" required maxLength={254} placeholder="voce@universidade.edu.br" /></label>
          <label>Senha<input name="password" type="password" autoComplete={register ? 'new-password' : 'current-password'} required minLength={register ? 10 : undefined} maxLength={128} placeholder={register ? 'No mínimo 10 caracteres' : 'Sua senha'} /></label>
          {error && <Notice error>{error}</Notice>}
          <button className="button primary full" disabled={busy}>{busy ? 'Aguarde…' : register ? 'Criar minha conta' : 'Entrar no CitaLens'}<Icon name="arrow" size={18} /></button>
        </form>
        <p className="auth-switch">{register ? 'Já tem uma conta?' : 'Sua primeira visita?'} <button className="text-button" onClick={() => { setRegister(!register); setError(''); }}>{register ? 'Entrar' : 'Criar conta'}</button></p>
        <div className="auth-note"><Icon name="shield" size={21} /><p>Seus documentos ficam no seu espaço. O modo de demonstração usa fontes sintéticas claramente identificadas.</p></div>
      </div>
      <div className="auth-bottom">Apoio à revisão acadêmica · A decisão final é sua.</div>
    </section>
  </main>;
}
