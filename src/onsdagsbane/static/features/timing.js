import { inTimingWorkspace } from '../core/state.js';
import { $, $$, admin, clock, courseMarks, date, e, intro, localInput, normalize, now, number, openDialog, options, roundingWarning, shell, staff, statusText, toast, uid } from '../core/ui.js';
import { api } from '../core/api.js';
import { showResults } from './results.js';
import { link, roundSelectors } from '../core/navigation.js';
import { runtime, state } from '../core/state.js';
export const timingWorkByRace = new Map();
export const timingNow = () => new Date(now().getTime() + Number(state.timing.race?.round.clock_offset || 0) * 1000);
function shiftTimingDrafts(offset) {
    for (const draft of Object.values(timingWork().drafts)) {
        const delta = offset - Number(draft.clockOffset || 0);
        if (!delta) continue;
        if (draft.automatic && draft.instant) {
            draft.instant = new Date(Date.parse(draft.instant) + delta * 1000).toISOString();
            delete draft.dateInput; delete draft.timeInput;
        }
        if (draft.originalAutomatic && draft.original?.instant)
            draft.original.instant = new Date(Date.parse(draft.original.instant) + delta * 1000).toISOString();
        draft.clockOffset = offset;
    }
    saveTimingWork();
}
function matchesOriginal(row, original) {
    return row.status === original.status && Number(row.penalty) === Number(original.penalty) &&
        ((!row.instant && !original.instant) || Date.parse(row.instant) === Date.parse(original.instant));
}
export async function synchronizeClock(action) {
    const work = timingWork();
    if (runtime.clockSyncing || timingLocked()) return;
    if (Object.values(work.drafts).some(d => d.saving)) {
        toast('Vent, til den aktuelle registrering er gemt, og synkronisér så uret.');
        return;
    }
    const previous = Number(state.timing.race.round.clock_offset || 0), instant = timingNow().getTime();
    const delta = action === 'clock-snap' ? (Math.round(instant / 60000) * 60000 - instant) / 1000 : action === 'clock-plus' ? 60 : -60;
    runtime.clockSyncing = true;
    const page = $('.timing-page');
    page.inert = true;
    try {
        const result = await api(`/api/rounds/${state.timing.timingRoundId}/clock`, { previous_offset: previous, offset: previous + delta });
        shiftTimingDrafts(result.offset);
        await refreshTiming();
        renderActiveTiming();
        toast('Uret er justeret. Automatisk tagne tider er korrigeret; manuelt indtastede tider er bevaret.');
    } catch (error) {
        try { await refreshTiming(); renderActiveTiming(); } catch {}
        toast(error);
    }
    finally { runtime.clockSyncing = false; if (page.isConnected) page.inert = false; }
}
export const timingDay = value => new Date(value).toLocaleDateString('sv-SE', { timeZone: 'Europe/Copenhagen' });
export function roundForRace(raceId) { return (state.session.data.rounds || []).find(round => round.courses.some(c => c.race_id === Number(raceId))); }
export function chooseTimingRound(rounds = state.session.data.rounds || [], races = state.session.data.races, when = now()) {
    const today = timingDay(when), items = rounds.map(round => { const ids = new Set(round.courses.map(c => c.race_id)); const children = races.filter(r => ids.has(r.id)); return { round, day: round.race_date || (children.length ? timingDay(children.reduce((a, b) => Date.parse(a.start) < Date.parse(b.start) ? a : b).start) : null) }; });
    const open = items.filter(item => !item.round.completed), ascending = (a, b) => (a.day || '9999').localeCompare(b.day || '9999') || a.round.position - b.round.position;
    return open.filter(item => item.day === today).sort((a, b) => b.round.position - a.round.position || b.round.id - a.round.id)[0]?.round || open.filter(item => item.day > today).sort(ascending)[0]?.round || open.filter(item => item.day && item.day < today).sort((a, b) => b.day.localeCompare(a.day) || a.round.position - b.round.position)[0]?.round || open.filter(item => !item.day).sort(ascending)[0]?.round || items.filter(item => item.day === today)[0]?.round || items.filter(item => item.day).sort((a, b) => b.day.localeCompare(a.day))[0]?.round || items[0]?.round;
}
export function timingStorageKey(roundId) { return `ob_timing_round:${state.session.seriesId}:${roundId}`; }
export function timingWork(roundId = state.timing.timingRoundId) {
    if (!timingWorkByRace.has(roundId)) {
        let saved = {};
        try {
            const raw = localStorage.getItem(timingStorageKey(roundId));
            if (raw)
                saved = JSON.parse(raw) || {};
            else {
                saved = { pins: [], drafts: {}, activeKey: null };
                const planned = (state.session.data.rounds || []).find(r => r.id === roundId);
                for (const course of planned?.courses || []) {
                    const old = JSON.parse(localStorage.getItem(`ob_timing:${state.session.seriesId}:${course.race_id}`) || 'null');
                    if (!old)
                        continue;
                    saved.pins.push(...(old.pins || []));
                    for (const [key, draft] of Object.entries(old.drafts || {}))
                        saved.drafts[key] = { ...draft, raceId: draft.boatId ? course.race_id : undefined };
                    saved.activeKey = saved.activeKey || old.activeKey;
                }
            }
        }
        catch { }
        const work = { pins: [...new Set(Array.isArray(saved.pins) ? saved.pins.filter(Number.isInteger) : [])], drafts: saved.drafts && typeof saved.drafts === 'object' && !Array.isArray(saved.drafts) ? saved.drafts : {}, activeKey: saved.activeKey || null };
        const retired = {};
        for (const [key, draft] of Object.entries(work.drafts)) {
            if (!draft.boatId) {
                retired[key] = draft;
                delete work.drafts[key];
                continue;
            }
            draft.saving = false;
            if (draft.capturing) delete draft.error;
            delete draft.capturing;
            delete draft.capture_id;
            delete draft.capture_request_id;
        }
        if (!work.drafts[work.activeKey]) work.activeKey = null;
        if (Object.keys(retired).length) {
            try {
                const key = timingStorageKey(roundId) + ':retired-loose';
                const previous = JSON.parse(localStorage.getItem(key) || '{}');
                localStorage.setItem(key, JSON.stringify({ ...previous, ...retired }));
            } catch {}
        }
        timingWorkByRace.set(roundId, work);
    }
    return timingWorkByRace.get(roundId);
}
export function saveTimingWork(roundId = state.timing.timingRoundId) {
    try {
        localStorage.setItem(timingStorageKey(roundId), JSON.stringify(timingWork(roundId)));
    }
    catch { }
}
export function activeTimingDraft() { const work = timingWork(); return work.drafts[work.activeKey] || null; }
export function timingCourseForBoat(boatId) { const row = state.timing.race.entries.find(r => r.boat_id === boatId); return state.timing.race.courses.find(r => r.id === row?.race_id) || {id:null, revision:row?.race_revision}; }
export function timingRevisions() { return Object.fromEntries(state.timing.race.courses.map(r => [String(r.id), r.revision])); }
export function timingLocked() { return !!(state.timing.race?.race.completed || state.timing.race?.race.published); }
export function buildTimingSearch() {
    runtime.timingSearchIndex = new Map(state.timing.race.entries.map(row => { const master = state.session.data.boats.find(b => b.id === row.boat_id) || {}; return [row.boat_id, normalize([master.name, master.skipper, master.sail_number, master.boat_type, master.club, master.class_name, master.course, JSON.stringify(master.ratings), master.extra_handicap, row.name, row.skipper, row.sail_number, row.boat_type, row.class_name, row.course, row.rating, statusText(row.status)].join(' '))]; }));
}
export function matchesTimingSearch(row) { const text = runtime.timingSearchIndex.get(row.boat_id) || ''; return normalize(state.timing.query.replace(/,/g, '.')).split(/\s+/).filter(Boolean).every(word => text.includes(word)); }
export function boatIdentity(row) { return `<div class="timing-identity"><strong>${e(row.name)}</strong> <span class="timing-sail">${e(row.sail_number)}</span><div>${e(row.skipper)} · ${row.course === 'stor' ? 'Stor' : 'Lille'} bane · ${e(row.class_name)}</div></div>`; }
export function pinIcon() { return '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="m16 3 5 5-4 2-3 5-2-2-6 6-1-1 6-6-2-2 5-3z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>'; }
export function searchBoats() { renderTimingLists(); }
function latestFinishFirst(a, b) {
    const timestamp = row => {
        const value = Date.parse(row.instant);
        return Number.isFinite(value) ? value : -Infinity;
    };
    // Compare full instants across both courses. Untimed statuses follow last;
    // ties and pursuit finishes without times retain their saved sequence.
    return timestamp(b) - timestamp(a) || a.sequence - b.sequence || a.boat_id - b.boat_id;
}
export function renderTimingLists() {
    if (!$('#unmeasured-boats') || !state.timing.race)
        return;
    const work = timingWork(), locked = timingLocked(), pins = new Set(work.pins);
    const remaining = state.timing.race.entries.filter(row => row.sequence === null);
    const measured = state.timing.race.entries.filter(row => row.sequence !== null).sort(latestFinishFirst);
    const pinned = remaining.filter(row => pins.has(row.boat_id)).sort((a, b) => work.pins.indexOf(a.boat_id) - work.pins.indexOf(b.boat_id));
    const visible = [...pinned, ...remaining.filter(row => !pins.has(row.boat_id) && matchesTimingSearch(row))];
    $('#unmeasured-count').textContent = visible.length === remaining.length ? remaining.length : `${visible.length} / ${remaining.length}`;
    const completed = measured.filter(matchesTimingSearch);
    $('#measured-count').textContent = completed.length === measured.length ? measured.length : `${completed.length} / ${measured.length}`;
    $('#unmeasured-boats').innerHTML = visible.map(row => {
        const draft = work.drafts[`boat:${row.boat_id}`];
        return `<div class="timing-boat ${pins.has(row.boat_id) ? 'is-pinned' : ''}" data-unmeasured="${row.boat_id}">${boatIdentity(row)}<div class="timing-row-actions"><button class="pin-button" data-pin="${row.boat_id}" aria-pressed="${pins.has(row.boat_id)}" aria-label="${pins.has(row.boat_id) ? 'Fjern pin fra' : 'Pin'} ${e(row.name)}" title="${pins.has(row.boat_id) ? 'Fjern pin' : 'Pin øverst, også under søgning'}">${pinIcon()}</button><button class="primary" data-measure="${row.boat_id}" ${locked ? 'disabled' : ''}>${draft ? 'Tid klar ' + clock(draft.instant) : 'Måltag'}</button></div></div>`;
    }).join('') || '<p class="timing-empty">Ingen både matcher søgningen.</p>';
    $('#measured-boats').innerHTML = completed.map(row => `<div class="timing-boat" data-measured="${row.boat_id}">${boatIdentity(row)}<div class="timing-row-actions"><strong class="measured-time ${row.status === 'DUTY' ? 'pending-duty' : ''}">${row.instant ? clock(row.instant) : row.status === 'FIN' ? 'Nr. ' + row.sequence : e(statusText(row.status))}</strong><button data-edit-finish="${row.boat_id}" ${locked ? 'disabled' : ''}>Ret</button>${row?.race_mode === 'pursuit' && !locked && row.status === 'FIN' ? `<button class="square-button" data-move="${row.boat_id}" data-direction="-1" aria-label="Flyt ${e(row.name)} op">↑</button><button class="square-button" data-move="${row.boat_id}" data-direction="1" aria-label="Flyt ${e(row.name)} ned">↓</button>` : ''}</div></div>`).join('') || '<p class="timing-empty">Ingen måltagne både matcher søgningen.</p>';
    const publish = $('[data-action="publish"]');
    if (publish)
        publish.disabled = Object.values(work.drafts).some(d => d.saving);
}
export function timingInputValues(draft) {
    const [day, time] = localInput(draft.instant || state.timing.race.race.start, true).split('T');
    return { date: draft.dateInput ?? day.split('-').reverse().join('-'), time: draft.timeInput ?? (draft.instant ? time.split('.')[0] : '') };
}
export function timingInputInstant(form) {
    const dateField = $('#finish-date', form), timeField = $('#finish-time', form);
    const match = /^(\d{2})-(\d{2})-(\d{4})$/.exec(dateField.value);
    let validDate = false, day = '';
    if (match) {
        const [, d, m, y] = match, check = new Date(Date.UTC(Number(y), Number(m) - 1, Number(d)));
        validDate = Number(y) >= 1000 && check.getUTCFullYear() === Number(y) && check.getUTCMonth() === Number(m) - 1 && check.getUTCDate() === Number(d);
        day = `${y}-${m}-${d}`;
    }
    dateField.setCustomValidity(dateField.value && !validDate ? 'Indtast en gyldig dato som DD-MM-ÅÅÅÅ.' : '');
    const validTime = /^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$/.test(timeField.value);
    timeField.setCustomValidity(timeField.value && !validTime ? 'Indtast et gyldigt klokkeslæt som HH:MM:SS.' : '');
    return validDate && validTime ? `${day}T${timeField.value}` : '';
}
export function renderActiveTiming() {
    const container = $('#active-timing');
    if (!container)
        return;
    const draft = activeTimingDraft();
    state.timing.selected = draft?.boatId || null;
    if (!draft?.boatId) {
        container.innerHTML = '';
        return;
    }
    const row = state.timing.race.entries.find(r => r.boat_id === draft.boatId);
    if (!row) { container.innerHTML = ''; return; }
    const locked = timingLocked(), disabled = locked || draft.saving, fields = timingInputValues(draft);
    container.innerHTML = `<section class="active-measurement" aria-label="Aktiv måltagning"><div class="active-heading"><h2>${e(row.name) + ' · ' + e(row.sail_number)}</h2><button class="square-button" data-timing-action="cancel" aria-label="Annuller aktiv måltagning" ${draft.saving ? 'disabled' : ''}>×</button></div>${row ? `<p>${e(row.skipper)} · ${row.course === 'stor' ? 'Stor' : 'Lille'} bane · ${e(row.class_name)}${draft.mode === 'edit' ? ' · Ret registrering' : ''}</p>` : ''}<form id="active-measure-form"><div class="active-time-row"><label class="field timing-time-field">Måltid (HH:MM:SS)<input id="finish-time" name="time" type="text" inputmode="numeric" autocomplete="off" maxlength="8" pattern="[0-9]{2}:[0-9]{2}:[0-9]{2}" placeholder="HH:MM:SS" value="${e(fields.time)}" ${disabled ? 'disabled' : ''} ${draft.status === 'FIN' ? 'required' : ''}></label><label class="field timing-date-field">Dato<input id="finish-date" name="date" type="text" data-calendar inputmode="numeric" autocomplete="off" maxlength="10" pattern="[0-9]{2}-[0-9]{2}-[0-9]{4}" placeholder="Vælg dato" value="${e(fields.date)}" ${disabled ? 'disabled' : ''} ${draft.status === 'FIN' ? 'required' : ''}></label>${row ? `<button type="button" data-timing-action="duty" ${disabled ? 'disabled' : ''}>Dommertjans</button>` : ''}<button class="primary" type="submit" ${disabled ? 'disabled' : ''}>${draft.saving ? 'Gemmer…' : 'Bekræft'}</button>${draft.mode === 'edit' ? `<button type="button" data-timing-action="return" ${disabled ? 'disabled' : ''}>Flyt tilbage til ikke måltaget</button>` : ''}${row?.race_mode === 'pursuit' && row && draft.mode === 'new' ? `<button type="button" data-timing-action="order" ${disabled ? 'disabled' : ''}>Kun rækkefølge</button>` : ''}</div><details class="timing-extra" ${draft.status !== 'FIN' || Number(draft.penalty) ? 'open' : ''}><summary>Status og straftid</summary><div class="row"><label class="field">Status<select name="status" id="finish-status" ${disabled ? 'disabled' : ''}>${options(['FIN', 'DNC', 'DNS', 'DNF', 'RET', 'DSQ', 'DNE', ...(staff() || draft.status === 'DUTY' ? ['DUTY'] : []), ...(draft.status === 'RDG' ? ['RDG'] : [])], draft.status, ['FIN', 'DNC', 'DNS', 'DNF', 'RET', 'DSQ', 'DNE', ...(staff() || draft.status === 'DUTY' ? ['Dommertjans – afventer beregning'] : []), ...(draft.status === 'RDG' ? ['RDG'] : [])])}</select></label><label class="field">Straftid (sekunder)<input id="finish-penalty" name="penalty" type="text" inputmode="decimal" data-number="true" value="${e(draft.penalty)}" ${disabled ? 'disabled' : ''}></label></div></details><p id="duty-notice" ${draft.status === 'DUTY' ? '' : 'hidden'}>Dommertjans registreres nu. Tiden beregnes først efter endt sejlads, når de øvrige deltageres resultater er indtastet.</p>${draft.error ? `<p class="error" role="alert">${e(draft.error)} Den indtastede tid er bevaret.</p>` : ''}</form></section>`;
}
export function activateTimingBoat(boatId, editing = false) {
    if (timingLocked())
        return;
    const work = timingWork(), row = state.timing.race.entries.find(r => r.boat_id === boatId);
    if (!row)
        return;
    const key = `boat:${boatId}`;
    let draft = work.drafts[key];
    if (!draft || draft.mode !== (editing ? 'edit' : 'new')) {
        draft = { key, instant: editing ? (row.instant || '') : timingNow().toISOString(), request_id: uid(), penalty: editing ? row.penalty : 0, status: editing ? row.status : 'FIN', automatic: editing ? row.clock_automatic : true, clockOffset: Number(state.timing.race.round.clock_offset || 0) };
        draft.boatId = boatId;
        draft.raceId = row.race_id;
        draft.mode = editing ? 'edit' : 'new';
        if (editing)
            draft.original = { instant: row.instant, status: row.status, penalty: row.penalty };
        if (editing) draft.originalAutomatic = row.clock_automatic;
        work.drafts[key] = draft;
    }
    work.activeKey = key;
    saveTimingWork();
    renderActiveTiming();
    renderTimingLists();
    $('#active-timing').scrollIntoView({ block: 'nearest' });
}
export function rememberActiveTiming() {
    const draft = activeTimingDraft(), form = $('#active-measure-form');
    if (!draft || !form || draft.saving)
        return;
    const before = JSON.stringify([draft.instant, draft.status, draft.penalty]);
    const raw = timingInputInstant(form), previous = draft.instant ? localInput(draft.instant).slice(0, 19) : '';
    draft.dateInput = $('#finish-date', form).value;
    draft.timeInput = $('#finish-time', form).value;
    // Show whole seconds, but retain the precise click-time until date or time is changed.
    if (raw !== previous || draft.manualTimeEdited)
        draft.instant = raw;
    delete draft.manualTimeEdited;
    draft.status = $('[name="status"]', form).value;
    draft.penalty = $('[name="penalty"]', form).value;
    $('#finish-date', form).required = draft.status === 'FIN';
    $('#finish-time', form).required = draft.status === 'FIN';
    $('#duty-notice', form).hidden = draft.status !== 'DUTY';
    $('#finish-penalty', form).disabled = draft.status !== 'FIN';
    if (draft.status !== 'FIN') {
        $('#finish-date', form).setCustomValidity('');
        $('#finish-time', form).setCustomValidity('');
    }
    if (before !== JSON.stringify([draft.instant, draft.status, draft.penalty]))
        draft.request_id = uid();
    delete draft.error;
    saveTimingWork();
}
export function removeTimingDraft(key, roundId = state.timing.timingRoundId) {
    const work = timingWork(roundId);
    delete work.drafts[key];
    if (work.activeKey === key)
        work.activeKey = null;
    saveTimingWork(roundId);
}
export async function refreshTiming(roundId = state.timing.timingRoundId) {
    const version = ++runtime.timingRefresh, detail = await api(`/api/rounds/${roundId}/timing`);
    if (version !== runtime.timingRefresh || !inTimingWorkspace() || state.timing.timingRoundId !== roundId)
        return;
    state.timing.race = detail;
    shiftTimingDrafts(Number(detail.round.clock_offset || 0));
    state.timing.timingRaceId = detail.race.id;
    buildTimingSearch();
    renderTimingLists();
    updateTimingContext();
}
export async function confirmActiveTiming(orderOnly = false) {
    rememberActiveTiming();
    const draft = activeTimingDraft();
    if (!draft || !draft.boatId || draft.saving)
        return;
    const form = $('#active-measure-form');
    if (!orderOnly && form && !form.reportValidity())
        return;
    const roundId = state.timing.timingRoundId, key = draft.key;
    const payload = { boat_id: draft.boatId, status: draft.status, penalty: draft.status === 'FIN' ? draft.penalty : 0, request_id: draft.request_id, automatic: !!draft.automatic, clock_offset: draft.clockOffset ?? Number(state.timing.race.round.clock_offset || 0) };
    if (draft.status === 'FIN' && !orderOnly) {
        payload.instant = draft.instant;
    }
    if (draft.mode === 'edit') {
        const row = state.timing.race.entries.find(r => r.boat_id === draft.boatId);
        if (!matchesOriginal(row, draft.original)) {
            draft.error = 'Bådens resultat er ændret. Åbn Ret igen og gennemgå den nye registrering.';
            renderActiveTiming();
            return;
        }
        payload.revision = timingCourseForBoat(draft.boatId).revision;
        payload.note = 'Rettet ved måltagning';
    }
    draft.saving = true;
    saveTimingWork();
    renderActiveTiming();
    renderTimingLists();
    try {
        await api(`/api/rounds/${roundId}/${draft.mode === 'edit' ? 'edit' : 'finish'}`, payload);
        const wasActive = timingWork(roundId).activeKey === key;
        removeTimingDraft(key, roundId);
        if (inTimingWorkspace() && state.timing.timingRoundId === roundId) {
            await refreshTiming(roundId);
            if (wasActive && !timingWork(roundId).activeKey) {
                renderActiveTiming();
                $('#boat-search')?.focus();
            }
            toast(payload.status === 'DUTY' ? 'Dommertjans er registreret. Tiden beregnes efter endt sejlads.' : 'Måltiden er bekræftet og gemt.');
        }
    }
    catch (error) {
        draft.saving = false;
        draft.error = error.message;
        if (error.handicapIssues?.length) toast(error);
        saveTimingWork(roundId);
        if (inTimingWorkspace() && state.timing.timingRoundId === roundId) {
            try {
                await refreshTiming(roundId);
            }
            catch { }
            const row = state.timing.race.entries.find(r => r.boat_id === draft.boatId);
            const same = row && row.sequence !== null && row.status === payload.status && Number(row.penalty) === Number(payload.penalty) && ((!row.instant && !payload.instant) || (row.instant && payload.instant && Date.parse(row.instant) === Date.parse(payload.instant)));
            if (same) {
                removeTimingDraft(key, roundId);
                renderActiveTiming();
                toast('Måltiden er bekræftet på serveren.');
            }
            else if (activeTimingDraft() === draft)
                renderActiveTiming();
            renderTimingLists();
        }
    }
}
export async function returnActiveTiming() {
    const draft = activeTimingDraft();
    if (!draft || draft.mode !== 'edit' || draft.saving)
        return;
    const row = state.timing.race.entries.find(r => r.boat_id === draft.boatId);
    if (!matchesOriginal(row, draft.original)) {
        draft.error = 'Bådens resultat er ændret. Åbn Ret igen før den flyttes tilbage.';
        renderActiveTiming();
        return;
    }
    draft.saving = true;
    renderActiveTiming();
    try {
        await api(`/api/rounds/${state.timing.timingRoundId}/undo`, { boat_id: draft.boatId, revision: timingCourseForBoat(draft.boatId).revision });
        removeTimingDraft(draft.key);
        await refreshTiming();
        renderActiveTiming();
        toast('Båden er flyttet tilbage til ikke måltaget.');
    }
    catch (error) {
        draft.saving = false;
        try {
            await refreshTiming();
        }
        catch { }
        const current = state.timing.race.entries.find(r => r.boat_id === draft.boatId);
        if (current?.sequence === null) {
            removeTimingDraft(draft.key);
            renderActiveTiming();
            toast('Båden er tilbage i ikke måltaget.');
        }
        else {
            draft.error = error.message;
            saveTimingWork();
            renderActiveTiming();
        }
    }
}
export function updateTimingContext() {
    const context = $('#timing-context');
    if (!context)
        return;
    const planned = state.session.data.rounds.find(r => r.id === state.timing.timingRoundId);
    context.innerHTML = planned.courses.map(course => {
        const race = state.timing.race.courses.find(r => r.course === course.course);
        return `<span data-timing-course="${course.course}"><strong>${course.course === 'stor' ? 'Stor' : 'Lille'} bane:</strong> ${race ? `${courseMarks(race.rounding_marks)} · ${number(race.distance,3)} sm · start ${clock(race.start)} · ${race.mode === 'pursuit' ? 'Jagtstart' : 'Almindelig respit'}` : 'Ikke indstillet'}</span>`;
    }).join('');
    const warning = $('#timing-rounding-warning'), markup = roundingWarning(state.timing.race.rounding_conflicts);
    if (warning.innerHTML !== markup) warning.innerHTML = markup;
    warning.hidden = !markup;
    const locked = timingLocked();
    for (const button of $$('[data-timing-action^="clock-"]'))
        button.disabled = locked;
    const finish = $('[data-action="complete-round"],[data-action="reopen-round"]');
    if (finish) {
        finish.dataset.action = locked ? 'reopen-round' : 'complete-round';
        finish.textContent = locked ? 'Genåbn ræs' : 'Afslut ræs';
    }
    const status = $('#timing-status');
    status.textContent = state.timing.race.race.completed ? 'Ræset er afsluttet for begge baner. Resterende både har fået DNC.' : (state.timing.race.unconfigured_courses.length ? 'Tider gemmes nu. Indstil banen her inden afslutning; beregnede tider vises derefter.' : '');
    status.hidden = !status.textContent;
}
export async function showTiming() {
    if (!staff()) {
        state.session.page = 'results';
        return showResults();
    }
    if (!state.timing.timingRoundId && state.timing.timingRaceId)
        state.timing.timingRoundId = roundForRace(state.timing.timingRaceId)?.id;
    if (!(state.session.data.rounds || []).some(r => r.id === state.timing.timingRoundId))
        state.timing.timingRoundId = chooseTimingRound()?.id;
    if (!state.timing.timingRoundId) {
        shell(`${intro('Måltagning', '')}${roundSelectors()}<p class="empty">Der er ingen ræs i denne serie.</p>`);
        return;
    }
    state.timing.race = await api(`/api/rounds/${state.timing.timingRoundId}/timing`);
    shiftTimingDrafts(Number(state.timing.race.round.clock_offset || 0));
    state.timing.timingRaceId = state.timing.race.race.id;
    buildTimingSearch();
    const race = state.timing.race.race, locked = timingLocked();
    const resultCourse = state.timing.race.courses.find(c => c.course === state.results.course) || state.timing.race.courses[0];
    const resultURL = resultCourse ? '/results?' + new URLSearchParams({ series: String(state.session.seriesId), course: resultCourse.course, class: 'all', view: 'daily', race: String(resultCourse.id), ...(!race.published ? { drafts: '1' } : {}) }) : null;
    shell(`<section class="timing-page">
      <div class="timing-header"><h1>Måltagning</h1><div class="row">
        ${admin() ? `<button data-configure-round="${state.timing.timingRoundId}" data-configure-course="stor">Indstil baner</button>` : ''}
        <button data-action="${locked ? 'reopen-round' : 'complete-round'}">${locked ? 'Genåbn ræs' : 'Afslut ræs'}</button>
        ${!race.published ? '<button data-action="publish">Offentliggør resultater →</button>' : ''}
        ${resultURL ? link(resultURL, 'Se resultater') : ''}
      </div></div>
      <div class="timing-toolbar">${roundSelectors()}
        <div class="time-tools"><strong id="live-clock">${clock(timingNow())}</strong>
          <button data-timing-action="clock-snap" ${locked ? 'disabled' : ''}>Synkronisér ur</button>
          <button data-timing-action="clock-minus" aria-label="Stil uret ét minut tilbage" ${locked ? 'disabled' : ''}>−1 minut</button>
          <button data-timing-action="clock-plus" aria-label="Stil uret ét minut frem" ${locked ? 'disabled' : ''}>+1 minut</button>
          <button type="button" class="clock-help" data-action="clock-help" aria-label="Hjælp til uret" aria-haspopup="dialog" title="Hjælp til uret">?</button>
          <span id="connection">Forbundet</span>
        </div>
      </div>
      <div id="timing-context" class="timing-context"></div><div id="timing-rounding-warning" hidden></div><p id="timing-status" hidden></p>
      <div id="active-timing"></div>
      <label class="field timing-search-label">Søg i begge lister<input id="boat-search" class="search-input" type="search" placeholder="Sejlnummer, bådnavn, skipper…" autocomplete="off" value="${e(state.timing.query)}"></label>
      <div class="timing-lists"><section><h2>Ikke måltaget <span id="unmeasured-count"></span></h2><div id="unmeasured-boats"></div></section>
      <section><h2>Måltaget / registreret <span id="measured-count"></span></h2><div id="measured-boats"></div></section></div>
    </section>`);
    updateTimingContext();
    renderActiveTiming();
    renderTimingLists();
}
export async function completionDialog(reopen = false) {
    if (!reopen && Object.keys(timingWork().drafts).length) {
        toast('Bekræft eller annullér de aktive tider først.');
        return;
    }
    await refreshTiming();
    if (!reopen && (!state.timing.race.courses.length || state.timing.race.unconfigured_courses.length)) {
        toast('Indstil banerne med tilmeldte både, før ræset afsluttes. Brug Indstil baner ovenfor.');
        return;
    }
    const missing = state.timing.race.entries.filter(row => row.sequence === null);
    openDialog(reopen ? 'Genåbn ræset for begge baner' : 'Afslut ræset for begge baner', `<form id="round-completion-form" data-reopen="${reopen}" class="stack"><p><strong>${e(state.timing.race.race.label)} · stor og lille bane</strong></p>${reopen ? '<p>Automatiske DNC-registreringer fjernes, så bådene igen kan måltages. Faktiske måltider og manuelt registrerede statusser bevares. Offentliggjorte resultater bliver kladder igen.</p>' : `<p>${missing.length} ikke-måltagne både får DNC. Gemte tider bevares. Dommertider beregnes automatisk ved afslutningen.</p>${missing.length ? `<p>${missing.map(row => e(row.name) + ' (' + (row.course === 'stor' ? 'stor' : 'lille') + ' bane)').join(', ')}</p>` : ''}`}<div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">${reopen ? 'Genåbn ræs' : 'Afslut ræs'}</button></div></form>`);
}
export async function redressDialog() {
    await refreshTiming();
    const candidates = state.timing.race.entries.filter(row => row.status === 'DUTY');
    openDialog('Dommertid efter sejladsen', `<form id="redress-form" class="stack"><p>Dommertiden beregnes ud fra de øvrige deltageres tider. Markér gerne dommertjans under måltagningen. Selve beregningen sker først efter endt sejlads, når de øvrige både har en måltid eller en afsluttende status, fx DNC eller DNF.</p><label class="field">Båd med dommertjans<select id="redress-boat" name="boat_id" required>${candidates.length ? candidates.map(row => `<option value="${row.boat_id}">${e(row.name)} · ${e(row.sail_number)}</option>`).join('') : '<option value="">Ingen både afventer dommertid</option>'}</select></label><p id="redress-readiness" role="status"></p><input type="hidden" name="request_id" value="${uid()}"><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Beregn dommertid</button></div></form>`);
    updateRedressReadiness();
}
export function updateRedressReadiness() {
    const form = $('#redress-form');
    if (!form)
        return;
    const boatId = Number($('#redress-boat', form).value), target = state.timing.race.entries.find(r => r.boat_id === boatId), entries = state.timing.race.entries.filter(r => r.race_id === target?.race_id);
    const missing = entries.filter(row => row.boat_id !== boatId && row.sequence === null);
    const timed = entries.filter(row => row.status === 'FIN' && row.corrected !== null);
    let reason = '';
    if (!boatId)
        reason = 'Markér først båden med status Dommertjans under måltagningen.';
    else if (Object.keys(timingWork().drafts).length)
        reason = 'Bekræft eller annullér de aktive tider før beregning af dommertid.';
    else if (!state.timing.race.race.completed)
        reason = 'Afslut først ræset for begge baner. Dommertiden beregnes derefter.';
    else if (missing.length)
        reason = 'Sejladsen er ikke færdigregistreret. Mangler måltid eller status for: ' + missing.map(row => row.name).join(', ') + '.';
    else if (entries.some(row => row.status === 'FIN' && row.corrected === null))
        reason = 'Alle målgangsbåde skal have tider for at beregne dommertid. Rækkefølgen alene er ikke nok.';
    else if (timed.length < 2)
        reason = 'Beregningen kræver mindst to tidsmålte både og brugbar historik.';
    $('#redress-readiness', form).textContent = reason || 'Alle øvrige deltagere er registreret. Dommertiden kan nu beregnes ud fra deres tider og bådens historik.';
    $('button.primary', form).disabled = !!reason;
}
export function publishDialog() {
    const race = state.timing.race.race;
    if (!race.published && state.timing.race.entries.some(row => row.status === 'DUTY')) {
        toast('Afslut ræset først. Dommertid beregnes automatisk ved afslutningen.');
        return;
    }
    if (!race.published && Object.keys(timingWork().drafts).length) {
        toast('Bekræft eller annullér de aktive tider før offentliggørelse.');
        return;
    }
    const missing = state.timing.race.entries.filter(r => r.sequence === null);
    openDialog(race.published ? 'Åbn resultater for rettelser' : 'Offentliggør resultater', `<form id="publish-form" class="stack"><p>${e(race.label)} · ${date(race.start)} · Stor og lille bane</p><p class="subtle">${race.published ? 'Sejladsen bliver en kladde og vises igen offentligt, når resultaterne offentliggøres på ny.' : 'Resultaterne bliver synlige for alle, og indtastningen låses indtil sejladsen åbnes for rettelser.'}</p>${!race.published && missing.length ? `<div class="notice">${missing.length} både har ingen registrering: ${missing.map(b => e(b.name)).join(', ')}.</div><label class="check"><input type="checkbox" name="confirm_dnc" required>Jeg bekræfter, at disse både skal have DNC</label>` : ''}<div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">${race.published ? 'Åbn for rettelser' : 'Offentliggør'}</button></div></form>`);
}
export function editFinishDialog(boatId) { activateTimingBoat(boatId, true); }
