import { $, date, dialog, e, keyNames, keys, openDialog, toast } from '../core/ui.js';
import { api, load } from '../core/api.js';
import { fleetLabel, fleetChooseHeat, renderFleetCommander } from './assignments.js';
import { render } from '../core/router.js';
import { dialogError, historicalCalculationDialog, scheduleRaceSetup } from './race.js';
import { runtime, state } from '../core/state.js';
let repairReturn = null;

async function beginHandicapRepair(issue) {
    const current = $('#boat-profile-form');
    if (current && Number(current.dataset.profileId) === issue.profile_id) {
        $(`[name="${issue.required_fields[0]}"]`, current)?.focus();
        return;
    }
    const fragment = document.createDocumentFragment();
    if (dialog.open) while (dialog.firstChild) fragment.append(dialog.firstChild);
    repairReturn = { fragment, hadDialog: !!fragment.childNodes.length, route: [...runtime.route], issue };
    $('#toast').classList.remove('visible');
    try {
        await editBoatProfile(issue.profile_id);
        const form = $('#boat-profile-form');
        form.dataset.repair = 'true';
        form.querySelector('button[type=submit]').textContent = 'Gem handicap og vend tilbage';
        form.insertAdjacentHTML('afterbegin', `<p class="notice info">${e(issue.round_label)} kræver ${e(issue.required_key)}. ${issue.race_id ? 'Kun manglende tal suppleres i det gemte handicapsæt for dette ræs; eksisterende historiske tal bevares.' : 'Udfyld de manglende tal, og fortsæt derefter, hvor du var.'}</p>`);
        for (const key of issue.required_fields) {
            const field = $(`[name="${key}"]`, form);
            if (field) { field.required = true; field.closest('label').classList.add('handicap-required'); }
        }
        $(`[name="${issue.required_fields[0]}"]`, form)?.focus();
    } catch (error) { await returnFromHandicapRepair(false); throw error; }
}

async function returnFromHandicapRepair(saved) {
    const context = repairReturn;
    repairReturn = null;
    if (!context) return;
    let refreshError = null;
    try {
        if (saved) {
            await load();
            if ($('#fleet-commander-form') && state.assignments) {
                const w = state.assignments;
                w.data = { ...w.data, ...state.session.data };
                w.catalog = await api(`/api/boats/catalog?series_id=${w.data.series_id}`);
                fleetChooseHeat(w.heat.id, w.scope);
                $('#assignment-error')?.replaceChildren();
                renderFleetCommander();
            }
        }
    } catch (error) { refreshError = error; }
    runtime.route = context.route;
    if (context.hadDialog) {
        dialog.replaceChildren(context.fragment);
        if (!dialog.open) dialog.showModal();
        if (saved) {
            $('.dialog-error', dialog)?.replaceChildren();
            for (const token of dialog.querySelectorAll('#day-token,#calculation-token')) token.value = '';
            if ($('#race-form')) scheduleRaceSetup();
        }
    } else dialog.close();
    if (saved) toast(refreshError ? 'Handicap gemt. Listen kunne ikke opdateres; prøv igen eller genindlæs siden.' : 'Handicap gemt. Du kan fortsætte, hvor du var.');
}

document.addEventListener('click', async event => {
    const button = event.target.closest('[data-handicap-fix]');
    if (!button || runtime.busy) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    try { await beginHandicapRepair(JSON.parse(button.dataset.handicapFix)); }
    catch (error) { toast(error); }
}, true);

dialog.addEventListener('close', () => {
    if (repairReturn) returnFromHandicapRepair(false).catch(error => toast(error));
});
export async function editBoatProfile(id = null) {
    const boat = id ? await api(`/api/boat-profiles?id=${id}`) : { name: '', skipper: '', sail_number: '', boat_type: '', club: '', ratings: {}, extra_handicap: 0, token: '' };
    openDialog(id ? `Rediger ${e(boat.name)}` : 'Opret båd', `<form id="boat-profile-form" class="stack" data-profile-id="${id || ''}"><input type="hidden" name="token" value="${e(boat.token)}"><div class="form-grid">${[['name', 'Bådnavn'], ['skipper', 'Skipper'], ['sail_number', 'Sejlnummer'], ['boat_type', 'Bådtype'], ['club', 'Klub']].map(([key, label]) => `<label class="field">${label}<input name="${key}" value="${e(boat[key])}" maxlength="160" ${key === 'club' ? '' : 'required'}></label>`).join('')}</div><h3>Handicap til nye ræs</h3><div class="rating-grid">${keys.map((key, i) => `<label class="field">${key}<span class="subtle">${keyNames[i]}</span><input name="${key}" value="${e(boat.ratings[key] ?? '')}" type="text" inputmode="decimal" data-number placeholder="Ikke udfyldt"></label>`).join('')}</div><label class="field">Ekstra handicap (sekunder)<input name="extra_handicap" type="text" inputmode="numeric" data-number value="${boat.extra_handicap}" required></label><p class="notice info">Oplysningerne gælder båden i alle serier. Nye handicaptal bruges fremover; gemte starttider, måltider og handicapsæt i eksisterende sejladser bevares.</p><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button type="submit" class="primary">Gem båd</button></div></form>`);
}
export async function deleteBoatDialog(id) {
    const preview = await api(`/api/boat-profiles/delete-preview?id=${id}`);
    openDialog(preview.can_delete ? `Slet ${e(preview.name)} fra systemet?` : `${e(preview.name)} kan ikke slettes`, `<form id="boat-delete-form" class="stack" data-profile-id="${id || ''}"><input type="hidden" name="token" value="${e(preview.token)}">${preview.can_delete ? `<p>Båden er ikke med i nogen ræs. Den slettes permanent fra hele systemets fælles bådliste; eventuelle tomme serietilmeldinger fjernes også.</p>${preview.series.length ? `<p>Tomme serietilmeldinger, der fjernes:</p><ul>${preview.series.map(s => `<li>${e(s.name)}</li>`).join('')}</ul>` : '<p>Båden er ikke tilmeldt nogen serie.</p>'}` : `<p class="notice" role="alert">${e(preview.reason)}</p><p>Båden er med i følgende ræs:</p><ul class="boat-delete-races">${(preview.rounds || []).map(r => `<li><strong>${e(r.series_name)} · ${e(r.label)}</strong><span>${e(fleetLabel(r.course))} · ${e(r.class_name)}</span></li>`).join('')}</ul>`}<div class="dialog-actions"><button type="button" data-action="close-dialog">${preview.can_delete ? 'Annuller' : 'Luk'}</button>${preview.can_delete ? '<button type="submit" class="danger">Slet båd fra systemet</button>' : ''}</div></form>`);
}
export async function historicalBoatDialog(id) {
    if (state.session.page !== 'registry') return;
    const [boat, races] = await Promise.all([api(`/api/boat-profiles?id=${id}`), api(`/api/boat-profiles/history?id=${id}`)]);
    state.boats.historicalRaces = races;
    openDialog(`Handicap i ræs · ${e(boat.name)}`, races.length ? `<form id="boat-history-form" class="stack"><p>Vælg det ræs, hvor bådens gemte handicap skal rettes.</p><label class="field">Tidligere ræs<select name="race_id">${races.map(r => `<option value="${r.race_id}">${e(r.series_name)} · ${e(r.label)} · ${date(r.start)} · ${e(fleetLabel(r.course))}</option>`).join('')}</select></label><div class="dialog-actions"><button type="button" data-action="close-dialog">Luk</button><button type="submit" class="primary">Åbn handicap</button></div></form>` : '<p>Båden har endnu ingen ræs med gemte handicaptal.</p><div class="dialog-actions"><button data-action="close-dialog">Luk</button></div>');
}
document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-profile-edit],[data-profile-delete],[data-new-profile],[data-profile-usage],[data-profile-history],[data-unregister]');
    if (!button || button.disabled || runtime.busy)
        return;
    try {
        if (button.hasAttribute('data-new-profile')) {
            await editBoatProfile();
            return;
        }
        if (button.dataset.profileUsage) {
            await showBoatUsage(Number(button.dataset.profileUsage));
            return;
        }
        if (button.dataset.profileHistory) {
            await historicalBoatDialog(Number(button.dataset.profileHistory));
            return;
        }
        if (button.dataset.unregister) {
            await api(`/api/series/registrations/${Number(button.dataset.unregister)}/remove`, {});
            await load();
            await render();
            return;
        }
        if (button.dataset.profileEdit)
            await editBoatProfile(Number(button.dataset.profileEdit));
        else
            await deleteBoatDialog(Number(button.dataset.profileDelete));
    }
    catch (error) {
        toast(error);
    }
});
document.addEventListener('submit', async (event) => {
    const form = event.target;
    if (!['boat-profile-form', 'boat-delete-form', 'boat-history-form'].includes(form.id))
        return;
    event.preventDefault();
    if (runtime.busy)
        return;
    runtime.busy = true;
    form.inert = true;
    try {
        if (form.id === 'boat-history-form') {
            const race = state.boats.historicalRaces.find(r => r.race_id === Number(form.elements.race_id.value));
            await historicalCalculationDialog(race.race_id, race.boat_id);
            return;
        }
        const fields = Object.fromEntries(new FormData(form)), deleting = form.id === 'boat-delete-form';
        const data = { ...fields, id: Number(form.dataset.profileId) };
        if (deleting)
            data.confirm = true;
        else {
            data.ratings = Object.fromEntries(keys.map(key => [key, fields[key] || null]));
            data.extra_handicap = fields.extra_handicap;
            if (repairReturn?.issue.race_id) data.repair = repairReturn.issue;
        }
        await api(deleting ? '/api/boat-profiles/delete' : '/api/boat-profiles', data);
        if (!deleting && !data.id && state.session.page === 'registry') {
            state.boats.query = fields.name;
            state.boats.usage = 'all';
        }
        if (form.dataset.repair) {
            await returnFromHandicapRepair(true);
            return;
        }
        await load();
        await render();
        dialog.close();
        toast(deleting ? 'Båden er slettet.' : 'Bådens oplysninger er gemt.');
    }
    catch (error) {
        dialogError(error);
    }
    finally {
        runtime.busy = false;
        form.inert = false;
    }
});
dialog.addEventListener('cancel', event => {
    if (runtime.busy && ($('#boat-profile-form') || $('#boat-delete-form')))
        event.preventDefault();
});
export async function showBoatUsage(id) {
    const preview = await api(`/api/boat-profiles/delete-preview?id=${id}`);
    const groups = new Map(preview.series.map(series => [series.series_id, { name: series.name, rounds: [] }]));
    for (const round of preview.rounds) {
        if (!groups.has(round.series_id)) groups.set(round.series_id, { name: round.series_name, rounds: [] });
        groups.get(round.series_id).rounds.push(round);
    }
    const count = new Set(preview.rounds.map(round => round.uid)).size;
    const summary = groups.size ? `${count} ræs i ${groups.size} ${groups.size === 1 ? 'serie' : 'serier'}.` : 'Båden er ikke tilmeldt nogen serie eller ræs.';
    openDialog(`Anvendelse: ${e(preview.name)}`, `<div class="boat-usage"><p>${summary}</p>${[...groups.values()].map(group => `<section class="boat-usage-series"><h3>${e(group.name)}</h3>${group.rounds.length ? `<ul class="boat-usage-list">${group.rounds.map(round => `<li><strong>${e(round.label)}</strong><span>${e(fleetLabel(round.course))} · ${e(round.class_name)}</span></li>`).join('')}</ul>` : '<p>Ingen ræs i denne serie.</p>'}</section>`).join('')}</div><div class="dialog-actions"><button type="button" data-action="close-dialog">Luk</button></div>`);
}
