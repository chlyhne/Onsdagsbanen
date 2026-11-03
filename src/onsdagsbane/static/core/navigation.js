import { state } from './state.js';
import { currentSeries } from './api.js';
import { e, admin } from './ui.js';
const danishDay = value => value ? value.split('-').reverse().join('-') : '';
export function currentRound() { return state.session.data?.rounds.find(r => r.uid === state.session.roundUid || r.id === state.session.roundId); }
export function link(url, text, active = false) { return `<a href="${e(url)}" data-nav ${active ? 'aria-current="page" class="active"' : ''}>${e(text)}</a>`; }
export function workspaceNavigation() {
    const page = state.session.page, id = state.session.seriesId;
    if (!['series', 'assignments', 'registrations', 'settings', 'series-results', 'round'].includes(page))
        return '';
    const series = currentSeries(), round = currentRound();
    const tabs = [['series', 'Ræs', ''], ['registrations', 'Seriens både', '/boats'], ['assignments', 'Både og løbsinddeling', '/assignments'], ['series-results', 'Resultater', '/results'], ['settings', 'Serieindstillinger', '/settings']];
    const menu = tabs.filter(([key]) => admin() || !['settings', 'assignments', 'registrations'].includes(key));
    return `<div class="workspace-context"><div class="workspace-breadcrumb">${link('/series', 'Serier')}<span>›</span>${link(`/series/${id}`, series?.name || 'Serie')}${page === 'round' && round ? `<span>›</span><strong>${e(round.label)}</strong>` : ''}</div><nav class="workspace-tabs" aria-label="Seriens arbejdsrum">${menu.map(([key, label, suffix]) => link(`/series/${id}${suffix}`, label, page === key)).join('')}</nav>${page === 'round' && round ? `<div class="round-heading"><h1>${e(round.label)}</h1><span>${round.race_date ? danishDay(round.race_date) : 'Dato ikke sat'}</span><span class="pill">${round.completed ? 'Afsluttet' : 'Åbent ræs'}</span></div><nav class="workspace-tabs round-tabs" aria-label="Ræsets arbejdsrum">${[['setup', 'Opsætning'], ['starts', 'Startlister'], ['timing', 'Måltagning'], ['results', 'Resultater'], ...(admin() ? [['history', 'Historik']] : [])].map(([key, label]) => link(`/rounds/${round.uid}/${key}`, label, state.session.roundTab === key)).join('')}</nav>` : ''}</div>`;
}
