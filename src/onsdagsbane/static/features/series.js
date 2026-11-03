import { $, $$, clock, date, e, now, openDialog, options, shell } from '../core/ui.js';
import { api, currentSeries } from '../core/api.js';
import { timingDay } from './timing.js';
import { state } from '../core/state.js';
export function showSeries() {
    const races = new Map(state.session.data.races.map(r => [r.id, r]));
    function courseCell(round, course) {
        const child = round.courses.find(c => c.course === course), race = races.get(child.race_id);
        return `<td><div class="series-heats">${child.heats.map(heat => `<button class="heat-link" data-heat="${heat.id}">${e(heat.name)} · ${heat.boat_ids.length} ${heat.boat_ids.length === 1 ? 'båd' : 'både'}</button>`).join('')}</div><div class="series-course-actions">${race ? `<span>${date(race.start)} · ${clock(race.start)} · ${race.mode === 'pursuit' ? 'Omvendt respit' : 'Almindelig respit'}</span><button class="small" data-open-timing="${race.id}">Måltagning</button>` : `<span>Start ${e(child.start_time || '–')} · bane ikke indstillet</span><button class="small" data-configure-round="${round.id}" data-configure-course="${course}">Indstil bane</button>`}</div></td>`;
    }
    shell(`<section class="series-page"><div class="results-heading"><h1>${e(currentSeries().name)}</h1><div class="row"><button class="primary" data-action="new-rounds">Tilføj ræs</button><button data-action="edit-series">Rediger serie</button><button data-action="catalog">Genbrug både fra en anden serie</button></div></div><p>Vælg et løb for at inddele bådene. Som standard gælder løbsinddelingen hele serien. Indstil dato, startform og baneforløb på hver bane før måltagning.</p><div class="table-scroll"><table class="series-table"><thead><tr><th scope="col">Ræs</th><th scope="col">Stor bane</th><th scope="col">Lille bane</th></tr></thead><tbody>${(state.session.data.rounds || []).map(round => `<tr><th scope="row">${e(round.label)}<div class="round-date">${round.race_date ? danishDay(round.race_date) : 'Dato ikke sat'}</div><button class="small" data-round-starts="${round.id}">Rediger ræs</button><label class="field round-system-label">Beregningsmål<select data-round-system="${round.id}" aria-label="Beregningsmål ${e(round.label)}">${options(['circle', 'gph', 'updown'], round.handicap_system || 'circle', ['Cirkelmål', 'GPH', 'Op/ned-mål'])}</select></label></th>${courseCell(round, 'stor')}${courseCell(round, 'lille')}</tr>`).join('')}</tbody></table></div></section>`);
}
export function startFields(values = {}, prefix = 'default_start_') {
    return `<div class="form-grid">${['stor', 'lille'].map(course => `<label class="field">${course === 'stor' ? 'Stor' : 'Lille'} bane · start (HH:MM:SS)<input name="${prefix + course}" type="text" data-start-time inputmode="numeric" maxlength="8" pattern="[0-9]{2}:[0-9]{2}:[0-9]{2}" value="${e(values[prefix + course] || values[course] || (course === 'stor' ? '18:30:00' : '18:35:00'))}" required></label>`).join('')}</div>`;
}
export function normalizeStartTime(field, complete = false) {
    field.value = field.value.replace(/[.,]/g, ':');
    if (complete && /^\d{2}:\d{2}$/.test(field.value))
        field.value += ':00';
    field.setCustomValidity(field.value && !/^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$/.test(field.value) ? 'Angiv et gyldigt klokkeslæt som HH:MM:SS.' : '');
}
export function danishDay(day) { return day ? day.split('-').reverse().join('-') : ''; }
export function setRaceToday(button) {
    const input = $('[name="race_date"]', button.form);
    if (!input || input.disabled)
        return;
    input.value = danishDay(timingDay(now()));
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.focus();
}
export function isoDay(value) {
    if (!value.trim())
        return null;
    const match = /^(\d{2})-(\d{2})-(\d{4})$/.exec(value.trim().replace(/[./]/g, '-'));
    if (!match)
        throw new Error('Angiv datoen som DD-MM-ÅÅÅÅ.');
    const [, d, m, y] = match;
    return `${y}-${m}-${d}`;
}
export async function roundStartsDialog(id) {
    const round = state.session.data.rounds.find(r => r.id === id), detail = await api(`/api/rounds/${id}/timing`);
    const locked = !!round.completed || detail.entries.some(r => r.race_id && r.sequence !== null && r.status !== 'DUTY') || detail.captures.some(c => c.race_id);
    const times = Object.fromEntries(round.courses.map(c => [c.course, c.start_time]));
    openDialog(`Ræs ${e(round.label)} · dato og starttider`, `<form id="round-starts-form" class="stack"><input type="hidden" name="round_id" value="${id}"><div class="row"><label class="field">Dato<input name="race_date" type="text" data-calendar data-calendar-year="${currentSeries().year}" inputmode="numeric" maxlength="10" placeholder="Vælg dato" value="${danishDay(round.race_date || round.suggested_date)}"></label><button type="button" class="small" data-action="race-today" ${locked ? 'disabled' : ''}>Dags dato</button></div>${startFields(times, 'start_')}<p class="subtle">Disse tider gælder kun ${e(round.label)}. Seriens standardtider ændres ikke.</p>${locked ? '<p>Der er allerede måltagning eller resultater i ræset. Starttiderne er beskyttet.</p>' : ''}<div class="dialog-actions"><button type="button" data-action="close-dialog">Luk</button><button class="primary" ${locked ? 'disabled' : ''}>Gem ræsindstillinger</button></div></form>`);
    if (locked)
        for (const input of $$('#round-starts-form input'))
            input.disabled = true;
}
export function roundsDialog() { openDialog('Tilføj ræs til serien', `<form id="rounds-form" class="stack"><p><strong>${e(currentSeries().name)}</strong></p><label class="field">Ræs<textarea name="round_labels" required placeholder="R19–R24 eller ét navn pr. linje"></textarea></label><h3>Starttider til de nye ræs</h3>${startFields({ stor: currentSeries().default_start_stor, lille: currentSeries().default_start_lille }, 'start_')}<p>Hvert ræs får egne starttider og begge baners løb. Tiderne kan rettes på det enkelte ræs.</p><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Tilføj ræs</button></div></form>`); }
export function seriesSettingsDialog() {
    const series = currentSeries();
    openDialog('Rediger den valgte serie', `<form id="series-settings-form" class="stack"><label class="field">Serienavn<input name="name" required maxlength="100" value="${e(series.name)}"></label><div class="form-grid"><label class="field">År<input name="year" type="text" inputmode="numeric" data-number="true" required value="${series.year}"></label><label class="field">Standardreference til nye sejladser (s/sm)<input name="reference" type="text" inputmode="decimal" data-number="true" required value="${series.reference}"></label></div><h3>Standardstarttider til nye ræs</h3>${startFields(series)}<label class="field">Fratræk efter antal sejladser (kommasepareret)<input name="discards" value="${e(JSON.parse(series.discards).join(', '))}" placeholder="6, 9, 12, 15, 18"></label><p class="subtle">Fratræk gælder seriens pointstilling. Standardreferencen bruges til nye sejladser. Eksisterende sejladser beholder deres reference.</p><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Gem seriens indstillinger</button></div></form>`);
}
export function discardsInput(value) {
    const parts = value.trim() ? value.trim().split(/[\s,;]+/) : [];
    if (parts.some(part => !/^\d+$/.test(part)))
        throw new Error('Angiv fratræk som hele antal sejladser, fx 6, 9, 12.');
    return parts.map(Number);
}
export function seriesDialog() {
    openDialog('Opret ny serie', `<form id="series-form" class="stack"><label class="field">Serienavn<input name="name" required maxlength="100" placeholder="Onsdagsbanen 2027"></label><div class="form-grid"><label class="field">År<input name="year" type="text" inputmode="numeric" data-number="true" value="${new Date().getFullYear() + 1}" required></label><label class="field">Referencehandicap (s/sm)<input name="reference" type="text" inputmode="decimal" data-number="true" value="1000" required></label></div><h3>Standardstarttider</h3>${startFields()}<label class="field">Ræs i serien<textarea name="round_labels" required rows="2">R1–R18</textarea></label><p class="subtle">Skriv et interval som R1–R18 eller ét ræsnavn pr. linje. Hvert ræs får begge baner med løbene nedenfor.</p><div class="form-grid"><label class="field">Løb på stor bane · ét pr. linje<textarea name="stor_fleets" required rows="2">Stor bane 1
Stor bane 2</textarea></label><label class="field">Løb på lille bane · ét pr. linje<textarea name="lille_fleets" required rows="2">Lille bane 1
Lille bane 2</textarea></label></div><label class="field">Fratræk efter antal sejladser (kommasepareret)<input name="discards" placeholder="6, 9, 12, 15, 18"></label><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Opret serie med ræs og løb</button></div></form>`);
}
