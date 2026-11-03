import { navigate, rememberAssignments } from '../core/router.js';
import { $, $$, admin, dialog, e, errorMarkup, normalize, options, toast } from '../core/ui.js';
import { api } from '../core/api.js';
import { danishDay } from './series.js';
import { render } from '../core/router.js';
import { state } from '../core/state.js';
export const fleetLabel = course => course === 'stor' ? 'Stor bane' : 'Lille bane';
export function fleetError(error) {
    const node = $('#assignment-error') || $('.dialog-error', dialog);
    if (node)
        node.innerHTML = errorMarkup(error);
    else
        toast(error);
}
export async function openFleetCommander(heatId = null, seriesId = state.session.seriesId, options = {}) {
    if (!options.embedded) {
        await navigate(`/series/${seriesId}/assignments${heatId ? `?heat=${heatId}&scope=series` : ''}`);
        return;
    }
    if (!admin())
        return;
    if (state.assignments?.saving) {
        toast('Vent et øjeblik, mens ændringen gemmes.');
        return;
    }
    const data = seriesId === state.session.data.series_id ? state.session.data : await api(`/api/state?series_id=${seriesId}`);
    state.assignments = { data, catalog: [], members: new Set(), left: new Set(), right: new Set(), query: '', memberQuery: '', source: '', includeAssigned: false, scope: 'series', status: '', loading: false, saving: false };
    state.assignments.catalog = await api(`/api/boats/catalog?series_id=${seriesId}`);
    const first = data.rounds.flatMap(r => r.courses.flatMap(c => c.heats)).find(h => h.id === heatId) || data.rounds[0]?.courses[0]?.heats[0];
    if (!first) {
        toast('Opret et ræs med løb i serien først.');
        return;
    }
    fleetChooseHeat(first.id, 'series');
    const selection = state.assignmentsRoute;
    fleetChooseHeat(first.id, selection.scope || 'series');
    Object.assign(state.assignments, { source: selection.source || '', query: selection.query || '', memberQuery: selection.memberQuery || '', includeAssigned: !!selection.includeAssigned });
    renderFleetCommander();
}
export function fleetChooseHeat(id, scope) {
    const w = state.assignments;
    w.round = w.data.rounds.find(r => r.courses.some(c => c.heats.some(h => h.id === id)));
    w.course = w.round.courses.find(c => c.heats.some(h => h.id === id));
    w.heat = w.course.heats.find(h => h.id === id);
    w.scope = scope;
    w.members = new Set(w.data.boats.filter(b => w.heat.boat_ids.includes(b.id)).map(b => b.profile_id));
    w.status = '';
    $('.dialog-error', dialog)?.replaceChildren();
}
export function fleetTree() {
    const w = state.assignments;
    return `<nav class="fleet-tree" aria-label="Serier, løb og ræs">${w.data.series.map(series => `<section class="fleet-tree-series"><button type="button" data-fleet-series="${series.id}" class="fleet-series-button ${series.id === w.data.series_id ? 'active' : ''}" aria-expanded="${series.id === w.data.series_id}">${series.id === w.data.series_id ? '▾' : '▸'} ${e(series.name)}</button>${series.id === w.data.series_id ? ['stor', 'lille'].map(course => {
        const names = [...new Set(w.data.rounds.flatMap(r => r.courses.filter(c => c.course === course).flatMap(c => c.heats.map(h => h.name))))];
        return `<details class="fleet-course" ${course === w.course.course ? 'open' : ''}><summary>${fleetLabel(course)}</summary>${names.map(name => {
            const entries = w.data.rounds.flatMap(r => r.courses.filter(c => c.course === course).flatMap(c => c.heats.filter(h => h.name === name).map(h => ({ round: r, heat: h }))));
            const active = course === w.course.course && name === w.heat.name;
            return `<details class="fleet-tree-heat" ${active ? 'open' : ''}><summary>${e(name)}</summary><button type="button" data-fleet-heat="${entries[0].heat.id}" data-fleet-scope="series" class="${active && w.scope === 'series' ? 'active' : ''}" ${active && w.scope === 'series' ? 'aria-current="true"' : ''}>Hele serien</button>${entries.map(({ round, heat }) => `<button type="button" data-fleet-heat="${heat.id}" data-fleet-scope="round" class="${active && w.round.id === round.id && w.scope !== 'series' ? 'active' : ''}" ${active && w.round.id === round.id && w.scope !== 'series' ? 'aria-current="true"' : ''}>${e(round.label)}${round.race_date ? ' · ' + danishDay(round.race_date) : ''}</button>`).join('')}</details>`;
        }).join('')}</details>`;
    }).join('') : ''}</section>`).join('')}</nav>`;
}
export function renderFleetCommander() {
    const w = state.assignments;
    $('#fleet-commander-form').innerHTML = `<div class="fleet-columns"><section class="fleet-navigation" aria-label="Vælg serie og løb"><h3>Serier og løb</h3><div id="fleet-tree">${fleetTree()}</div></section><section class="fleet-panel fleet-destination" aria-label="Valgt løb"><h3>Både i løbet</h3><div class="fleet-target"><strong>${e(w.data.series.find(s => s.id === w.data.series_id).name)} · ${e(w.heat.name)}</strong><label class="field">Ændringen gælder<select id="fleet-scope">${options(['series', 'forward', 'backward', 'round'], w.scope, ['Hele serien', w.round.label + ' og frem', w.round.label + ' og bagud', 'Kun ' + w.round.label])}</select></label></div><label class="field">Søg i løbets både<input type="search" id="fleet-member-search" placeholder="Sejlnummer, bådnavn, skipper…" autocomplete="off" value="${e(w.memberQuery)}"></label><div class="fleet-list-tools"><label class="check"><input type="checkbox" data-fleet-all="left">Markér alle viste</label><span id="fleet-left-count"></span></div><div class="fleet-list-head" aria-hidden="true"><span>Båd</span><span>Sejlnr.</span><span>Skipper</span></div><div id="fleet-left-list" class="fleet-boat-list" aria-label="Både i det valgte løb"></div></section><div class="fleet-arrows"><button type="button" data-fleet-action="add" aria-label="Tilføj markerede både til løbet" title="Tilføj markerede både til løbet"><span aria-hidden="true">←</span><small>Tilføj</small></button><button type="button" data-fleet-action="remove" aria-label="Fjern markerede både fra løbet" title="Fjern markerede både fra løbet"><span aria-hidden="true">→</span><small>Fjern</small></button></div><section class="fleet-panel fleet-source" aria-label="Både til rådighed"><h3>Både til rådighed</h3><label class="field">Vis både fra<select id="fleet-source"><option value="">Alle serier og fælles bådliste</option>${options(w.data.series.map(s => s.id), w.source, w.data.series.map(s => s.name))}</select></label><label class="check fleet-include-label"><input type="checkbox" id="fleet-include-assigned" title="Vis og flyt både, der allerede er i andre løb i de valgte ræs" ${w.includeAssigned ? 'checked' : ''}>Medtag allerede fordelte både</label><label class="field timing-search-label">Søg både<input type="search" class="search-input" id="fleet-search" placeholder="Sejlnummer, bådnavn, skipper…" autocomplete="off" value="${e(w.query)}"></label><div class="fleet-list-tools"><label class="check"><input type="checkbox" data-fleet-all="right">Markér alle viste</label><span id="fleet-right-count"></span></div><div class="fleet-list-head" aria-hidden="true"><span>Båd</span><span>Sejlnr.</span><span>Skipper</span></div><div id="fleet-right-list" class="fleet-boat-list" aria-label="Både der kan tilføjes"></div></section></div><div class="fleet-footer"><span id="fleet-save-status" role="status" aria-live="polite"></span><span class="fleet-footer-help">Markér både · brug pilene · gemmes automatisk</span></div>`;
    $('#fleet-source').value = w.source;
    renderFleetLists();
}
export function fleetMatches(boat, query) {
    const text = normalize([boat.name, boat.skipper, boat.sail_number, boat.boat_type, boat.club, JSON.stringify(boat.ratings), ...boat.series.map(s => s.name + ' ' + s.class_name)].join(' '));
    return normalize(query.replace(/,/g, '.')).split(/\s+/).filter(Boolean).every(word => text.includes(word));
}
export function fleetTargetRoundUids() {
    const w = state.assignments;
    return new Set(w.data.rounds.filter(r => (w.scope === 'series' || (w.scope === 'round' && r.id === w.round.id) || (w.scope === 'forward' && r.position >= w.round.position) || (w.scope === 'backward' && r.position <= w.round.position)) && r.courses.some(c => c.course === w.course.course && c.heats.some(h => h.name === w.heat.name))).map(r => r.uid));
}
export function fleetOccupied(boat) {
    const rounds = fleetTargetRoundUids();
    return (boat.assignments || []).some(a => a.series_id === state.assignments.data.series_id && rounds.has(a.round_uid));
}
export function fleetAvailable(boat) { return !state.assignments.members.has(boat.id) && (state.assignments.includeAssigned || !fleetOccupied(boat)); }
export function fleetVisible(side) {
    const w = state.assignments;
    return w.catalog.filter(b => (side === 'left' ? w.members.has(b.id) : fleetAvailable(b)) && fleetMatches(b, side === 'left' ? w.memberQuery : w.query) && (side === 'left' || !w.source || b.series.some(s => s.id === Number(w.source))));
}
export function renderFleetLists() {
    const w = state.assignments;
    for (const id of w.left)
        if (!w.members.has(id))
            w.left.delete(id);
    const available = new Set(w.catalog.filter(fleetAvailable).map(b => b.id));
    for (const id of w.right)
        if (!available.has(id))
            w.right.delete(id);
    for (const side of ['left', 'right']) {
        const visible = fleetVisible(side), marked = w[side];
        const list = $(`#fleet-${side}-list`), scroll = list.scrollTop;
        $(`#fleet-${side}-list`).innerHTML = visible.map(b => `<div class="fleet-boat-row"><label class="fleet-boat ${marked.has(b.id) ? 'is-selected' : ''}" title="${e([b.name, b.sail_number, b.skipper, b.boat_type, b.club, ...b.series.map(s => s.name + ' / ' + s.class_name)].filter(Boolean).join(' · '))}"><input type="checkbox" data-fleet-side="${side}" data-fleet-profile="${b.id}" ${marked.has(b.id) ? 'checked' : ''}><span><strong>${e(b.name)}</strong> <span class="fleet-sail">${e(b.sail_number)}</span><span class="fleet-skipper">${e(b.skipper)}</span></span></label><div class="fleet-boat-actions"><button type="button" data-profile-edit="${b.id}" aria-label="Rediger ${e(b.name)}" title="Rediger båd">✎</button><button type="button" data-profile-delete="${b.id}" aria-label="Slet ${e(b.name)}" title="Slet båd">×</button></div></div>`).join('') || '<p class="fleet-empty">Ingen både matcher.</p>';
        $(`#fleet-${side}-count`).textContent = `${visible.length} vist · ${marked.size} markeret`;
        list.scrollTop = scroll;
        const all = $(`[data-fleet-all="${side}"]`), count = visible.filter(b => marked.has(b.id)).length;
        all.checked = visible.length > 0 && count === visible.length;
        all.indeterminate = count > 0 && count < visible.length;
        all.disabled = !visible.length;
    }
    $('[data-fleet-action="add"]').disabled = !w.right.size || w.loading;
    const remove = $('[data-fleet-action="remove"]');
    remove.disabled = !w.left.size || w.loading || w.scope !== 'round';
    remove.title = w.scope === 'round' ? 'Fjern markerede både fra dette ræs' : 'Vælg ét konkret ræs for at fjerne både';
    $('#fleet-save-status').textContent = w.status || `${w.members.size} både i løbet · ændringer gemmes automatisk`;
    $('.fleet-footer-help').textContent = w.scope === 'round' ? 'Markér både · brug pilene · gemmes automatisk' : 'Tilføj og flyt med venstre pil · vælg ét ræs for at fjerne';
    rememberAssignments(w);
    for (const panel of $$('.fleet-panel'))
        panel.inert = w.loading;
}
export async function fleetMove(action) {
    const w = state.assignments, marked = action === 'add' ? w.right : w.left;
    if (w.loading || !marked.size)
        return;
    w.loading = true;
    w.saving = true;
    w.status = 'Gemmer…';
    $('#assignment-error')?.replaceChildren();
    renderFleetLists();
    try {
        const result = await api('/api/heats/boats/move', { heat_id: w.heat.id, scope: w.scope, action, profile_ids: [...marked], include_assigned: w.includeAssigned });
        w.data = { ...w.data, ...result.data };
        w.catalog = result.catalog;
        if (state.session.seriesId === result.series_id)
            state.session.data = { ...state.session.data, ...result.data };
        if (w === state.assignments && $('#fleet-commander-form')) {
            fleetChooseHeat(w.heat.id, w.scope);
            w.status = `Gemt · ${result.changes.length} ræs opdateret.${result.changes.some(c => c.historical) ? ' Historiske resultater er kladder til ny offentliggørelse.' : ''}`;
            renderFleetLists();
        }
        else
            await render();
    }
    catch (error) {
        if (w === state.assignments && $('#fleet-commander-form')) {
            w.status = 'Lagringen kunne ikke bekræftes. Prøv pilen igen.';
            fleetError(error);
        }
    }
    finally {
        w.loading = false;
        w.saving = false;
        if (w === state.assignments && $('#fleet-commander-form'))
            renderFleetLists();
    }
}
export async function fleetSwitchSeries(id) { await navigate(`/series/${id}/assignments`); }
document.addEventListener('input', event => {
    if (!event.target.closest('#fleet-commander-form'))
        return;
    if (event.target.id === 'fleet-search') {
        state.assignments.query = event.target.value;
        renderFleetLists();
    }
    if (event.target.id === 'fleet-member-search') {
        state.assignments.memberQuery = event.target.value;
        renderFleetLists();
    }
});
document.addEventListener('keydown', event => {
    if (event.key === 'Enter' && event.target.matches('#fleet-search,#fleet-member-search'))
        event.preventDefault();
});
document.addEventListener('change', event => {
    const t = event.target;
    if (!t.closest('#fleet-commander-form'))
        return;
    if (t.dataset.fleetSide) {
        const selected = state.assignments[t.dataset.fleetSide];
        t.checked ? selected.add(Number(t.dataset.fleetProfile)) : selected.delete(Number(t.dataset.fleetProfile));
        renderFleetLists();
        $(`[data-fleet-side="${t.dataset.fleetSide}"][data-fleet-profile="${t.dataset.fleetProfile}"]`)?.focus({ preventScroll: true });
    }
    if (t.dataset.fleetAll) {
        const selected = state.assignments[t.dataset.fleetAll];
        for (const boat of fleetVisible(t.dataset.fleetAll))
            t.checked ? selected.add(boat.id) : selected.delete(boat.id);
        renderFleetLists();
    }
    if (t.id === 'fleet-include-assigned') {
        state.assignments.includeAssigned = t.checked;
        renderFleetLists();
    }
    if (t.id === 'fleet-source') {
        state.assignments.source = t.value;
        renderFleetLists();
    }
    if (t.id === 'fleet-scope') {
        state.assignments.scope = t.value;
        state.assignments.status = '';
        $('#fleet-tree').innerHTML = fleetTree();
        renderFleetLists();
    }
});
document.addEventListener('click', async (event) => {
    const button = event.target.closest('#fleet-commander-form button');
    if (!button || button.disabled || state.assignments.loading)
        return;
    if (button.matches('[data-profile-edit],[data-profile-delete]'))
        return;
    const w = state.assignments;
    try {
        if (button.dataset.fleetSeries || button.dataset.fleetHeat) {
            if (button.dataset.fleetSeries) {
                if (Number(button.dataset.fleetSeries) !== w.data.series_id) {
                    w.loading = true;
                    renderFleetLists();
                    await fleetSwitchSeries(Number(button.dataset.fleetSeries));
                }
                return;
            }
            fleetChooseHeat(Number(button.dataset.fleetHeat), button.dataset.fleetScope);
            renderFleetCommander();
            return;
        }
        const action = button.dataset.fleetAction;
        if (action === 'add' || action === 'remove')
            await fleetMove(action);
    }
    catch (error) {
        if (w === state.assignments)
            fleetError(error);
    }
    finally {
        w.loading = false;
        if (w === state.assignments && $('#fleet-commander-form'))
            renderFleetLists();
    }
});
document.addEventListener('submit', event => {
    if (event.target.id === 'fleet-commander-form')
        event.preventDefault();
});
