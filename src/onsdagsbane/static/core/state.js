/** Independent workspace stores; temporary UI work belongs in runtime. */
export const state = {
    session: {
        page: 'results', seriesId: null, data: null,
        roundUid: null, roundId: null, roundTab: null,
        offset: 0, connected: true
    },
    results: {
        course: 'stor', className: null, view: 'overall',
        raceId: null, draft: false, result: null
    },
    roundResults: {
        course: 'stor', className: null, view: 'daily',
        raceId: null, draft: true, result: null
    },
    timing: {
        query: '', selected: null, race: null,
        timingRoundId: null, timingRaceId: null
    },
    planning: {
        course: 'stor', startRaceId: null,
        historicalRace: null, historicalSets: [], raceDrafts: {}
    },
    boats: { boatSets: [], query: '', catalog: [] },
    assignments: null,
    assignmentsRoute: {
        heatId: null, scope: 'series', source: '',
        query: '', memberQuery: '', includeAssigned: false
    }
};

export const runtime = {
    busy: false,
    route: [],
    toastTimer: undefined,
    requestSerial: 0,
    tableFitFrame: undefined,
    timingSearchIndex: new Map(),
    timingRefresh: 0,
    clockSyncing: false
};

export function inTimingWorkspace() {
    return state.session.page === 'timing' ||
        (state.session.page === 'round' && state.session.roundTab === 'timing');
}
