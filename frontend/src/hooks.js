import { useCallback, useEffect, useState } from 'react';
import { api, query } from './api.js';
import { isTerminal } from './components.jsx';

export function useCollection(path, filters = {}, refresh = 0) {
  const [state, setState] = useState({ items: [], total: 0, loading: true, error: '' });
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const serialized = JSON.stringify(filters);
  useEffect(() => { setOffset(0); }, [path, serialized]);
  useEffect(() => {
    if (!path) { setState({ items: [], total: 0, loading: false, error: '' }); return undefined; }
    const controller = new AbortController();
    setState((current) => ({ ...current, loading: true, error: '' }));
    api(`${path}${query({ ...JSON.parse(serialized), limit: 25, offset })}`, { signal: controller.signal })
      .then((result) => setState({ ...result, items: result.items || [], total: result.total ?? result.items?.length ?? 0, loading: false, error: '' }))
      .catch((error) => { if (error.name !== 'AbortError') setState({ items: [], total: 0, loading: false, error: error.message }); });
    return () => controller.abort();
  }, [path, serialized, offset, version, refresh]);
  return { ...state, offset, limit: 25, setOffset, reload: useCallback(() => setVersion((value) => value + 1), []) };
}

export function useAnalysis(id, refresh = 0) {
  const [state, setState] = useState({ run: null, progress: null, error: '', loading: false });
  useEffect(() => {
    if (!id) { setState({ run: null, progress: null, error: '', loading: false }); return undefined; }
    let disposed = false;
    let timer;
    const controller = new AbortController();
    setState({ run: null, progress: null, error: '', loading: true });
    const poll = async () => {
      try {
        const [run, progress] = await Promise.all([api(`/analyses/${id}`, { signal: controller.signal }), api(`/analyses/${id}/progress`, { signal: controller.signal })]);
        if (disposed) return;
        setState({ run, progress, error: '', loading: false });
        if (!isTerminal(run.status) || (run.reports || []).some((report) => ['QUEUED', 'PROCESSING', 'GENERATING', 'PENDING'].includes(report.status))) timer = setTimeout(poll, 3000);
      } catch (error) {
        if (disposed || error.name === 'AbortError') return;
        setState((current) => ({ ...current, loading: false, error: error.message }));
        timer = setTimeout(poll, 7000);
      }
    };
    poll();
    return () => { disposed = true; clearTimeout(timer); controller.abort(); };
  }, [id, refresh]);
  return state;
}

export function useReferenceOptions(id, refresh = 0) {
  const [state, setState] = useState({ items: [], error: '' });
  useEffect(() => {
    if (!id) return undefined;
    const controller = new AbortController();
    const fetchAll = async () => {
      try {
        const items = [];
        let total = 1;
        while (items.length < total) {
          const result = await api(`/analyses/${id}/references${query({ limit: 100, offset: items.length })}`, { signal: controller.signal });
          items.push(...result.items);
          total = result.total;
          if (!result.items.length) break;
        }
        setState({ items, error: '' });
      } catch (error) { if (error.name !== 'AbortError') setState({ items: [], error: error.message }); }
    };
    fetchAll();
    return () => controller.abort();
  }, [id, refresh]);
  return state;
}
