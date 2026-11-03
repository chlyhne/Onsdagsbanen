import { state, runtime } from './state.js';
import { $, dialog, staff, admin, toast } from './ui.js';
import { api, load } from './api.js';
import { showResults, restoreResultPreferences } from '../features/results.js';
import { showAdmin } from '../features/access.js';
import { timingURL } from './navigation.js';
import { showTiming } from '../features/timing.js';
import { showSeriesList, showAssignmentsPage, showRegistry, showSeriesSettings, showRoundPage, todayRound } from '../features/workspaces.js';
export function routeUrl(page = state.session.page) {
    const id = state.session.seriesId;
    const series = { series: '', assignments: '/assignments', registrations: '', settings: '' };
    if (page in series)
        return `/series/${id}${series[page]}`;
    if (page === 'round' && state.session.roundTab === 'timing')
        return timingURL(state.session.seriesId, state.session.roundUid);
    if (page === 'round')
        return `/rounds/${state.session.roundUid}/${state.session.roundTab || 'setup'}`;
    if (['today', 'timing', 'starts'].includes(page)) return timingURL();
    return ({ 'series-list': '/series', registry: '/boats', boats: '/boats', admin: '/admin' })[page] || '/results';
}
async function canonicalResultLocation() {
    const url = new URL(location.href), params = url.searchParams;
    const series = url.pathname.match(/^\/series\/(\d+)\/results$/);
    const race = url.pathname.match(/^\/rounds\/([\w-]+)\/results$/);
    if (!series && !race) return;
    if (series) params.set('series', series[1]);
    else {
        let data = state.session.data;
        let round = data.rounds.find(r => r.uid === race[1]);
        if (!round && staff()) {
            const workspace = await api(`/api/workspaces/rounds/${race[1]}`);
            data = await api(`/api/state?series_id=${workspace.series.id}`);
            round = data.rounds.find(r => r.uid === race[1]);
        }
        if (!round && !staff()) {
            for (const candidate of data.series) {
                const other = await api(`/api/state?series_id=${candidate.id}`);
                round = other.rounds.find(r => r.uid === race[1]);
                if (round) { data = other; break; }
            }
        }
        if (!round) throw new Error('Ræset findes ikke.');
        const course = round.courses.find(c => c.course === params.get('course')) || round.courses.find(c => c.race_id) || round.courses[0];
        params.set('series', String(data.series_id));
        params.set('course', course.course);
        params.set('view', 'daily');
        if (!params.has('class')) params.set('class', 'all');
        if (course.race_id) params.set('race', String(course.race_id));
        if (staff() && !params.has('drafts')) params.set('drafts', '1');
    }
    if (!staff()) params.delete('drafts');
    url.pathname = '/results';
    history.replaceState({}, '', url);
}
export async function readLocation() {
    // Older bookmarks resolve to the single results page with their selection.
    await canonicalResultLocation();
    const path = location.pathname, q = new URLSearchParams(location.search);
    Object.assign(state.session, { roundUid: null, roundId: null, roundTab: null });
    let match = path.match(/^\/series\/(\d+)(?:\/(assignments|boats|settings))?$/);
    if (match) {
        state.session.seriesId = Number(match[1]);
        state.session.page = match[2] === 'assignments' ? 'assignments' : 'settings';
    }
    else if ((match = path.match(/^\/rounds\/([\w-]+)(?:\/(setup|starts|timing|results|history))?$/))) {
        Object.assign(state.session, { roundUid: match[1], roundTab: match[2] || 'setup', page: 'round' });
        if (staff()) {
            const w = await api(`/api/workspaces/rounds/${match[1]}`);
            state.session.seriesId = w.series.id;
            state.session.roundId = w.round.id;
        }
    }
    else {
        state.session.page = ({ '/series': 'series-list', '/boats': 'registry', '/admin': 'admin', '/timing': 'timing', '/today': 'timing' })[path] || 'results';
        if (state.session.page === 'timing')
            state.session.seriesId = /^\d+$/.test(q.get('series')) ? Number(q.get('series')) : state.timing.seriesId || state.session.seriesId;
    }
    if (state.session.page === 'results')
        restoreResultPreferences();
    if (match && path.startsWith('/series/'))
        state.session.seriesId = Number(match[1]);
    state.assignmentsRoute = { heatId: q.has('heat') ? Number(q.get('heat')) : null, scope: q.get('scope') || 'series', source: q.get('source') || '', query: q.get('q') || '', memberQuery: q.get('members') || '', includeAssigned: q.get('include_assigned') === '1' };
    if (state.session.page === 'registry') {
        state.boats.query = q.get('q') || '';
        state.boats.usage = ['all', 'used', 'unused'].includes(q.get('usage')) ? q.get('usage') : 'all';
    }
    if (state.session.page === 'round') {
        state.planning.course = q.get('course') || 'stor';
    }
    await load();
    if (state.session.page === 'settings' && location.pathname !== `/series/${state.session.seriesId}`)
        history.replaceState({}, '', `/series/${state.session.seriesId}`);
    if (staff() && (state.session.page === 'timing' || state.session.page === 'round')) {
        const isTiming = state.session.page === 'timing' || state.session.roundTab === 'timing';
        const round = state.session.page === 'round' ? state.session.data.rounds.find(r => r.uid === state.session.roundUid) : state.session.data.rounds.find(r => r.uid === q.get('round')) || todayRound();
        if (state.timing.timingRoundId !== round?.id) {
            state.timing.query = '';
            state.timing.selected = null;
            state.timing.timingRaceId = null;
            state.timing.race = null;
        }
        state.timing.seriesId = state.session.seriesId;
        state.timing.roundUid = round?.uid || null;
        state.timing.timingRoundId = round?.id || null;
        if (round)
            Object.assign(state.session, { page: 'round', roundUid: round.uid, roundId: round.id, roundTab: isTiming ? 'timing' : state.session.roundTab });
        if (isTiming)
            history.replaceState({}, '', timingURL(state.session.seriesId, round?.uid));
    }
}
export async function navigate(url, { replace = false } = {}) {
    if (dialog.open)
        dialog.close();
    const previous = $('#app main');
    if (previous) previous.inert = true;
    try {
        history[replace ? 'replaceState' : 'pushState']({}, '', url);
        await readLocation();
        await render('none');
    } finally {
        if (previous?.isConnected) previous.inert = false;
    }
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
        if (['series', 'registrations'].includes(state.session.page)) state.session.page = 'settings';
        if (!admin() && ['registry', 'assignments', 'admin'].includes(state.session.page))
            state.session.page = 'series-list';
        if (['timing', 'starts', 'today'].includes(state.session.page)) {
            const r = state.session.page === 'today' ? todayRound() : state.session.data.rounds.find(r => r.id === state.timing.timingRoundId) || state.session.data.rounds.find(r => r.courses.some(c => c.race_id === state.timing.timingRaceId)) || todayRound();
            if (r)
                Object.assign(state.session, { roundUid: r.uid, roundId: r.id, roundTab: state.session.page === 'starts' ? 'starts' : 'timing', page: 'round' });
            else
                state.session.page = 'timing';
        }
        if (state.session.page === 'boats')
            state.session.page = 'registry';
        if (historyMode !== 'none' && (routeUrl().includes('?') ? location.pathname + location.search : location.pathname) !== routeUrl())
            history[historyMode === 'push' ? 'pushState' : 'replaceState']({}, '', routeUrl());
        const views = { 'series-list': showSeriesList, assignments: showAssignmentsPage, registry: showRegistry, settings: showSeriesSettings, round: showRoundPage, timing: showTiming, admin: showAdmin };
        if (state.session.page === 'results')
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
    if (event.target.id === 'timing-series') {
        event.stopImmediatePropagation();
        try { await navigate(timingURL(Number(event.target.value), null)); }
        catch (error) { toast(error); }
        return;
    }
    if (event.target.id === 'timing-race') {
        const r = state.session.data.rounds.find(r => r.id === Number(event.target.value));
        if (r) {
            event.stopImmediatePropagation();
            try {
                await navigate(state.session.roundTab && state.session.roundTab !== 'timing' ? `/rounds/${r.uid}/${state.session.roundTab}` : timingURL(state.session.seriesId, r.uid));
            } catch (error) { toast(error); }
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
