import { workspaceNavigation, link, timingURL } from './navigation.js';
import { runtime, state } from './state.js';
export const $ = (s, root = document) => root.querySelector(s);
export const $$ = (s, root = document) => [...root.querySelectorAll(s)];
export const escapeHTML = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const e = escapeHTML;
export function courseMarks(marks = []) {
    return marks.length ? `<span class="course-sequence">${marks.map(mark => `<span class="course-mark course-${mark.side === 'starboard' ? 'starboard' : 'port'}" title="${e(mark.name)}: ${mark.side === 'starboard' ? 'styrbord (grøn)' : 'bagbord (rød)'}" aria-label="${e(mark.name)}: ${mark.side === 'starboard' ? 'styrbord, grøn' : 'bagbord, rød'}">${e(mark.name)}</span>`).join('')}</span>` : 'N/A';
}
export function roundingWarning(conflicts = []) {
    const sides = values => values.map(side => side === 'starboard' ? 'styrbord' : 'bagbord').join(' / ');
    return conflicts.length ? `<div class="notice course-rounding-warning" role="alert"><strong>Advarsel: Stor og lille bane har forskellige rundinger ved fælles mærker.</strong><ul>${conflicts.map(mark => `<li><strong>${e(mark.name)}:</strong> Stor bane ${sides(mark.stor)}; lille bane ${sides(mark.lille)}.</li>`).join('')}</ul></div>` : '';
}
export const keys = ['TACIL', 'TACIM', 'TACIH', 'TAUDL', 'TAUDM', 'TAUDH'];
export const keyNames = ['Cirkel · let', 'Cirkel · mellem', 'Cirkel · hård', 'Op/ned · let', 'Op/ned · mellem', 'Op/ned · hård'];
export const dialog = $('#dialog');
export const staff = () => !!state.session.data?.user?.approved;
export const admin = () => staff() && state.session.data.user.role === 'admin';
export const owner = () => state.session.data?.can_approve;
export const number = (v, d = 0) => v == null ? '–' : Number(v).toLocaleString('da-DK', { maximumFractionDigits: d });
export const gphNumber = v => v == null ? '–' : Number(v).toLocaleString('da-DK', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
export const date = v => new Date(v).toLocaleDateString('da-DK', { day: 'numeric', month: 'short' });
export const clock = v => new Date(v).toLocaleTimeString('da-DK', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }).replace(/\./g, ':');
export const seconds = v => {
    if (v == null)
        return '–';
    const sign = v < 0 ? '−' : '';
    v = Math.round(Math.abs(v));
    return `${sign}${Math.floor(v / 3600)}:${String(Math.floor(v / 60) % 60).padStart(2, '0')}:${String(v % 60).padStart(2, '0')}`;
};
export const now = () => new Date(Date.now() + state.session.offset);
export function localInput(value, precise = false) { const d = new Date(value); const suffix = precise ? '.' + String(d.getMilliseconds()).padStart(3, '0') : ''; return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}${suffix}`; }
export const normalize = v => String(v).normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase().replace(/ø/g, 'o').replace(/æ/g, 'ae');
export const uid = () => crypto.randomUUID();
export const statusText = status => status === 'DUTY' ? 'Dommertjans · beregnes ved afslutning' : status === 'RDG' ? 'Dommertjans · beregnet' : status;
export function errorMarkup(error) {
    return `<p class="error">${e(error.message || error)}</p>${(error.handicapIssues || []).map(issue => `<button type="button" data-handicap-fix="${e(JSON.stringify(issue))}">Ret handicap for ${e(issue.name)}</button>`).join(' ')}`;
}
export function toast(message) {
    const node = $('#toast'), actionable = message?.handicapIssues?.length;
    if (actionable) node.innerHTML = errorMarkup(message);
    else node.textContent = message?.message || message;
    node.classList.add('visible');
    clearTimeout(runtime.toastTimer);
    if (!actionable) runtime.toastTimer = setTimeout(() => node.classList.remove('visible'), 5000);
}
export const classOptions = (course = state.results.course) => [...new Set([...state.session.data.boats.filter(b => b.course === course).map(b => b.class_name), ...(state.session.data.rounds || []).flatMap(r => r.courses.filter(c => c.course === course).flatMap(c => c.heats.map(h => h.name)))])].sort();
export const options = (list, value, labels) => list.map((x, i) => `<option value="${e(x)}" ${String(x) === String(value) ? 'selected' : ''}>${e(labels ? labels[i] : x)}</option>`).join('');
export const raceOptions = (list, value) => list.map(r => `<option value="${r.id}" ${r.id === Number(value) ? 'selected' : ''}>${e(r.label)} · ${date(r.start)} · ${r.course === 'stor' ? 'Stor' : 'Lille'} bane${r.published ? '' : ' · kladde'}</option>`).join('');
export function markMap(routeValues = []) { const marks = state.session.data.marks; const minX = Math.min(...marks.map(m => m.longitude)), maxX = Math.max(...marks.map(m => m.longitude)); const minY = Math.min(...marks.map(m => m.latitude)), maxY = Math.max(...marks.map(m => m.latitude)); const p = m => [22 + (m.longitude - minX) / (maxX - minX) * 212, 140 - (m.latitude - minY) / (maxY - minY) * 120]; const selected = routeValues.length ? ['J', ...routeValues, 'X'] : []; return `<svg class="course-map" viewBox="0 0 260 160" role="img" aria-label="Banemærker ved Kaløvig"><path d="M0 0 L35 0 17 30 25 48 3 89 0 160Z" fill="#d7e2d7"/><path d="M55 0V160M105 0V160M155 0V160M205 0V160M0 35H260M0 85H260M0 135H260" stroke="#dce8e0" stroke-width=".5"/>${selected.length ? `<polyline class="route" points="${selected.map(n => marks.find(m => m.name === n)).filter(Boolean).map(p).map(p => p.join(',')).join(' ')}"/>` : ''}${marks.map(m => { const [x, y] = p(m); return `<circle cx="${x}" cy="${y}" r="3.5" ${selected.includes(m.name) ? 'class="active"' : ''}/><text x="${x + 7}" y="${y + 3}">${e(m.name)}</text>`; }).join('')}</svg>`; }
export function shell(content) {
    const user = state.session.data.user, page = state.session.page;
    const menu = [['/results', 'Resultater'], ['/timing', 'Måltagning'], ['/series', 'Serier'], ...(admin() ? [['/boats', 'Bådregister'], ['/admin', 'Administration']] : [])];
    const activeArea = ['round', 'timing'].includes(page) ? '/timing' : ['series-list', 'series', 'assignments', 'registrations', 'settings'].includes(page) ? '/series' : page === 'registry' ? '/boats' : page === 'admin' ? '/admin' : '/results';
    $('#app').innerHTML = `<div class="app-shell ${page === 'assignments' ? 'is-assignments' : ''}"><header class="topbar"><a class="brand" href="/results" data-nav><img class="brand-logo" src="/kbl-logo.png" width="44" height="44" alt="Kaløvig Bådelaugs logo"><span>Onsdagsbanen<small>Kaløvig Bådelaug</small></span></a><div class="toptools">${user ? `<span class="person">${e(user.name)}</span><button data-action="logout" class="small" ${state.session.data.demo ? 'disabled' : ''}>Log ud</button>` : '<button data-action="login">Log ind</button>'}</div></header>${staff() ? `<nav class="nav app-nav" aria-label="Hovedmenu">${menu.map(([url, name]) => link(url === '/timing' ? timingURL() : url, name, activeArea === url)).join('')}</nav>` : ''}<main>${workspaceNavigation()}${state.session.data.demo ? '<p class="demo-banner">Lokal demonstration</p>' : ''}${content}</main></div>`;
    fitTableColumns();
}
export function intro(title, description, action = '') { return `<div class="intro"><div><h1>${title}</h1><p>${description}</p></div>${action}</div>`; }
export function selectors(course = state.results.course) { return `<div class="segmented" aria-label="Vælg bane"><button data-course="stor" class="${course === 'stor' ? 'active' : ''}">Stor bane</button><button data-course="lille" class="${course === 'lille' ? 'active' : ''}">Lille bane</button></div>`; }
export function fitTableColumns() {
    for (const table of $$('table')) {
        if (!table.getClientRects().length)
            continue;
        const cells = [...table.rows].flatMap(row => [...row.cells]);
        for (const cell of cells)
            if (cell.dataset.proportioned) {
                cell.style.removeProperty('min-width');
                delete cell.dataset.proportioned;
            }
        const widths = [];
        for (const row of table.rows) {
            let column = 0;
            for (const cell of row.cells) {
                if (cell.colSpan === 1)
                    widths[column] = Math.max(widths[column] || 0, Math.ceil(cell.getBoundingClientRect().height) + 1);
                column += cell.colSpan;
            }
        }
        for (const row of table.rows) {
            let column = 0;
            for (const cell of row.cells) {
                if (cell.colSpan === 1 && widths[column]) {
                    cell.style.minWidth = widths[column] + 'px';
                    cell.dataset.proportioned = 'true';
                }
                column += cell.colSpan;
            }
        }
    }
}
new MutationObserver(changes => {
    if (changes.some(change => change.target.nodeType === 1 && change.target.closest('table') || [...change.addedNodes].some(node => node.nodeType === 1 && (node.matches('table') || node.querySelector('table'))))) {
        cancelAnimationFrame(runtime.tableFitFrame);
        runtime.tableFitFrame = requestAnimationFrame(fitTableColumns);
    }
}).observe(document.body, { childList: true, subtree: true });
window.addEventListener('resize', fitTableColumns);
export function openDialog(title, body) {
    dialog.innerHTML = `<div class="dialog-head"><h2>${title}</h2><button data-action="close-dialog" aria-label="Luk">×</button></div><div class="body">${body}<div class="dialog-error" role="alert"></div></div>`;
    if (!dialog.open)
        dialog.showModal();
    fitTableColumns();
}
export function normalizeNumberInput(input) {
    const value = input.value.replace(/,/g, '.');
    if (input.value !== value) {
        const cursor = input.selectionStart;
        input.value = value;
        try {
            input.setSelectionRange(cursor, cursor);
        }
        catch { }
    }
    input.setCustomValidity(value && !/^\d+(?:\.\d+)?$/.test(value) ? 'Angiv ét tal. Komma og punktum kan begge bruges som decimaltegn.' : '');
}
