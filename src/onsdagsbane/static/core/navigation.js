import { state } from './state.js';
import { currentSeries } from './api.js';
import { e, options } from './ui.js';
const danishDay = value => value ? value.split('-').reverse().join('-') : '';
export function link(url, text, active = false) { return `<a href="${e(url)}" data-nav ${active ? 'aria-current="page" class="active"' : ''}>${e(text)}</a>`; }
export function timingURL(seriesId = state.timing.seriesId || state.session.seriesId, roundUid = state.timing.roundUid) {
    const query = new URLSearchParams();
    if (seriesId) query.set('series', String(seriesId));
    if (roundUid) query.set('round', roundUid);
    return '/timing' + (query.size ? '?' + query : '');
}
export function roundSelectors() {
    const data = state.session.data, rounds = data.rounds || [];
    return `<div class="results-controls workspace-selectors"><label class="field">Serie<select id="timing-series" aria-label="Serie">${options(data.series.map(s => s.id), state.session.seriesId, data.series.map(s => s.name))}</select></label><label class="field">Ræs<select id="timing-race" aria-label="Ræs" ${rounds.length ? '' : 'disabled'}>${rounds.length ? rounds.map(r => `<option value="${r.id}" ${r.id === state.session.roundId ? 'selected' : ''}>${e(r.label)} · ${r.race_date ? danishDay(r.race_date) : r.suggested_date ? danishDay(r.suggested_date) + ' (forslag)' : 'Dato ikke sat'}${r.completed ? ' · afsluttet' : ''}</option>`).join('') : '<option>Ingen ræs</option>'}</select></label></div>`;
}
export function workspaceNavigation() {
    const page = state.session.page, id = state.session.seriesId;
    if (!['series', 'assignments', 'registrations', 'settings', 'round'].includes(page))
        return '';
    const series = currentSeries();
    if (page === 'round')
        return state.session.roundTab === 'timing' ? '' : `<div class="workspace-context">${roundSelectors()}</div>`;
    if (page === 'assignments')
        return `<div class="workspace-context"><div class="row">${link(`/series/${id}`, '← Indstillinger')}<strong>${e(series?.name || 'Serie')}</strong></div></div>`;
    return '';
}
