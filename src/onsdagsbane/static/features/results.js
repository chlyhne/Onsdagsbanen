import { $, admin, classOptions, courseMarks, date, e, number, openDialog, options, raceOptions, seconds, shell, staff, statusText, toast } from '../core/ui.js';
import { api } from '../core/api.js';
import { runtime, state } from '../core/state.js';
export const resultPreferenceCookie = 'ob_results';
export function restoreResultPreferences() {
    let saved = {};
    try {
        const raw = document.cookie.split('; ').find(row => row.startsWith(resultPreferenceCookie + '='));
        const parsed = raw ? JSON.parse(decodeURIComponent(raw.slice(resultPreferenceCookie.length + 1))) : {};
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed))
            saved = parsed;
    }
    catch { }
    const params = new URLSearchParams(location.search);
    const input = { ...saved };
    for (const key of ['series', 'course', 'class', 'view', 'race'])
        if (params.has(key))
            input[key] = params.get(key);
    const identity = value => /^\d+$/.test(String(value)) && Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : null;
    state.session.seriesId = identity(input.series) || undefined;
    state.results.course = ['stor', 'lille'].includes(input.course) ? input.course : 'stor';
    state.results.className = input.class === 'all' ? '' : typeof input.class === 'string' && input.class.length <= 160 ? input.class : null;
    state.results.view = ['daily', 'overall'].includes(input.view) ? input.view : 'overall';
    state.results.raceId = identity(input.race);
    state.results.draft = params.get('drafts') === '1';
}
export function resultURL(publicLink = false) {
    const url = new URL('/results', location.origin);
    url.search = new URLSearchParams({ series: String(state.session.seriesId), course: resultState().course, class: resultState().className || 'all', view: resultState().view, ...(resultState().raceId ? { race: String(resultState().raceId) } : {}), ...(!publicLink && resultState().draft ? { drafts: '1' } : {}) }).toString();
    return url;
}
export function rememberResults(historyMode = 'replace') {
    const url = resultURL();
    if (url.href !== location.href)
        history[historyMode === 'push' ? 'pushState' : 'replaceState']({}, '', url);
    const saved = Object.fromEntries(url.searchParams);
    delete saved.drafts;
    document.cookie = resultPreferenceCookie + '=' + encodeURIComponent(JSON.stringify(saved)) + '; Path=/; Max-Age=31536000; SameSite=Lax' + (location.protocol === 'https:' ? '; Secure' : '');
}
export async function copyResultLink() {
    const url = resultURL(true).href;
    try {
        await navigator.clipboard.writeText(url);
        toast('Linket er kopieret.');
    }
    catch {
        openDialog('Del resultater', `<label class="field">Kopiér dette link<input id="share-url" type="url" value="${e(url)}" readonly></label>`);
        $('#share-url').select();
    }
}
export function resultClassOptions() {
    return ['stor', 'lille'].map(course => {
        const classes = classOptions(course);
        return `<optgroup label="${course === 'stor' ? 'Stor bane' : 'Lille bane'}">${classes.map(name => `<option data-course="${course}" value="${e(name)}" ${resultState().course === course && resultState().className === name ? 'selected' : ''}>${e(name)}</option>`).join('')}<option value="${course}:all" ${resultState().course === course && resultState().className === '' ? 'selected' : ''}>${course === 'stor' ? 'Stor' : 'Lille'} bane · alle løb</option></optgroup>`;
    }).join('');
}
export async function showResults(historyMode = 'replace') {
    if (!staff()) resultState().draft = false;
    const classes = classOptions(resultState().course);
    if (resultState().className === null || (resultState().className !== '' && !classes.includes(resultState().className)))
        resultState().className = classes[0] || '';
    const id = ++runtime.requestSerial;
    const data = await api(`/api/results?series_id=${state.session.seriesId}&course=${resultState().course}&class_name=${encodeURIComponent(resultState().className)}${resultState().draft ? '&drafts=1' : ''}`);
    if (id !== runtime.requestSerial || state.session.page !== 'results')
        return;
    resultState().result = data;
    const races = data.races;
    if (!races.some(r => r.id === resultState().raceId))
        resultState().raceId = races.at(-1)?.id || null;
    const chosen = races.find(r => r.id === resultState().raceId);
    rememberResults(historyMode === 'none' ? 'replace' : historyMode);
    let table = '', conditions = '';
    if (resultState().view === 'overall') {
        table = `<table class="standings"><caption class="visually-hidden">Samlet pointstilling · ${e(resultState().className || resultState().course + ' bane · alle løb')}</caption><thead><tr><th scope="col" class="rank">Pl.</th><th scope="col">Bådnavn</th><th scope="col">Skipper</th>${resultState().className ? '' : '<th scope="col">Løb</th>'}<th scope="col">Sejlnummer</th>${races.map(r => `<th scope="col" class="numeric" title="${e(r.label)} · ${date(r.start)}">${e(r.label)}</th>`).join('')}<th scope="col" class="numeric">I alt</th></tr></thead><tbody>${data.overall.map(r => `<tr><td class="rank">${r.rank}</td><td class="boatname">${e(r.name)}</td><td class="skipper">${e(r.skipper)}</td>${resultState().className ? '' : `<td>${e(r.class_name)}</td>`}<td class="sail">${e(r.sail_number)}</td>${r.scores.map(score => `<td class="numeric ${score.discarded ? 'discarded' : ''}" title="${e(statusText(score.status))}${score.discarded ? ' · fratrukket' : ''}">${number(score.points, 1)}${score.status === 'RDG' ? '*' : ''}</td>`).join('')}<td class="numeric total">${number(r.total, 1)}</td></tr>`).join('')}</tbody></table>`;
    }
    else if (chosen) {
        const dayRows = data.daily[chosen.id] || [];
        const distances = [...new Set(dayRows.map(row => row.distance).filter(value => value != null))].sort((a, b) => a - b);
        if (!distances.length && chosen.distance != null)
            distances.push(chosen.distance);
        const windCategories = [...new Set(dayRows.map(row => row.wind_category))];
        if (!windCategories.length)
            windCategories.push(chosen.rating_key?.slice(-1));
        const windNames = { L: 'Let', M: 'Mellem', H: 'Hård' };
        const marks = chosen.rounding_marks || [];
        const courseLetters = courseMarks(marks);
        conditions = `<p class="race-conditions"><span><strong>Bane:</strong> ${courseLetters}</span><span><strong>Distance:</strong> ${distances.length ? distances.map(value => number(value, 3) + ' sm').join(' / ') : 'Ikke angivet'}</span><span><strong>Vindkategori:</strong> ${chosen.rating_key === 'GPH' ? 'Ikke relevant (GPH)' : windCategories.map(value => windNames[value] || 'Ikke angivet').join(' / ')}</span></p>`;
        const hasExtra = dayRows.some(row => row.extra_handicap);
        const hasPenalty = dayRows.some(row => row.penalty);
        table = `<table class="daily-results"><caption class="visually-hidden">Dagens tider · ${e(resultState().className || resultState().course + ' bane · alle løb')} · ${e(chosen.label)}</caption><thead><tr><th scope="col" class="rank">Pl.</th><th scope="col">Bådnavn</th><th scope="col">Skipper</th>${resultState().className ? '' : '<th scope="col">Løb</th>'}<th scope="col" class="numeric">Beregnet tid</th><th scope="col" class="numeric points" aria-label="Point" title="Point">Point</th><th scope="col" class="numeric">Sejltid</th><th scope="col">Sejlnr.</th><th scope="col" class="numeric">Hdcp.</th>${hasExtra ? '<th scope="col" class="numeric">Ekstra handicap</th>' : ''}${hasPenalty ? '<th scope="col" class="numeric">Straftid</th>' : ''}</tr></thead><tbody>${dayRows.map(r => `<tr><td class="rank">${r.rank || '–'}</td><td class="boatname">${e(r.name)}${r.status === 'RDG' ? ' *' : ''}</td><td class="skipper">${e(r.skipper)}</td>${resultState().className ? '' : `<td>${e(r.class_name)}</td>`}<td class="numeric">${r.status === 'FIN' || r.status === 'RDG' ? seconds(r.corrected) : e(statusText(r.status))}</td><td class="numeric total points">${number(r.points, 1)}</td><td class="numeric">${seconds(r.elapsed)}</td><td class="sail">${e(r.sail_number)}</td><td class="numeric">${number(r.rating, 1)}</td>${hasExtra ? `<td class="numeric">${r.extra_handicap ? '+' + number(r.extra_handicap) + ' s' : '–'}</td>` : ''}${hasPenalty ? `<td class="numeric">${r.penalty ? '+' + number(r.penalty) + ' s' : '–'}</td>` : ''}</tr>`).join('')}</tbody></table>`;
    }
    const setupWarnings = (resultState().view === 'daily' ? (chosen?.warning ? [chosen] : []) : races.filter(r => r.warning));
    const warnings = [data.reference_warning ? `<p>Serien bruger forskellige referencehandicap: ${data.references.map(v => number(v, 1)).join(', ')} s/sm.${admin() ? ' <button class="quiet-button" data-action="reference">Indstil reference</button>' : ''}</p>` : '', ...setupWarnings.map(r => `<p>${e(r.label)} · ${date(r.start)}: ${e(r.warning)}</p>`)];
    shell(`<section class="results-page"><div class="results-heading"><h1>Resultater</h1><div class="row"><button class="small" data-action="pdf">PDF</button><button class="small" data-action="copy-link" ${resultState().draft ? 'disabled title="Kladder skal offentliggøres før deling"' : ''}>Kopiér link</button></div></div><div class="results-controls"><label class="field">Serie<select id="result-series" aria-label="Serie">${options(state.session.data.series.map(s => s.id), state.session.seriesId, state.session.data.series.map(s => s.name))}</select></label><label class="field">Løb<select id="class-select">${resultClassOptions()}</select></label><label class="field">Vis<select id="result-view">${options(['overall', 'daily'], resultState().view, ['Samlet pointstilling', 'Tider for dagen'])}</select></label>${resultState().view === 'daily' && races.length ? `<label class="field">Sejlads<select id="result-race">${raceOptions([...races].reverse(), resultState().raceId)}</select></label>` : ''}${staff() ? `<label class="check"><input id="draft-toggle" type="checkbox" ${resultState().draft ? 'checked' : ''}>Vis kladder</label>` : ''}</div>${warnings.some(Boolean) ? `<details class="results-warning"><summary>Advarsel om baneopsætning</summary>${warnings.join('')}</details>` : ''}${conditions}<div class="panel results-table">${races.length ? table : '<p class="empty">Ingen offentliggjorte resultater i dette løb endnu.</p>'}</div>${races.length ? `<p class="results-note">${resultState().view === 'overall' ? 'Overstreget = fratrukket. ' : ''}* = dommertjans-redress.${data.source_scoring ? ' Point og fratræk fra Manage2Sail.' : ''}${resultState().view === 'daily' && chosen ? ` Reference: ${number(chosen.reference, 1)} s/sm · ${chosen.mode === 'pursuit' ? 'jagtstart' : 'almindelig respit'}.` : ''}</p>` : ''}</section>`);
}
export function resultState() { return state.results; }
