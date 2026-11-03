import { render } from '../core/router.js';
import { api, currentSeries } from '../core/api.js';
import { $, admin, clock, date, dialog, e, errorMarkup, intro, keys, markMap, number, openDialog, options, raceOptions, seconds, selectors, shell, toast } from '../core/ui.js';
import { openFleetCommander } from './assignments.js';
import { danishDay } from './series.js';
import { refreshTiming, timingDay } from './timing.js';
import { runtime, state } from '../core/state.js';
export async function showStarts() {
    let races = state.session.data.races.filter(r => r.course === state.planning.course && r.mode === 'pursuit' && (!state.session.roundId || state.session.data.rounds.find(p => p.id === state.session.roundId)?.courses.some(c => c.race_id === r.id)));
    if (!races.some(r => r.id === state.planning.startRaceId))
        state.planning.startRaceId = races[0]?.id;
    const detail = state.planning.startRaceId ? await api(`/api/races/${state.planning.startRaceId}?startlist=1`) : null;
    const race = detail?.race;
    const entries = (detail?.entries || []).sort((a, b) => new Date(a.start) - new Date(b.start) || a.sail_number.localeCompare(b.sail_number));
    shell(`${intro('Startlister', 'Starttider og rækkefølge for omvendt respit.', admin() ? '<button class="primary" data-action="new-race">Indstil bane</button>' : '')}<div class="filters">${selectors(state.planning.course)}${races.length ? `<select id="start-race" aria-label="Vælg startliste">${raceOptions(races, state.planning.startRaceId)}</select>` : ''}</div>${race ? `<div class="workspace"><section class="panel"><div class="panel-head"><div><h2>${e(race.label)} · Omvendt respit</h2><p>${date(race.start)} · ${number(race.distance, 3)} sm · ${e(race.rating_key === 'import' ? 'Importerede målebrevstal' : race.rating_key)}</p></div><button class="small" data-action="print">Udskriv ↗</button></div>${!race.published ? '<div class="notice info">Foreløbig startliste · sejladsen er endnu ikke offentliggjort.</div>' : ''}<div class="table-scroll"><table><thead><tr><th>Nr.</th><th>Starttid</th><th>Båd / skipper</th><th>Sejlnr.</th><th>Løb</th><th class="numeric">Hdcp.</th><th class="numeric">Ekstra handicap</th></tr></thead><tbody>${entries.map((b, i) => `<tr><td>${i + 1}</td><td><strong>${b.start ? clock(b.start) : 'Afventer handicap'}</strong></td><td><div class="boatname">${e(b.name)}</div><div class="boatdetail">${e(b.skipper)}</div></td><td>${e(b.sail_number)}</td><td>${e(b.class_name)}</td><td class="numeric">${number(b.rating, 1)}</td><td class="numeric">+${number(b.extra_handicap)} s</td></tr>`).join('')}</tbody></table></div></section><aside class="side"><section class="panel"><h3>Banen</h3>${markMap(JSON.parse(race.route))}<p>${JSON.parse(race.route).length ? `J–X → ${JSON.parse(race.route).map(e).join(' → ')} → X` : 'Distancen er importeret fra Manage2Sail.'}</p><span class="distance">${number(race.distance, 3)} sm</span></section><section class="panel"><h3>DH og jagtstart</h3><p>Langsomste båd starter først. Startforskellene beregnes ud fra det valgte DH-tal og distancen. Ekstra handicap lægges til starttidspunktet.</p><p>Reference til præmietider: ${number(race.reference, 1)} s/sm.</p></section></aside></div>` : '<div class="panel empty">Der er ingen omvendt respit-sejladser på den valgte bane.</div>'}`);
}
export function dialogError(error) { $('.dialog-error', dialog).innerHTML = errorMarkup(error); }
export async function catalogDialog() { await openFleetCommander(); }
export async function heatDialog(id) { await openFleetCommander(id); }
export async function raceDialog(roundId = null, course = state.results.course) {
    roundId = roundId || state.session.roundId;
    const available = (state.session.data.rounds || []).filter(r => (!roundId || r.id === Number(roundId)) && r.courses.some(c => !c.race_id));
    if (!available.length) {
        state.session.page = 'series';
        await render();
        toast('Alle planlagte baner er indstillet. Tilføj ræs på seriens side.');
        return;
    }
    const planned = available.find(r => r.id === Number(roundId)) || available.find(r => r.courses.some(c => c.course === course && !c.race_id)) || available[0];
    if (planned.courses.find(c => c.course === course)?.race_id)
        course = planned.courses.find(c => !c.race_id).course;
    const sibling = state.session.data.races.find(r => planned.courses.some(c => c.race_id === r.id));
    const day = planned.race_date || (sibling ? timingDay(sibling.start) : '');
    const plannedTime = planned.courses.find(c => c.course === course)?.start_time || currentSeries()['default_start_' + course];
    runtime.route = [];
    openDialog('Indstil bane i et ræs', `<form id="race-form" class="stack"><div class="form-grid"><label class="field">Ræs<select id="race-round" name="round_id">${options(available.map(r => r.id), planned.id, available.map(r => r.label))}</select></label><label class="field">Bane<select name="course" id="race-course">${options(['stor', 'lille'], course, ['Stor bane', 'Lille bane'])}</select></label><label class="field">Startform<select name="mode">${options(['normal', 'pursuit'], 'normal', ['Almindelig respit', 'Jagtstart / omvendt respit'])}</select></label><label class="field">Dato (dd-mm-åååå)<input name="race_date" type="text" inputmode="numeric" maxlength="10" placeholder="DD-MM-ÅÅÅÅ" value="${danishDay(day)}" required></label><label class="field">Første start (HH:MM:SS)<input name="start_time" type="text" data-start-time inputmode="numeric" maxlength="8" pattern="[0-9]{2}:[0-9]{2}:[0-9]{2}" value="${e(plannedTime)}" required></label><label class="field">Beregningsmål · hele ræset<select name="handicap_system" id="race-system">${options(['circle', 'gph', 'updown'], planned.handicap_system || 'circle', ['Cirkelmål', 'GPH', 'Op/ned-mål'])}</select></label><label class="field">Vindkategori<select name="wind_category" id="race-wind"><option value="" disabled>Ikke relevant ved GPH</option>${options(['L', 'M', 'H'], 'M', ['Let', 'Mellem', 'Hård'])}</select></label><label class="field">Referencehandicap (s/sm)<input name="reference" type="text" inputmode="decimal" data-number="true" value="${currentSeries().reference}" required></label></div><div><h3>Rundingsmærker i rækkefølge</h3><p class="subtle">Start på linjen J–X. Mål ved X. Tryk på mærkerne, også ved gentagelser.</p><div class="row">${state.session.data.marks.filter(m => !['J', 'X'].includes(m.name)).map(m => `<button type="button" data-mark="${e(m.name)}">${e(m.name)}</button>`).join('')}<button type="button" class="small" data-action="route-undo">↶</button></div><div id="route-preview" class="route-builder">Vælg mindst ét rundingsmærke.</div><p id="route-distance" class="distance">– sm</p></div><div><h3>Løb og deltagere</h3><div id="race-boats"></div><p class="subtle">Deltagere tilføjes til løbene på siden Serie.</p></div><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Gem bane og gå til måltagning</button></div></form>`);
    await fillPlannedStart(false);
}
export function rememberRaceSetup() {
    const form = $('#race-form');
    if (!form?.dataset.roundId)
        return;
    const record = state.planning.raceDrafts[form.dataset.roundId] ||= { courses: {} };
    record.race_date = $('[name="race_date"]', form).value;
    record.handicap_system = $('#race-system').value;
    record.courses[form.dataset.course] = {
        ...Object.fromEntries(['start_time', 'mode', 'wind_category', 'reference'].map(name => [name, $(`[name="${name}"]`, form).value])),
        route: [...runtime.route]
    };
}
export function raceSetupSaved(form) {
    const record = state.planning.raceDrafts[form.dataset.roundId];
    if (record) {
        delete record.courses[form.dataset.course];
        if (!Object.keys(record.courses).length)
            delete state.planning.raceDrafts[form.dataset.roundId];
    }
    form.dataset.saved = 'true';
}
export async function fillPlannedStart(remember = true) {
    if (remember)
        rememberRaceSetup();
    const form = $('#race-form'), round = state.session.data.rounds.find(r => r.id === Number($('#race-round').value)), course = $('#race-course').value;
    const sibling = state.session.data.races.find(r => round.courses.some(c => c.race_id === r.id));
    const record = state.planning.raceDrafts[round.id], draft = record?.courses[course];
    form.dataset.roundId = String(round.id);
    form.dataset.course = course;
    const values = {
        race_date: record?.race_date ?? danishDay(round.race_date || (sibling ? timingDay(sibling.start) : '')),
        handicap_system: record?.handicap_system ?? round.handicap_system ?? 'circle',
        start_time: draft?.start_time ?? round.courses.find(c => c.course === course)?.start_time ?? currentSeries()['default_start_' + course],
        mode: draft?.mode ?? 'normal', wind_category: draft?.wind_category ?? 'M',
        reference: draft?.reference ?? currentSeries().reference
    };
    for (const [name, value] of Object.entries(values))
        $(`[name="${name}"]`, form).value = value;
    runtime.route = [...(draft?.route || [])];
    updateRaceBoats();
    await updateRoute();
}

dialog.addEventListener('close', () => {
    const form = $('#race-form');
    if (form && form.dataset.saved !== 'true')
        delete state.planning.raceDrafts[form.dataset.roundId];
});
export function updateRaceBoats() {
    const round = state.session.data.rounds.find(r => r.id === Number($('#race-round').value)), courseField = $('#race-course'), system = $('#race-system').value, wind = $('#race-wind');
    wind.disabled = system === 'gph';
    if (wind.disabled)
        wind.value = '';
    else if (!wind.value)
        wind.value = 'M';
    const key = system === 'gph' ? 'GPH' : (system === 'circle' ? 'TACI' : 'TAUD') + wind.value;
    for (const option of courseField.options)
        option.disabled = !!round.courses.find(c => c.course === option.value)?.race_id;
    if (courseField.selectedOptions[0]?.disabled)
        courseField.value = round.courses.find(c => !c.race_id)?.course;
    const child = round.courses.find(c => c.course === courseField.value);
    $('#race-boats').innerHTML = child.heats.map(heat => `<div class="race-fleet"><strong>${e(heat.name)}</strong><p>${heat.boat_ids.map(id => { const b = state.session.data.boats.find(b => b.id === id); return b ? e(b.name) + ((key === 'GPH' ? b.gph : b.ratings[key]) == null ? ` (${e(key)} mangler)` : '') : ''; }).filter(Boolean).join(', ') || 'Ingen tilmeldte både'}</p></div>`).join('');
}
export async function updateRoute() {
    const serial = ++runtime.requestSerial;
    $('#route-preview').innerHTML = runtime.route.length ? `<span>J–X → </span>${runtime.route.map((r, i) => `<button class="route-token" type="button" data-remove-mark="${i}" title="Fjern mærke">${e(r)} ×</button>`).join('')}<span> → X</span>` : 'Vælg mindst ét rundingsmærke.';
    if (!runtime.route.length) {
        $('#route-distance').textContent = '– sm';
        return;
    }
    const result = await api('/api/geometry', { route: runtime.route });
    if (serial === runtime.requestSerial)
        $('#route-distance').textContent = number(result.distance, 3) + ' sm';
}
export function referenceDialog() { openDialog('Referencehandicap', `<form id="reference-form" class="stack"><p class="subtle">Seriens værdi bruges ved oprettelse af nye sejladser. Du kan også ensrette alle eksisterende sejladser. På samme distance får alle både samme tidsforskydning, og stillingen bevares.</p><label class="field">Seriens reference (s/sm)<input type="text" inputmode="decimal" data-number="true" name="reference" min="0.1" step="0.1" value="${currentSeries().reference}" required></label><label class="check"><input type="checkbox" name="apply_all">Sæt også denne reference på alle sejladser i serien</label><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Gem seriens reference</button></div></form><hr><h3>Indstil én sejlads</h3><form id="race-reference-form" class="stack"><label class="field">Sejlads<select name="race_id">${raceOptions(state.session.data.races, state.session.data.races[0]?.id)}</select></label><label class="field">Reference (s/sm)<input name="reference" type="text" inputmode="decimal" data-number="true" step="0.1" min="0.1" required value="${state.session.data.races[0]?.reference || currentSeries().reference}"></label><button>Gem for denne sejlads</button></form>`); }
export async function historicalCalculationDialog(raceId = null) {
    if (!raceId)
        await refreshTiming();
    if (!raceId && state.timing.race.courses.length > 1) {
        openDialog('Vælg bane for historisk beregning', `<form id="course-action-form" class="stack"><label class="field">Bane<select name="race_id">${state.timing.race.courses.map(r => `<option value="${r.id}">${r.course === 'stor' ? 'Stor' : 'Lille'} bane · ${e(r.label)}</option>`).join('')}</select></label><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Åbn beregningsgrundlag</button></div></form>`);
        return;
    }
    state.planning.historicalRace = await api(`/api/races/${raceId || state.timing.timingRaceId}`);
    openDialog('Historisk beregningsgrundlag', `<form id="calculation-form" class="stack"><p><strong>${e(state.planning.historicalRace.race.label)} · ${state.planning.historicalRace.race.course === 'stor' ? 'Stor' : 'Lille'} bane</strong>. Genberegningen bruger sejladsens gemte deltagere, handicaptal, distancer og faktiske start- og måltider.</p><label class="field">Ret en deltagers historiske handicap<select id="calculation-boat" name="boat_id"><option value="">Bevar alle gemte handicaptal</option>${state.planning.historicalRace.entries.map(row => `<option value="${row.boat_id}">${e(row.name)} · ${e(row.sail_number)} · ${e(row.class_name)}</option>`).join('')}</select></label><div id="calculation-boat-fields"></div><label class="field">Årsag til genberegning<input name="note" required maxlength="500" placeholder="Fx fejl i målebrev anvendt ved R4"></label><p class="subtle">Nutidens stamdata og deltagelse bruges ikke som erstatning. Kun denne sejlads ændres og skal derefter offentliggøres igen. Dommertid skal beregnes på ny.</p><button type="button" data-action="historical-preview">Forhåndsvis genberegning</button><div id="calculation-preview"></div><input type="hidden" name="token" id="calculation-token"><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary" disabled>Genberegn kun denne sejlads</button></div></form>`);
}
export async function updateHistoricalBoat() {
    const row = state.planning.historicalRace.entries.find(r => r.boat_id === Number($('#calculation-boat').value));
    if (!row) {
        $('#calculation-boat-fields').innerHTML = '';
        return;
    }
    const sets = await api(`/api/boats/handicaps?id=${row.boat_id}`);
    if (Number($('#calculation-boat').value) !== row.boat_id)
        return;
    state.planning.historicalSets = sets;
    const saved = { ...row.handicap_set.ratings };
    saved[state.planning.historicalRace.race.rating_key] = row.rating;
    $('#calculation-boat-fields').innerHTML = `<label class="field">Bådens handicapsæt<select name="handicap_set_id" id="historical-set"><option value="">Nyt eller ændret sæt</option>${sets.map(set => `<option value="${set.id}" ${set.id === row.handicap_set_id ? 'selected' : ''}>Sæt #${set.id}${set.id === row.handicap_set_id ? ' · anvendt i dette ræs' : ''}</option>`).join('')}</select></label><p>Gemte værdier fra sejladsen · ${number(row.distance, 3)} sm · start ${clock(row.start)}. Tomme tal er ukendte, ikke nul.</p><div class="rating-grid">${keys.map(key => `<label class="field">${key}${key === state.planning.historicalRace.race.rating_key ? ' · anvendt' : ''}<input name="${key}" type="text" inputmode="decimal" data-number="true" value="${saved[key] ?? ''}" ${key === state.planning.historicalRace.race.rating_key || (state.planning.historicalRace.race.rating_key === 'GPH' && ['TACIL', 'TACIM', 'TACIH'].includes(key)) ? 'required' : ''}></label>`).join('')}</div>${keys.includes(state.planning.historicalRace.race.rating_key) || state.planning.historicalRace.race.rating_key === 'GPH' ? '' : `<label class="field">Anvendt Hdcp.<input name="rating" type="text" inputmode="decimal" data-number="true" value="${row.rating ?? ''}" required></label>`}<label class="field">Gemt ekstra handicap (sekunder)<input name="extra_handicap" type="text" inputmode="numeric" data-number="true" value="${row.extra_handicap}" required></label>`;
}
export function historicalData() {
    const form = $('#calculation-form'), data = Object.fromEntries(new FormData(form));
    return { ...data, boat_id: data.boat_id ? Number(data.boat_id) : null, ratings: Object.fromEntries(keys.filter(k => k in data).map(k => [k, data[k] || null])) };
}
export async function previewHistorical() {
    const form = $('#calculation-form');
    if (!form.reportValidity())
        return;
    const result = await api(`/api/races/${state.planning.historicalRace.race.id}/calculation-preview`, historicalData());
    $('#calculation-token').value = result.token;
    $('#calculation-preview').innerHTML = `<p>${e(result.notice)}</p><div class="table-scroll"><table><thead><tr><th>Båd</th><th>Hdcp. før → efter</th><th>Beregnet tid før → efter</th></tr></thead><tbody>${result.changes.map(row => `<tr><td>${e(row.name)}</td><td>${number(row.old_rating, 1)} → ${number(row.rating, 1)}</td><td>${seconds(row.old_corrected)} → ${row.status === 'DUTY' || row.status === 'RDG' ? 'Afventer dommertidsberegning' : seconds(row.corrected)}</td></tr>`).join('')}</tbody></table></div>`;
    $('button.primary', form).disabled = false;
}
export function daySettingsDialog() {
    runtime.route = [];
    const dates = [...new Set(state.session.data.races.filter(r => !state.session.roundId || state.session.data.rounds.find(p => p.id === state.session.roundId)?.courses.some(c => c.race_id === r.id)).map(r => r.start.slice(0, 10)))].sort().reverse();
    openDialog('Dagens distance og vindkategori', `<form id="day-settings-form" class="stack"><label class="field">Sejladsdag<select name="date">${options(dates, dates[0])}</select></label><label class="field">Vindkategori for alle løb på begge baner<select name="wind_category"><option value="">Behold eksisterende vindkategori</option><option value="L">Let vind</option><option value="M">Mellem vind</option><option value="H">Hård vind</option></select></label><p class="subtle">Ændring af vindkategori bruger bådenes DH-tal for den valgte vind. Forhåndsvisningen kontrollerer, om alle har det nødvendige tal.</p><label class="field">Distance ensrettes på denne bane<select name="course">${options(['stor', 'lille'], state.results.course, ['Stor bane', 'Lille bane'])}</select></label><h3>Vælg rundingsmærker for at sætte samme distance</h3><div class="row">${state.session.data.marks.filter(m => !['J', 'X'].includes(m.name)).map(m => `<button type="button" data-mark="${e(m.name)}">${e(m.name)}</button>`).join('')}<button type="button" data-action="route-undo">↶</button></div><div class="route-builder" id="route-preview">Ingen distanceændring, før der er valgt mærker.</div><div class="distance" id="route-distance">– sm</div><button type="button" data-action="day-preview">Forhåndsvis ændringer</button><div id="day-preview-result"></div><input id="day-token" name="token" type="hidden"><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Gennemfør for alle valgte løb</button></div></form>`);
    updateDayWind();
}
export function updateDayWind() {
    const form = $('#day-settings-form');
    if (!form)
        return;
    const races = state.session.data.races.filter(r => r.start.slice(0, 10) === $('[name="date"]', form).value), field = $('[name="wind_category"]', form);
    field.disabled = !!races.length && races.every(r => r.rating_key === 'GPH');
    field.options[0].textContent = field.disabled ? 'Ikke relevant ved GPH' : 'Behold eksisterende vindkategori';
    if (field.disabled)
        field.value = '';
    field.title = field.disabled ? 'Vindkategori er ikke relevant ved GPH' : '';
}
export function dayFormData(apply) { const data = Object.fromEntries(new FormData($('#day-settings-form'))); return { ...data, series_id: state.session.seriesId, round_uid: state.session.roundUid || undefined, route: runtime.route, apply }; }
