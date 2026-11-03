import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { admin, e, gphNumber, intro, normalize, number, options, shell } from '../core/ui.js';
import { openFleetCommander } from './assignments.js';
import { showTiming, chooseTimingRound } from './timing.js';
import { showStarts } from './race.js';
import { danishDay, startFields } from './series.js';
export const phaseLabel = { planned: 'Planlagt', ready: 'Klar', live: 'I gang', finished: 'Afsluttet' };
export async function showSeriesList() {
    const items = await Promise.all(state.session.data.series.map(s => api(`/api/workspaces/series/${s.id}`)));
    shell(`${intro('Serier', 'Vælg den serie, du vil arbejde med.', admin() ? '<button class="primary" data-action="new-series">Opret serie</button>' : '')}<div class="series-cards">${items.map(w => `<a class="series-card" href="/series/${w.series.id}" data-nav><h2>${e(w.series.name)}</h2><p>${w.series.year} · ${w.rounds.length} ræs · ${w.registrations.length} både</p><span>${w.rounds.filter(r => r.status.published).length} offentliggjorte ræs</span></a>`).join('') || '<p>Ingen serier endnu.</p>'}</div>`);
}
export async function showAssignmentsPage() {
    shell('<section class="assignment-workspace"><div id="assignment-host"><form id="fleet-commander-form" class="fleet-commander"></form></div><p id="assignment-error" class="dialog-error" role="alert"></p></section>');
    await openFleetCommander(state.assignmentsRoute.heatId, state.session.seriesId, { embedded: true });
}
export async function showRegistry() {
    const boats = await api('/api/boat-profiles');
    state.boats.catalog = boats;
    shell(`<section class="registry-page">${intro('Bådregister', 'Bådenes stamdata og handicap på tværs af serier.', admin() ? '<button class="primary" data-new-profile>Opret båd</button>' : '')}<div class="registry-controls"><label class="field registry-search">Find båd<input id="registry-search" type="search" placeholder="Båd, sejlnummer, skipper eller type…" value="${e(state.boats.query || '')}" autocomplete="off"></label><label class="field">Vis<select id="registry-usage" aria-label="Vis både">${options(['all', 'used', 'unused'], state.boats.usage, ['Alle både', 'Bruges i ræs', 'Uden ræs'])}</select></label><span id="registry-count" role="status" aria-live="polite"></span></div><div class="registry-list">${boats.map(b => `<article class="registry-boat" data-registry-row data-used="${b.round_count > 0}" data-search="${e(normalize([b.name, b.sail_number, b.skipper, b.boat_type, b.club, ...b.series.map(s => s.name)].join(' ')))}"><div class="registry-identity"><h2>${e(b.name)} <span>${e(b.sail_number)}</span></h2><p>${e(b.skipper)} · ${e(b.boat_type)}${b.club ? ' · ' + e(b.club) : ''}</p></div><dl class="registry-facts"><div><dt>GPH</dt><dd>${gphNumber(b.gph)}</dd></div><div><dt>Anvendelse</dt><dd>${b.round_count ? `${b.round_count} ræs · ${b.series.length} ${b.series.length === 1 ? 'serie' : 'serier'}` : 'Ingen ræs'}</dd></div></dl><div class="registry-actions"><button class="small" data-profile-edit="${b.id}">Rediger båd</button><button class="small" data-profile-usage="${b.id}">Anvendelse</button><button class="small" data-profile-history="${b.id}" ${b.saved_race_count ? '' : 'disabled title="Båden har endnu ingen ræs med gemte handicaptal"'}>Handicap i ræs</button><button class="small danger" data-profile-delete="${b.id}">Slet båd</button></div></article>`).join('')}</div><div id="registry-empty" class="empty" hidden><p>${boats.length ? 'Ingen både matcher søgningen og filteret.' : 'Der er endnu ingen både i registeret.'}</p>${boats.length ? '<button type="button" id="registry-reset">Nulstil filtre</button>' : ''}</div></section>`);
    filterRegistry(state.boats.query || '', state.boats.usage);
}
export function filterRegistry(query, usage = state.boats.usage) {
    state.boats.query = query;
    state.boats.usage = usage;
    const words = normalize(query).split(/\s+/).filter(Boolean);
    const rows = [...document.querySelectorAll('[data-registry-row]')];
    for (const row of rows) {
        const used = row.dataset.used === 'true';
        row.hidden = !words.every(w => row.dataset.search.includes(w)) || (usage === 'used' && !used) || (usage === 'unused' && used);
    }
    const count = rows.filter(row => !row.hidden).length;
    document.querySelector('#registry-count').textContent = `${count} af ${rows.length} både`;
    document.querySelector('#registry-empty').hidden = count > 0;
    const url = new URL(location.href);
    if (query) url.searchParams.set('q', query); else url.searchParams.delete('q');
    if (usage !== 'all') url.searchParams.set('usage', usage); else url.searchParams.delete('usage');
    history.replaceState({}, '', url);
}
export async function showSeriesSettings() {
    const expanded = new Set([...document.querySelectorAll('[data-series-round][open]')].map(node => node.dataset.seriesRound));
    const w = await api(`/api/workspaces/series/${state.session.seriesId}`), series = w.series;
    const rounds = w.rounds.map(round => {
        const courses = round.courses.map(course => {
            const race = state.session.data.races.find(r => r.id === course.race_id);
            const route = race ? JSON.parse(race.route) : [];
            return `<p><strong>${course.course === 'stor' ? 'Stor' : 'Lille'} bane:</strong> Start ${e(course.start_time || '–')} · ${race ? (route.length ? route.map(e).join(' → ') : 'N/A') + ' · ' + number(race.distance, 3) + ' sm' : 'Bane ikke indstillet'}</p>`;
        }).join('');
        return `<details class="panel series-round-settings" data-series-round="${round.id}" ${expanded.has(String(round.id)) ? 'open' : ''}><summary><strong>${e(round.label)}</strong> · ${round.race_date ? danishDay(round.race_date) : round.suggested_date ? danishDay(round.suggested_date) + ' (forslag)' : 'Dato ikke sat'} · ${phaseLabel[round.status.phase]}</summary><div class="padded">${admin() ? `<div class="row"><button data-round-starts="${round.id}">Dato og starttider</button><button data-configure-round="${round.id}" data-configure-course="stor">Indstil baner</button></div><label class="field round-system-field">Beregningsmål for begge baner<select data-round-system="${round.id}">${[['circle', 'Cirkelmål'], ['gph', 'GPH'], ['updown', 'Op/ned-mål']].map(([key, label]) => `<option value="${key}" ${round.handicap_system === key ? 'selected' : ''}>${label}</option>`).join('')}</select></label>` : ''}${courses}</div></details>`;
    }).join('');
    shell(`<section class="series-settings-page">${intro('Serieindstillinger', series.name, admin() ? `<a class="button primary" href="/series/${series.id}/assignments" data-nav>Løbsinddeling</a>` : '')}<div class="series-settings-layout"><form id="series-settings-form" class="stack workspace-form"><fieldset class="stack series-settings-fields" ${admin() ? '' : 'disabled'}><label class="field">Serienavn<input name="name" value="${e(series.name)}" required maxlength="100"></label><div class="form-grid"><label class="field">År<input name="year" value="${series.year}" data-number></label><label class="field">Referencehandicap<input name="reference" value="${series.reference}" data-number></label></div><h3>Standardstarttider</h3>${startFields(series)}<label class="field">Fratræk efter antal ræs<input name="discards" value="${e(JSON.parse(series.discards).join(', '))}"></label>${admin() ? '<button class="primary">Gem serieindstillinger</button>' : ''}</fieldset></form><section class="series-rounds"><div class="round-settings-heading"><h2>Ræs i serien</h2>${admin() ? '<button data-action="new-rounds">Tilføj ræs</button>' : ''}</div><div class="stack">${rounds || '<p>Der er endnu ingen ræs i serien.</p>'}</div></section></div></section>`);
}
export async function showRoundPage() {
    const w = await api(`/api/workspaces/rounds/${state.session.roundUid}${state.session.roundTab === 'history' ? '/history' : ''}`);
    state.session.roundId = w.round.id;
    state.timing.timingRoundId = w.round.id;
    if (state.session.roundTab === 'timing') {
        await showTiming();
        return;
    }
    if (state.session.roundTab === 'starts') {
        await showStarts();
        return;
    }
    if (state.session.roundTab === 'history') {
        shell(`${intro('Historik', 'Registrerede handlinger og rettelser for dette ræs.')}<div class="stack">${w.history.map(h => `<details class="audit-entry"><summary>${new Date(h.at * 1000).toLocaleString('da-DK')} · ${e(h.action)} · ${e(h.name || 'Administrator')}</summary><pre>${e(JSON.stringify(JSON.parse(h.payload), null, 2))}</pre></details>`).join('') || '<p>Ingen registrerede ændringer.</p>'}</div>`);
        return;
    }
    const planned = state.session.data.rounds.find(r => r.id === w.round.id);
    shell(`${intro('Opsætning', 'Fælles ræsindstillinger og opsætning af hver bane.', admin() ? `<button data-round-starts="${w.round.id}">Rediger dato og starttider</button>` : '')}<div class="round-summary"><span>Forløb: <strong>${phaseLabel[w.status.phase]}</strong></span><span>Resultater: <strong>${w.status.published ? 'Offentliggjort' : 'Kladde'}</strong></span><span>${w.status.participants} både</span></div>${admin() ? `<label class="field workspace-field">Beregningsmål for begge baner<select data-round-system="${w.round.id}">${[['circle', 'Cirkelmål'], ['gph', 'GPH'], ['updown', 'Op/ned-mål']].map(([key, label]) => `<option value="${key}" ${w.round.handicap_system === key ? 'selected' : ''}>${label}</option>`).join('')}</select></label>` : ''}<div class="course-cards">${planned.courses.map(c => { const race = w.courses.find(r => r.id === c.race_id); return `<section class="panel padded"><h2>${c.course === 'stor' ? 'Stor bane' : 'Lille bane'}</h2><p>${race ? `${e(race.mode === 'pursuit' ? 'Omvendt respit' : 'Almindelig respit')} · ${number(race.distance, 3)} sm · ${e(race.rating_key)}` : 'Bane og startform er ikke indstillet.'}</p><p>${c.heats.map(h => `${e(h.name)}: ${h.boat_ids.length} både`).join('<br>')}</p>${admin() ? `<button data-configure-round="${w.round.id}" data-configure-course="${c.course}">Indstil bane</button>` : ''}</section>`; }).join('')}</div>`);

}
export function todayRound() { return chooseTimingRound(); }
document.addEventListener('input', event => {
    if (event.target.id === 'registry-search')
        filterRegistry(event.target.value);
});

document.addEventListener('change', event => {
    if (event.target.id === 'registry-usage') filterRegistry(state.boats.query, event.target.value);
});
document.addEventListener('click', event => {
    if (event.target.id === 'registry-reset') {
        document.querySelector('#registry-search').value = '';
        document.querySelector('#registry-usage').value = 'all';
        filterRegistry('', 'all');
    }
});
