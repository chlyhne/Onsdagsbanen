import { state, runtime } from './state.js';
import { $, dialog, staff, admin, toast } from './ui.js';
import { api, load } from './api.js';
import { showResults, restoreResultPreferences } from '../features/results.js';
import { showAdmin } from '../features/access.js';
import { showSeriesList, showSeriesHome, showRegistrationPage, showAssignmentsPage, showRegistry, showSeriesSettings, showRoundPage, todayRound } from '../features/workspaces.js';
export function routeUrl(page = state.session.page) {
    const id = state.session.seriesId;
    const series = { series: '', assignments: '/assignments', registrations: '/boats', settings: '/settings', 'series-results': '/results' };
    if (page in series)
        return `/series/${id}${series[page]}`;
    if (page === 'round')
        return `/rounds/${state.session.roundUid}/${state.session.roundTab || 'setup'}`;
    return ({ 'series-list': '/series', registry: '/boats', boats: '/boats', admin: '/admin', today: '/today', timing: '/today', starts: '/today' })[page] || '/results';
}
export async function readLocation() {
    const path = location.pathname, q = new URLSearchParams(location.search);
    Object.assign(state.session, { roundUid: null, roundId: null, roundTab: null });
    let match = path.match(/^\/series\/(\d+)(?:\/(assignments|boats|results|settings))?$/);
    if (match) {
        state.session.seriesId = Number(match[1]);
        state.session.page = ({ assignments: 'assignments', boats: 'registrations', results: 'series-results', settings: 'settings' })[match[2]] || 'series';
    }
    else if ((match = path.match(/^\/rounds\/([\w-]+)(?:\/(setup|starts|timing|results|history))?$/))) {
        Object.assign(state.session, { roundUid: match[1], roundTab: match[2] || 'setup', page: 'round' });
        if (staff()) {
            const w = await api(`/api/workspaces/rounds/${match[1]}`);
            state.session.seriesId = w.series.id;
            state.session.roundId = w.round.id;
        }
    }
    else
        state.session.page = ({ '/series': 'series-list', '/boats': 'registry', '/admin': 'admin', '/today': 'today' })[path] || 'results';
    if (['results', 'series-results'].includes(state.session.page))
        restoreResultPreferences();
    if (match && path.startsWith('/series/'))
        state.session.seriesId = Number(match[1]);
    state.assignmentsRoute = { heatId: q.has('heat') ? Number(q.get('heat')) : null, scope: q.get('scope') || 'series', source: q.get('source') || '', query: q.get('q') || '', memberQuery: q.get('members') || '', includeAssigned: q.get('include_assigned') === '1' };
    if (state.session.page === 'registry')
        state.boats.query = q.get('q') || '';
    if (state.session.page === 'round') {
        state.planning.course = q.get('course') || 'stor';
        state.roundResults.course = q.get('course') || 'stor';
        state.roundResults.className = q.get('class') === 'all' ? '' : q.get('class');
    }
    await load();
}
export async function navigate(url, { replace = false } = {}) {
    if (dialog.open)
        dialog.close();
    history[replace ? 'replaceState' : 'pushState']({}, '', url);
    await readLocation();
    await render('none');
}
export function rememberAssignments(w) {
    const s = { heatId: w.heat.id, scope: w.scope, source: w.source, query: w.query, memberQuery: w.memberQuery, includeAssigned: w.includeAssigned };
    state.assignmentsRoute = s;
    const q = new URLSearchParams({ heat: String(s.heatId), scope: s.scope });
    for (const [key, value] of [['source', s.source], ['q', s.query], ['members', s.memberQuery], ['include_assigned', s.includeAssigned ? '1' : '']])
        if (value)
            q.set(key, value);
    history.replaceState({}, '', `${routeUrl('assignments')}?${q}`);
}
export async function render(historyMode = 'replace') {
    try {
        if (!staff())
            state.session.page = 'results';
        if (!admin() && ['registry', 'assignments', 'registrations', 'settings', 'admin'].includes(state.session.page))
            state.session.page = 'series-list';
        if (['timing', 'starts', 'today'].includes(state.session.page)) {
            const r = state.session.page === 'today' ? todayRound() : state.session.data.rounds.find(r => r.id === state.timing.timingRoundId) || state.session.data.rounds.find(r => r.courses.some(c => c.race_id === state.timing.timingRaceId)) || todayRound();
            if (r)
                Object.assign(state.session, { roundUid: r.uid, roundId: r.id, roundTab: state.session.page === 'starts' ? 'starts' : 'timing', page: 'round' });
            else
                state.session.page = 'series';
        }
        if (state.session.page === 'boats')
            state.session.page = 'registry';
        if (historyMode !== 'none' && location.pathname !== routeUrl())
            history[historyMode === 'push' ? 'pushState' : 'replaceState']({}, '', routeUrl());
        const views = { 'series-list': showSeriesList, series: showSeriesHome, assignments: showAssignmentsPage, registrations: showRegistrationPage, registry: showRegistry, settings: showSeriesSettings, round: showRoundPage, admin: showAdmin };
        if (['results', 'series-results'].includes(state.session.page))
            await showResults(historyMode);
        else
            await (views[state.session.page] || showSeriesList)();
    }
    catch (error) {
        toast(error);
        $('#app main')?.insertAdjacentHTML('afterbegin', '<p class="notice">Siden kunne ikke indlæses. Prøv at genindlæse.</p>');
    }
}
export async function selectSeries(id) { await navigate(`/series/${Number(id)}`); }
export async function afterSaved(message, close = true) {
    if (close && dialog.open)
        dialog.close();
    await load();
    await render();
    toast(message);
}
export function enhancePage() { }
document.addEventListener('click', async (event) => {
    const a = event.target.closest('a[data-nav]');
    if (a && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) {
        event.preventDefault();
        try {
            await navigate(a.href);
        }
        catch (error) {
            toast(error);
        }
        return;
    }
    const b = event.target.closest('button');
    if (!b || b.disabled || runtime.busy)
        return;
    let url = null;
    if (b.dataset.page)
        url = routeUrl(({ boats: 'registry', timing: 'today' })[b.dataset.page] || b.dataset.page);
    if (b.dataset.selectSeries)
        url = `/series/${Number(b.dataset.selectSeries)}`;
    if (b.dataset.heat)
        url = `/series/${state.session.seriesId}/assignments?heat=${b.dataset.heat}&scope=series`;
    if (b.dataset.openTiming) {
        const r = state.session.data.rounds.find(r => r.courses.some(c => c.race_id === Number(b.dataset.openTiming)));
        if (r)
            url = `/rounds/${r.uid}/timing`;
    }
    if (b.dataset.action === 'catalog')
        url = routeUrl('assignments');
    if (b.dataset.action === 'edit-series')
        url = routeUrl('settings');
    if (url) {
        event.preventDefault();
        event.stopImmediatePropagation();
        try {
            await navigate(url);
        }
        catch (error) {
            toast(error);
        }
    }
}, true);
document.addEventListener('change', async (event) => {
    if (event.target.id === 'timing-race') {
        const r = state.session.data.rounds.find(r => r.id === Number(event.target.value));
        if (r) {
            event.stopImmediatePropagation();
            await navigate(`/rounds/${r.uid}/timing`);
        }
    }
}, true);
window.addEventListener('popstate', async () => {
    try {
        if (dialog.open)
            dialog.close();
        await readLocation();
        await render('none');
    }
    catch (error) {
        toast(error);
    }
});
