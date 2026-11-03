import { editBoatProfile } from '../features/boats.js';
function profileFromRegistration(id) { return state.session.data.boats.find(b => b.id === id)?.profile_id; }
import { inTimingWorkspace } from './state.js';
import { resultState, resultURL } from '../features/results.js';
import { routeUrl } from './router.js';
import { $, clock, dialog, e, keys, normalizeNumberInput, now, number, openDialog, toast, uid } from './ui.js';
import { activateTimingBoat, activeTimingDraft, chooseTimingRound, completionDialog, confirmActiveTiming, editFinishDialog, publishDialog, redressDialog, refreshTiming, rememberActiveTiming, removeTimingDraft, renderActiveTiming, renderTimingLists, returnActiveTiming, roundForRace, saveTimingWork, searchBoats, showTiming, synchronizeClock, timingNow, timingCourseForBoat, timingRevisions, timingWork, updateRedressReadiness } from '../features/timing.js';
import { api, load } from './api.js';
import { discardsInput, isoDay, normalizeStartTime, roundStartsDialog, roundsDialog, seriesDialog, setRaceToday } from '../features/series.js';
import { catalogDialog, dayFormData, daySettingsDialog, dialogError, fillPlannedStart, heatDialog, historicalData, previewHistorical, raceDialog, saveRaceSetup, scheduleRaceSetup, closeRaceSetup, referenceDialog, showStarts, updateDayWind, updateHistoricalBoat, updateRaceBoats, updateRoute } from '../features/race.js';
import { afterSaved, navigate, render, selectSeries } from './router.js';
import { copyResultLink, showResults } from '../features/results.js';
import { showLogin } from '../features/access.js';
import { runtime, state } from './state.js';
document.addEventListener('input', event => {
    if (event.target.closest('#active-measure-form')) {
        if (['finish-time', 'finish-date'].includes(event.target.id) && activeTimingDraft()) {
            const draft = activeTimingDraft();
            draft.automatic = false;
            draft.manualTimeEdited = true;
            draft.request_id = uid();
        }
        if (event.target.matches('[data-number]'))
            normalizeNumberInput(event.target);
        if (event.target.id === 'finish-time')
            event.target.value = event.target.value.replace(/[.,]/g, ':');
        if (event.target.id === 'finish-date')
            event.target.value = event.target.value.replace(/[./]/g, '-');
        rememberActiveTiming();
    }
}, true);
document.addEventListener('submit', event => {
    if (event.target.id !== 'active-measure-form')
        return;
    event.preventDefault();
    event.stopImmediatePropagation();
    confirmActiveTiming();
}, true);
document.addEventListener('click', async (event) => {
    const button = event.target.closest('button');
    if (!button || button.disabled || runtime.clockSyncing || !(state.session.page === 'timing' || (state.session.page === 'round' && state.session.roundTab === 'timing')))
        return;
    const action = button.dataset.timingAction;
    if (!action && !button.dataset.pin && !button.dataset.measure)
        return;
    event.preventDefault();
    event.stopImmediatePropagation();
    try {
        if (button.dataset.pin) {
            const id = Number(button.dataset.pin), work = timingWork();
            work.pins = work.pins.includes(id) ? work.pins.filter(value => value !== id) : [...work.pins, id];
            saveTimingWork();
            renderTimingLists();
            return;
        }
        if (button.dataset.measure) {
            activateTimingBoat(Number(button.dataset.measure));
            return;
        }
        if (action?.startsWith('clock-'))
            await synchronizeClock(action);
        else if (action === 'cancel') {
            const draft = activeTimingDraft();
            if (draft && !draft.saving) {
                removeTimingDraft(draft.key);
                renderActiveTiming();
                renderTimingLists();
            }
        }
        else if (action === 'return')
            await returnActiveTiming();
        else if (action === 'order')
            await confirmActiveTiming(true);
        else if (action === 'duty') {
            $('#finish-status').value = 'DUTY';
            rememberActiveTiming();
            await confirmActiveTiming();
        }
    }
    catch (error) {
        const draft = activeTimingDraft();
        if (draft) {
            draft.error = error.message;
            saveTimingWork();
            renderActiveTiming();
        }
        else
            toast(error);
    }
}, true);
document.addEventListener('input', ev => {
    if (ev.target.matches('[data-number]'))
        normalizeNumberInput(ev.target);
    if (keys.includes(ev.target.name) || ev.target.name === 'extra_handicap') {
        const selector = ev.target.closest('#boat-form') ? $('#boat-set') : ev.target.closest('#calculation-form') ? $('#historical-set') : null;
        if (selector)
            selector.value = '';
    }
    if (ev.target.matches('[data-start-time]'))
        normalizeStartTime(ev.target);
    if (ev.target.closest('#calculation-form') && ev.target.id !== 'calculation-token') {
        $('#calculation-token').value = '';
        $('#calculation-form button.primary').disabled = true;
    }
    if (ev.target.closest('#day-settings-form') && ev.target.id !== 'day-token')
        $('#day-token').value = '';
    if (ev.target.closest('#race-form')) {
        if (ev.target.name === 'distance') { runtime.route = []; updateRoute(); }
        scheduleRaceSetup();
    }
    if (ev.target.id === 'boat-search') {
        state.timing.query = ev.target.value;
        searchBoats();
    }
    ;
    ;
});
document.addEventListener('change', async (ev) => {
    if (runtime.busy)
        return;
    try {
        const t = ev.target;
        if (t.closest('#race-form')) {
            if (['race-round', 'race-course'].includes(t.id)) {
                const form = $('#race-form');
                form.inert = true;
                const saving = saveRaceSetup();
                const controls = [...form.querySelectorAll('input,select,button')].map(control => [control, control.disabled]);
                for (const [control] of controls) control.disabled = true;
                let switched = false;
                try {
                    if (!await saving) {
                        $('#race-round').value = form.dataset.roundId;
                        $('#race-course').value = form.dataset.course;
                        return;
                    }
                    for (const [control, disabled] of controls) control.disabled = disabled;
                    await fillPlannedStart(false);
                    switched = true;
                } finally {
                    if (!switched)
                        for (const [control, disabled] of controls) control.disabled = disabled;
                    form.inert = false;
                }
            } else {
                updateRaceBoats();
                scheduleRaceSetup();
            }
            return;
        }
        if (t.name === 'date' && t.closest('#day-settings-form')) {
            updateDayWind();
            return;
        }
        if (t.dataset.roundSystem) {
            const round = state.session.data.rounds.find(r => r.id === Number(t.dataset.roundSystem));
            try {
                await api('/api/rounds/handicap-system', { round_id: round.id, handicap_system: t.value });
                await load();
                await render();
            }
            catch (error) {
                t.value = round.handicap_system || 'circle';
                throw error;
            }
            return;
        }
        if (t.id === 'boat-set' || t.id === 'historical-set') {
            const set = (t.id === 'boat-set' ? state.boats.boatSets : state.planning.historicalSets).find(set => set.id === Number(t.value));
            if (set) {
                const form = t.form;
                for (const key of keys)
                    $(`[name="${key}"]`, form).value = set.ratings[key] ?? '';
                $('[name="extra_handicap"]', form).value = set.extra_handicap;
                {
                    $('#calculation-token').value = '';
                    $('#calculation-form button.primary').disabled = true;
                }
            }
            return;
        }
        if (t.id === 'calculation-boat') {
            await updateHistoricalBoat();
            $('#calculation-token').value = '';
            $('#calculation-form button.primary').disabled = true;
            return;
        }
        ;
        if (t.id === 'redress-boat') {
            updateRedressReadiness();
            return;
        }
        if (t.id === 'result-series') {
            state.session.seriesId = Number(t.value);
            resultState().raceId = null;
            await navigate(resultURL());
        }
        else if (t.id === 'class-select') {
            const previousCourse = resultState().course;
            if (t.value.endsWith(':all')) {
                resultState().course = t.value.split(':')[0];
                resultState().className = '';
            }
            else {
                const course = t.selectedOptions[0]?.dataset.course;
                if (!course)
                    return;
                resultState().course = course;
                resultState().className = t.value;
            }
            if (previousCourse !== resultState().course)
                resultState().raceId = null;
            await showResults('push');
        }
        else if (t.id === 'result-view') {
            resultState().view = t.value;
            await showResults('push');
        }
        else if (t.id === 'draft-toggle') {
            resultState().draft = t.checked;
            await showResults();
        }
        else if (t.id === 'result-race') {
            resultState().raceId = Number(t.value);
            await showResults('push');
        }
        else if (t.id === 'start-race') {
            state.planning.startRaceId = Number(t.value);
            await showStarts();
        }
        else if (t.name === 'race_id' && t.closest('#race-reference-form'))
            $('[name="reference"]', t.form).value = state.session.data.races.find(r => r.id === Number(t.value)).reference;
    }
    catch (error) {
        toast(error);
    }
});
document.addEventListener('click', async (ev) => {
    const target = ev.target.closest('button,[data-page]');
    if (!target || target.disabled || runtime.busy || target.closest('#fleet-commander-form') || target.matches('[data-profile-edit],[data-profile-delete],[data-profile-history]'))
        return;
    try {
        if (target.dataset.roundStarts) {
            await roundStartsDialog(Number(target.dataset.roundStarts));
            return;
        }
        if (target.dataset.selectSeries) {
            await selectSeries(target.dataset.selectSeries);
            return;
        }
        if (target.dataset.heat) {
            await heatDialog(Number(target.dataset.heat));
            return;
        }
        if (target.dataset.configureRound) {
            await raceDialog(Number(target.dataset.configureRound), target.dataset.configureCourse);
            return;
        }
        if (target.dataset.openTiming) {
            state.timing.timingRaceId = Number(target.dataset.openTiming);
            state.timing.timingRoundId = roundForRace(state.timing.timingRaceId)?.id;
            state.session.page = 'timing';
            await render();
            return;
        }
        if (target.dataset.page) {
            state.session.page = target.dataset.page;
            state.timing.selected = null;
            if (inTimingWorkspace()) {
                await load();
                state.timing.timingRoundId = chooseTimingRound()?.id;
                state.timing.query = '';
            }
            await render();
            return;
        }
        if (target.dataset.course && state.session.page === 'round' && state.session.roundTab === 'starts') {
            state.planning.course = target.dataset.course;
            history.replaceState({}, '', routeUrl() + '?course=' + target.dataset.course);
            await render();
            return;
        }
        if (target.dataset.course) {
            resultState().course = target.dataset.course;
            resultState().className = null;
            resultState().raceId = null;
            await render('push');
            return;
        }
        if (target.dataset.view) {
            resultState().view = target.dataset.view;
            await showResults();
            return;
        }
        if (target.dataset.boat) {
            state.timing.selected = Number(target.dataset.boat);
            await showTiming();
            $('#finish-time')?.focus();
            return;
        }
        if (target.dataset.mark) {
            runtime.route.push(target.dataset.mark);
            if ($('#race-form')) scheduleRaceSetup();
            await updateRoute();
            return;
        }
        if (target.dataset.removeMark) {
            runtime.route.splice(Number(target.dataset.removeMark), 1);
            if ($('#race-form')) scheduleRaceSetup();
            await updateRoute();
            return;
        }
        if (target.dataset.editBoat) {
            await editBoatProfile(profileFromRegistration(Number(target.dataset.editBoat)));
            return;
        }
        if (target.dataset.editFinish) {
            editFinishDialog(Number(target.dataset.editFinish));
            return;
        }
        const action = target.dataset.action;
        if (action === 'race-today') {
            setRaceToday(target);
            return;
        }
        if (action === 'copy-link') {
            await copyResultLink();
            return;
        }
        if (action === 'clock-help') {
            openDialog('Hjælp til uret', '<p>Synkronisér ved minuttets slag i tårnet. Automatisk tagne tider korrigeres også; manuelt indtastede tider bevares.</p><div class="dialog-actions"><button type="button" data-action="close-dialog">Luk</button></div>');
            return;
        }
        if (action === 'login') {
            showLogin();
            return;
        }
        if (action === 'close-dialog') {
            if ($('#race-form')) { await closeRaceSetup(); return; }
            dialog.close();
            return;
        }
        if (action === 'new-race') {
            await raceDialog(inTimingWorkspace() ? state.timing.timingRoundId : null);
            return;
        }
        if (action === 'catalog') {
            await catalogDialog();
            return;
        }
        if (action === 'new-boat') {
            await editBoatProfile();
            return;
        }
        if (action === 'new-series') {
            seriesDialog();
            return;
        }
        if (action === 'new-rounds') {
            roundsDialog();
            return;
        }
        if (action === 'reference') {
            referenceDialog();
            return;
        }
        if (action === 'day-settings') {
            daySettingsDialog();
            return;
        }
        if (action === 'route-undo') {
            runtime.route.pop();
            if ($('#race-form')) scheduleRaceSetup();
            await updateRoute();
            return;
        }
        if (action === 'print') {
            window.print();
            return;
        }
        if (action === 'pdf') {
            window.location.href = `/api/pdf?series_id=${state.session.seriesId}&course=${resultState().course}&class_name=${encodeURIComponent(resultState().className)}${resultState().view === 'daily' && resultState().raceId ? '&race_id=' + resultState().raceId : ''}`;
            return;
        }
        if (action === 'historical-preview') {
            await previewHistorical();
            return;
        }
        if (action === 'redress-dialog') {
            await redressDialog();
            return;
        }
        if (action === 'complete-round') {
            await completionDialog();
            return;
        }
        if (action === 'reopen-round') {
            await completionDialog(true);
            return;
        }
        if (action === 'publish') {
            publishDialog();
            return;
        }
        if (runtime.busy)
            return;
        runtime.busy = true;
        target.disabled = true;
        if (action === 'logout') {
            await api('/api/logout', {});
            state.session.page = 'results';
            resultState().draft = false;
            await afterSaved('Du er logget ud.');
        }
        else if (target.dataset.undo) {
            await api(`/api/races/${state.timing.timingRaceId}/undo`, { boat_id: Number(target.dataset.undo), revision: state.timing.race.race.revision });
            await afterSaved('Registreringen er fortrudt. Den kan findes i ændringshistorikken.');
        }
        else if (target.dataset.move) {
            const selectedCourse = timingCourseForBoat(Number(target.dataset.move));
            const order = state.timing.race.entries.filter(r => r.race_id === selectedCourse.id && r.status === 'FIN' && r.sequence !== null).sort((a, b) => a.sequence - b.sequence).map(r => r.boat_id);
            const i = order.indexOf(Number(target.dataset.move)), j = i + Number(target.dataset.direction);
            if (j >= 0 && j < order.length) {
                [order[i], order[j]] = [order[j], order[i]];
                await api(`/api/races/${selectedCourse.id}/reorder`, { boat_ids: order, revision: selectedCourse.revision });
                await afterSaved('Målgangsrækkefølgen er rettet.');
            }
        }
        else if (target.dataset.user) {
            await api('/api/users', { id: Number(target.dataset.user), role: $(`#user-role-${target.dataset.user}`).value, approved: target.dataset.approved === 'true' });
            await afterSaved('Brugerens adgang er opdateret.');
        }
        else if (action === 'day-preview') {
            const result = await api('/api/day-settings', dayFormData(false));
            $('#day-token').value = result.token;
            $('#day-preview-result').innerHTML = `<p class="notice">${e(result.notice)}</p>${result.changes.map(r => `<p class="subtle">${e(r.label)} · ${r.course === 'stor' ? 'Stor' : 'Lille'} bane · ${r.updates.length} både · ${e(r.rating_key)} · ${[...new Set(r.updates.map(u => number(u.distance, 3)))].join(', ')} sm</p>`).join('')}`;
        }
        else
            ;
    }
    catch (error) {
        if (dialog.open)
            dialogError(error);
        else
            toast(error);
        if (inTimingWorkspace()) {
            try {
                const selection = state.timing.selected;
                await showTiming();
                state.timing.selected = selection;
            }
            catch { }
        }
    }
    finally {
        runtime.busy = false;
        if (target.isConnected)
            target.disabled = false;
    }
});
document.addEventListener('submit', async (ev) => {
    if (['fleet-commander-form', 'boat-profile-form', 'boat-delete-form', 'boat-history-form'].includes(ev.target.id))
        return;
    ev.preventDefault();
    if (runtime.busy)
        return;
    runtime.busy = true;
    const form = ev.target, submit = $('button.primary', form);
    if (submit)
        submit.disabled = true;
    try {
        const data = Object.fromEntries(new FormData(form));
        if (form.id === 'catalog-form') {
            await api('/api/boats/enroll', { ...data, series_id: state.session.seriesId, profile_ids: new FormData(form).getAll('profile_ids').map(Number) });
            await afterSaved('Bådene er tilmeldt serien fra den fælles bådliste.');
        }
        else if (form.id === 'round-starts-form') {
            await api('/api/rounds/starts', { round_id: Number(data.round_id), race_date: isoDay(data.race_date), start_times: { stor: data.start_stor, lille: data.start_lille } });
            await afterSaved('Ræsets dato og starttider er gemt.');
        }
        else if (form.id === 'round-completion-form') {
            const finished = await api(`/api/rounds/${state.timing.timingRoundId}/${form.dataset.reopen === 'true' ? 'reopen' : 'complete'}`, { revisions: timingRevisions() });
            await afterSaved(form.dataset.reopen === 'true' ? 'Ræset er genåbnet for begge baner.' : `Ræset er afsluttet for begge baner. ${finished.redress?.length || 0} dommertider er beregnet. Resterende både har fået DNC.`);
        }
        else if (form.id === 'series-settings-form') {
            await api('/api/series/settings', { ...data, series_id: state.session.seriesId, ...(data.year ? { year: Number(data.year) } : {}), discards: discardsInput(data.discards) });
            await afterSaved('Indstillingerne for den valgte serie er gemt.');
        }
        else if (form.id === 'calculation-form') {
            await api(`/api/races/${state.planning.historicalRace.race.id}/recalculate`, historicalData());
            await afterSaved('Kun den valgte sejlads er genberegnet ud fra det historiske grundlag. Gennemgå og offentliggør igen.');
        }
        else if (form.id === 'rounds-form') {
            await api('/api/rounds', { ...data, series_id: state.session.seriesId, start_times: { stor: data.start_stor, lille: data.start_lille } });
            state.session.page = 'series';
            await afterSaved('Ræs med begge baner og løb er tilføjet.');
        }
        else if (form.id === 'redress-form') {
            if (Object.keys(timingWork().drafts).length)
                throw new Error('Bekræft eller annullér de aktive tider før beregning af dommertid.');
            await api(`/api/races/${timingCourseForBoat(Number(data.boat_id)).id}/redress`, { boat_id: Number(data.boat_id), request_id: data.request_id });
            await afterSaved('Dommertiden er beregnet ud fra de registrerede tider.');
        }
        else if (form.id === 'day-settings-form') {
            const result = await api('/api/day-settings', dayFormData(true));
            await afterSaved('Dagens opsætning er ensrettet. Resultaterne skal gennemgås og offentliggøres igen.');
        }
        else if (form.id === 'reference-form') {
            await api('/api/series/reference', { series_id: state.session.seriesId, reference: data.reference, apply_all: !!data.apply_all, revisions: Object.fromEntries(state.session.data.races.map(r => [String(r.id), r.revision])) });
            await afterSaved('Referencehandicappet er opdateret.');
        }
        else if (form.id === 'race-reference-form') {
            const race = state.session.data.races.find(r => r.id === Number(data.race_id));
            await api('/api/race-reference', { ...data, race_id: race.id, revision: race.revision });
            await afterSaved('Sejladsens reference er opdateret.');
        }
        else if (form.id === 'series-form') {
            const created = await api('/api/series', { ...data, year: Number(data.year), fleets: { stor: data.stor_fleets, lille: data.lille_fleets }, discards: data.discards.trim() ? data.discards.split(',').map(s => Number(s.trim())) : [] });
            state.session.seriesId = created.id;
            state.session.page = 'series';
            state.timing.timingRaceId = null;
            state.timing.timingRoundId = null;
            state.planning.startRaceId = null;
            await afterSaved('Serien med ræs, baner og løb er oprettet. Tilføj både til løbene.');
        }
        else if (form.id === 'publish-form') {
            await api(`/api/rounds/${state.timing.timingRoundId}/${state.timing.race.race.published ? 'reopen' : 'publish'}`, { revisions: timingRevisions(), confirm_dnc: !!data.confirm_dnc });
            await afterSaved('Sejladsens offentliggørelse er opdateret.');
        }
        else if (form.id === 'edit-finish-form') {
            await api(`/api/races/${state.timing.timingRaceId}/edit`, { ...data, boat_id: Number(form.dataset.boatId), revision: state.timing.race.race.revision, request_id: uid() });
            await afterSaved('Rettelsen er gemt i historikken.');
        }
    }
    catch (error) {
        dialogError(error);
    }
    finally {
        runtime.busy = false;
        if (submit?.isConnected)
            submit.disabled = false;
    }
});
setInterval(() => {
    const live = $('#live-clock');
    if (live)
        live.textContent = clock(timingNow());
}, 250);
setInterval(async () => {
    if (!(state.session.page === 'timing' || (state.session.page === 'round' && state.session.roundTab === 'timing')) || !state.timing.timingRoundId || runtime.busy || runtime.clockSyncing || dialog.open)
        return;
    try {
        const before = Date.now(), fresh = await api(`/api/state?series_id=${state.session.seriesId}`);
        state.session.offset = new Date(fresh.server_time).getTime() - (before + Date.now()) / 2;
        state.session.connected = true;
        const badge = $('#connection');
        if (badge) {
            badge.textContent = 'Forbundet';
            badge.className = 'pill dot';
        }
        const latest = fresh.races.find(r => r.id === state.timing.timingRaceId);
        state.session.data = fresh;
        await refreshTiming();
    }
    catch {
        state.session.connected = false;
        const badge = $('#connection');
        if (badge) {
            badge.textContent = 'Ingen forbindelse';
            badge.className = 'pill amber';
        }
    }
}, 10000);
document.addEventListener('blur', event => {
    if (event.target.matches('[data-start-time]'))
        normalizeStartTime(event.target, true);
}, true);
