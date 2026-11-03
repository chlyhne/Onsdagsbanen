import { state } from './state.js';
export async function api(path, data) {
    const response = await fetch(path, { method: data ? 'POST' : 'GET', headers: data ? { 'Content-Type': 'application/json', 'X-CSRF-Token': state.session.data?.user?.csrf || '' } : {}, body: data ? JSON.stringify(data) : undefined });
    let result;
    try {
        result = await response.json();
    }
    catch {
        throw new Error('Serveren gav ikke et læsbart svar. Registreringen er ikke bekræftet.');
    }
    if (!response.ok)
        throw Object.assign(new Error(result.error || 'Handlingen kunne ikke gennemføres.'), { handicapIssues: result.handicap_issues || [] });
    return result;
}
export async function load() {
    const before = Date.now();
    state.session.data = await api('/api/state' + (state.session.seriesId ? `?series_id=${state.session.seriesId}` : ''));
    if (!state.session.data.series.some(series => series.id === state.session.data.series_id))
        state.session.data = await api('/api/state');
    state.session.offset = new Date(state.session.data.server_time).getTime() - (before + Date.now()) / 2;
    state.session.seriesId = state.session.data.series_id;
    state.session.connected = true;
}
export const currentSeries = () => state.session.data.series.find(s => s.id === state.session.seriesId);
