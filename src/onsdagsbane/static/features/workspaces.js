import { link } from '../core/navigation.js';
import { state } from '../core/state.js';
import { api, currentSeries } from '../core/api.js';
import { admin, e, gphNumber, intro, number, shell, toast } from '../core/ui.js';
import { openFleetCommander } from './assignments.js';
import { showTiming, chooseTimingRound } from './timing.js';
import { showStarts, historicalCalculationDialog } from './race.js';
import { showResults } from './results.js';
import { danishDay, startFields } from './series.js';
export const phaseLabel = { planned: 'Planlagt', ready: 'Klar', live: 'I gang', finished: 'Afsluttet' };
export async function showSeriesList() {
    const items = await Promise.all(state.session.data.series.map(s => api(`/api/workspaces/series/${s.id}`)));
    shell(`${intro('Serier', 'Vælg den serie, du vil arbejde med.', admin() ? '<button class="primary" data-action="new-series">Opret serie</button>' : '')}<div class="series-cards">${items.map(w => `<a class="series-card" href="/series/${w.series.id}" data-nav><h2>${e(w.series.name)}</h2><p>${w.series.year} · ${w.rounds.length} ræs · ${w.registrations.length} både</p><span>${w.rounds.filter(r => r.status.published).length} offentliggjorte ræs</span></a>`).join('') || '<p>Ingen serier endnu.</p>'}</div>`);
}
export async function showSeriesHome() {
    const w = await api(`/api/workspaces/series/${state.session.seriesId}`);
    shell(`${intro('Ræs', 'Planlæg sæsonen, og åbn et ræs for at arbejde med begge baner.', admin() ? '<button class="primary" data-action="new-rounds">Tilføj ræs</button>' : '')}<div class="table-scroll"><table class="workspace-table"><thead><tr><th>Ræs</th><th>Dato</th><th>Forløb</th><th>Resultater</th><th>Både</th><th>Baner</th><th></th></tr></thead><tbody>${w.rounds.map(r => `<tr><td><strong>${e(r.label)}</strong></td><td>${r.race_date ? danishDay(r.race_date) : 'Ikke sat'}</td><td>${phaseLabel[r.status.phase]}</td><td>${r.status.published ? 'Offentliggjort' : 'Kladde'}</td><td>${r.status.participants}</td><td>${r.courses.map(c => `${c.course === 'stor' ? 'Stor' : 'Lille'}: ${c.race_id ? 'indstillet' : 'mangler opsætning'}`).join(' · ')}</td><td>${link(`/rounds/${r.uid}/${r.status.phase === 'planned' ? 'setup' : 'timing'}`, 'Åbn ræs →')}</td></tr>`).join('')}</tbody></table></div>`);
}
export async function showRegistrationPage() {
    const w = await api(`/api/workspaces/series/${state.session.seriesId}`);
    shell(`${intro('Seriens både', 'Tilmeldingen hører til serien. Bådens stamdata hører til det fælles bådregister.', `<a class="button primary" data-nav href="/series/${state.session.seriesId}/assignments">Åbn løbsinddeling</a>`)}<div class="table-scroll"><table class="workspace-table"><thead><tr><th>Båd</th><th>Sejlnr.</th><th>Skipper</th><th>Ræs</th><th></th></tr></thead><tbody>${w.registrations.map(b => `<tr><td>${e(b.name)}</td><td>${e(b.sail_number)}</td><td>${e(b.skipper)}</td><td>${b.round_count}</td><td><button class="small" data-profile-edit="${b.profile_id}">Rediger båd</button> <button class="small" data-unregister="${b.id}" ${b.round_count ? 'disabled title="Fjern båden fra seriens ræs enkeltvis først"' : ''}>Afmeld serie</button></td></tr>`).join('') || '<tr><td colspan="5">Ingen både er tilmeldt serien.</td></tr>'}</tbody></table></div>`);
}
export async function showAssignmentsPage() {
    shell('<section class="assignment-workspace"><div id="assignment-host"><form id="fleet-commander-form" class="fleet-commander"></form></div><p id="assignment-error" class="dialog-error" role="alert"></p></section>');
    await openFleetCommander(state.assignmentsRoute.heatId, state.session.seriesId, { embedded: true });
}
export async function showRegistry() {
    const boats = await api('/api/boat-profiles');
    state.boats.catalog = boats;
    shell(`${intro('Bådregister', 'Fælles både og handicapsæt på tværs af alle serier.', admin() ? '<button class="primary" data-new-profile>Opret båd</button>' : '')}<label class="field registry-search">Søg i alle både<input id="registry-search" type="search" placeholder="Båd, sejlnummer, skipper…" value="${e(state.boats.query || '')}" autocomplete="off"></label><div class="table-scroll"><table class="workspace-table"><thead><tr><th>Båd</th><th>Sejlnr.</th><th>Skipper</th><th>Type</th><th>GPH</th><th>Serier / ræs</th><th>Handlinger</th></tr></thead><tbody>${boats.map(b => `<tr data-registry-row data-search="${e([b.name, b.sail_number, b.skipper, b.boat_type, b.club].join(' ').toLocaleLowerCase('da-DK'))}"><td><strong>${e(b.name)}</strong></td><td>${e(b.sail_number)}</td><td>${e(b.skipper)}</td><td>${e(b.boat_type)}</td><td>${gphNumber(b.gph)}</td><td>${b.series.length} serier · ${b.assignments.length} ræs</td><td>${admin() ? `<button class="small" data-profile-edit="${b.id}">Rediger</button> <button class="small" data-profile-usage="${b.id}">Anvendelse</button> <button class="small danger" data-profile-delete="${b.id}">Slet fra systemet</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="7">Ingen både endnu.</td></tr>'}</tbody></table></div>`);
    filterRegistry(state.boats.query || '');
}
export function filterRegistry(query) {
    state.boats.query = query;
    const words = query.toLocaleLowerCase('da-DK').split(/\s+/).filter(Boolean);
    for (const row of document.querySelectorAll('[data-registry-row]'))
        row.hidden = !words.every(w => row.dataset.search.includes(w));
}
export function showSeriesSettings() {
    const series = currentSeries();
    shell(`${intro('Serieindstillinger', 'Disse standarder gælder fremtidige ræs. Historiske rettelser foretages på det konkrete ræs.')}<form id="series-settings-form" class="stack workspace-form"><label class="field">Serienavn<input name="name" value="${e(series.name)}" required maxlength="100"></label><div class="form-grid"><label class="field">År<input name="year" value="${series.year}" data-number ${state.session.data.races.length ? 'disabled' : ''}></label><label class="field">Referencehandicap<input name="reference" value="${series.reference}" data-number></label></div><h3>Standardstarttider</h3>${startFields(series)}<label class="field">Fratræk efter antal ræs<input name="discards" value="${e(JSON.parse(series.discards).join(', '))}"></label><button class="primary">Gem serieindstillinger</button></form>`);
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
    if (state.session.roundTab === 'results') {
        state.roundResults.view = 'daily';
        await showResults();
        return;
    }
    if (state.session.roundTab === 'history') {
        shell(`${intro('Historik', 'Registrerede handlinger og rettelser for dette ræs.')}<div class="stack">${w.history.map(h => `<details class="audit-entry"><summary>${new Date(h.at * 1000).toLocaleString('da-DK')} · ${e(h.action)} · ${e(h.name || 'Administrator')}</summary><pre>${e(JSON.stringify(JSON.parse(h.payload), null, 2))}</pre></details>`).join('') || '<p>Ingen registrerede ændringer.</p>'}</div>`);
        return;
    }
    const planned = state.session.data.rounds.find(r => r.id === w.round.id);
    shell(`${intro('Opsætning', 'Fælles ræsindstillinger og opsætning af hver bane.', admin() ? `<button data-round-starts="${w.round.id}">Rediger dato og starttider</button>` : '')}${admin() && w.courses.length ? '<p><button data-action="day-settings">Ret distance og vind for dette ræs</button></p>' : ''}<div class="round-summary"><span>Forløb: <strong>${phaseLabel[w.status.phase]}</strong></span><span>Resultater: <strong>${w.status.published ? 'Offentliggjort' : 'Kladde'}</strong></span><span>${w.status.participants} både</span></div>${admin() ? `<label class="field workspace-field">Beregningsmål for begge baner<select data-round-system="${w.round.id}">${[['circle', 'Cirkelmål'], ['gph', 'GPH'], ['updown', 'Op/ned-mål']].map(([key, label]) => `<option value="${key}" ${w.round.handicap_system === key ? 'selected' : ''}>${label}</option>`).join('')}</select></label>` : ''}<div class="course-cards">${planned.courses.map(c => { const race = w.courses.find(r => r.id === c.race_id); return `<section class="panel padded"><h2>${c.course === 'stor' ? 'Stor bane' : 'Lille bane'}</h2><p>${race ? `${e(race.mode === 'pursuit' ? 'Omvendt respit' : 'Almindelig respit')} · ${number(race.distance, 3)} sm · ${e(race.rating_key)}` : 'Bane og startform er ikke indstillet.'}</p><p>${c.heats.map(h => `${e(h.name)}: ${h.boat_ids.length} både`).join('<br>')}</p>${admin() && !race ? `<button data-configure-round="${w.round.id}" data-configure-course="${c.course}">Indstil bane</button>` : ''}${race ? link(`/rounds/${w.round.uid}/timing`, 'Åbn måltagning →') : ''}</section>`; }).join('')}</div>`);
    if (admin())
        planned.courses.forEach((course, index) => {
            if (course.race_id)
                document.querySelectorAll('.course-cards .panel')[index].insertAdjacentHTML('beforeend', ` <button class="small" data-calculation-race="${course.race_id}">Ret beregningsgrundlag</button>`);
        });
}
export function todayRound() { return chooseTimingRound(); }
document.addEventListener('input', event => {
    if (event.target.id === 'registry-search')
        filterRegistry(event.target.value);
});
document.addEventListener('click', async (event) => {
    const b = event.target.closest('[data-calculation-race]');
    if (b) {
        try {
            await historicalCalculationDialog(Number(b.dataset.calculationRace));
        }
        catch (error) {
            toast(error);
        }
    }
});
