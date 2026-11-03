const months = ['Januar', 'Februar', 'Marts', 'April', 'Maj', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'December'];
const weekdays = ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn'];
const dayLabel = new Intl.DateTimeFormat('da-DK', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
let calendar = null;

function parseDate(value) {
    const match = /^(\d{2})-(\d{2})-(\d{4})$/.exec(value);
    if (!match) return null;
    const [, day, month, year] = match.map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day ? date : null;
}
const isoDate = date => date.toISOString().slice(0, 10);
const today = () => {
    const date = new Date();
    return new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
};

function closeCalendar(focus = false) {
    const current = calendar;
    calendar = null;
    if (!current) return;
    current.panel.remove();
    current.input.setAttribute('aria-expanded', 'false');
    current.input.removeAttribute('aria-controls');
    if (focus && current.input.isConnected && !current.input.matches(':disabled')) current.input.focus({ preventScroll: true });
}

function positionCalendar() {
    if (!calendar) return;
    const { panel, input } = calendar, anchor = input.getBoundingClientRect(), bounds = panel.getBoundingClientRect();
    panel.style.left = Math.max(8, Math.min(anchor.left, innerWidth - bounds.width - 8)) + 'px';
    const below = anchor.bottom + 6;
    panel.style.top = Math.max(8, Math.min(below + bounds.height <= innerHeight - 8 ? below : anchor.top - bounds.height - 6, innerHeight - bounds.height - 8)) + 'px';
}

function renderCalendar(focus = null) {
    const current = calendar;
    if (!current) return;
    const { year, month, panel } = current;
    const first = new Date(Date.UTC(year, month, 1));
    const offset = (first.getUTCDay() + 6) % 7;
    const days = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    const selected = current.selected, todayISO = isoDate(today());
    const years = Array.from({ length: Math.max(2200, year) - Math.min(2020, year) + 1 }, (_, i) => Math.min(2020, year) + i);
    const cells = Array.from({ length: 42 }, (_, index) => {
        const day = index - offset + 1;
        if (day < 1 || day > days) return '<td></td>';
        const date = new Date(Date.UTC(year, month, day)), iso = isoDate(date);
        return `<td role="gridcell" aria-selected="${iso === selected}"><button type="button" data-calendar-date="${iso}" aria-label="${dayLabel.format(date)}" ${iso === todayISO ? 'aria-current="date"' : ''} tabindex="${iso === current.focus ? '0' : '-1'}">${day}</button></td>`;
    });
    panel.innerHTML = `<div class="calendar-heading"><button type="button" data-calendar-move="-1" aria-label="Forrige måned">‹</button><select data-calendar-month aria-label="Måned">${months.map((name, i) => `<option value="${i}" ${i === month ? 'selected' : ''}>${name}</option>`).join('')}</select><select data-calendar-year aria-label="År">${years.map(value => `<option ${value === year ? 'selected' : ''}>${value}</option>`).join('')}</select><button type="button" data-calendar-move="1" aria-label="Næste måned">›</button></div><table class="calendar-grid" role="grid" aria-label="${months[month]} ${year}"><thead><tr>${weekdays.map(name => `<th scope="col">${name}</th>`).join('')}</tr></thead><tbody>${Array.from({ length: 6 }, (_, i) => `<tr>${cells.slice(i * 7, i * 7 + 7).join('')}</tr>`).join('')}</tbody></table><div class="calendar-footer"><button type="button" data-calendar-today>I dag</button>${current.input.required ? '' : '<button type="button" data-calendar-clear>Ryd dato</button>'}<button type="button" data-calendar-close>Luk</button></div>`;
    if (focus) {
        const target = focus === 'day' ? `[data-calendar-date="${current.focus}"]` : focus;
        panel.querySelector(target)?.focus({ preventScroll: true });
    }
    requestAnimationFrame(positionCalendar);
}

function chooseDate(iso) {
    const input = calendar.input;
    closeCalendar();
    input.value = iso ? iso.split('-').reverse().join('-') : '';
    input.setCustomValidity('');
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    if (input.isConnected) input.focus({ preventScroll: true });
}

function moveMonth(offset, focus) {
    const previous = new Date(calendar.focus + 'T00:00:00Z');
    const month = new Date(Date.UTC(calendar.year, calendar.month + offset, 1));
    calendar.year = month.getUTCFullYear();
    calendar.month = month.getUTCMonth();
    calendar.focus = isoDate(new Date(Date.UTC(calendar.year, calendar.month, Math.min(previous.getUTCDate(), new Date(Date.UTC(calendar.year, calendar.month + 1, 0)).getUTCDate()))));
    renderCalendar(focus);
}

function openCalendar(input) {
    if (input.matches(':disabled')) return;
    if (calendar?.input === input) { closeCalendar(true); return; }
    closeCalendar();
    const selected = parseDate(input.value), initial = selected || today();
    if (!selected && input.dataset.calendarYear) initial.setUTCFullYear(Number(input.dataset.calendarYear));
    const panel = document.createElement('div');
    panel.className = 'date-calendar';
    panel.id = 'date-calendar';
    panel.popover = 'auto';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Vælg dato');
    (input.closest('dialog') || document.body).appendChild(panel);
    calendar = { input, panel, year: initial.getUTCFullYear(), month: initial.getUTCMonth(), selected: selected ? isoDate(selected) : null, focus: isoDate(initial) };
    input.setAttribute('aria-expanded', 'true');
    input.setAttribute('aria-controls', panel.id);
    panel.addEventListener('toggle', event => {
        if (event.newState === 'closed' && calendar?.panel === panel) closeCalendar();
    });
    panel.addEventListener('click', event => {
        const button = event.target.closest('button');
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();
        if (button.dataset.calendarDate) chooseDate(button.dataset.calendarDate);
        else if (button.hasAttribute('data-calendar-today')) chooseDate(isoDate(today()));
        else if (button.hasAttribute('data-calendar-clear')) chooseDate('');
        else if (button.hasAttribute('data-calendar-close')) closeCalendar(true);
        else if (button.dataset.calendarMove) moveMonth(Number(button.dataset.calendarMove), `[data-calendar-move="${button.dataset.calendarMove}"]`);
    });
    panel.addEventListener('change', event => {
        event.stopPropagation();
        const target = event.target;
        if (target.matches('[data-calendar-month]')) moveMonth(Number(target.value) - calendar.month, '[data-calendar-month]');
        else if (target.matches('[data-calendar-year]')) moveMonth((Number(target.value) - calendar.year) * 12, '[data-calendar-year]');
    });
    panel.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            event.preventDefault(); event.stopPropagation(); closeCalendar(true); return;
        }
        const button = event.target.closest('[data-calendar-date]');
        if (!button) return;
        if (['PageUp', 'PageDown'].includes(event.key)) {
            event.preventDefault(); moveMonth(event.key === 'PageUp' ? -1 : 1, 'day'); return;
        }
        const date = new Date(button.dataset.calendarDate + 'T00:00:00Z');
        const offsets = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7, Home: -(date.getUTCDay() + 6) % 7, End: 6 - (date.getUTCDay() + 6) % 7 };
        if (!(event.key in offsets)) return;
        event.preventDefault();
        date.setUTCDate(date.getUTCDate() + offsets[event.key]);
        Object.assign(calendar, { year: date.getUTCFullYear(), month: date.getUTCMonth(), focus: isoDate(date) });
        renderCalendar('day');
    });
    renderCalendar();
    panel.showPopover();
    positionCalendar();
    panel.querySelector(`[data-calendar-date="${calendar.focus}"]`)?.focus({ preventScroll: true });
}

function enhanceDates() {
    for (const input of document.querySelectorAll('input[data-calendar]:not([data-calendar-ready])')) {
        input.dataset.calendarReady = 'true';
        input.inputMode = 'none';
        input.autocomplete = 'off';
        input.setAttribute('aria-haspopup', 'dialog');
        input.setAttribute('aria-expanded', 'false');
        const wrapper = document.createElement('span');
        wrapper.className = 'date-input-control';
        input.before(wrapper);
        wrapper.appendChild(input);
        wrapper.insertAdjacentHTML('beforeend', '<span class="date-input-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 2v6m10-6v6M3 11h18"/></svg></span>');
    }
    if (calendar && (!calendar.input.isConnected || calendar.input.matches(':disabled'))) closeCalendar();
}
document.addEventListener('pointerdown', event => {
    if (event.target.matches('input[data-calendar]:not(:disabled)')) event.preventDefault();
}, true);
document.addEventListener('click', event => {
    if (event.target.matches('input[data-calendar]:not(:disabled)')) {
        event.preventDefault(); event.stopImmediatePropagation(); openCalendar(event.target);
    }
}, true);
document.addEventListener('keydown', event => {
    if (event.target.matches('input[data-calendar]:not(:disabled)') && ['Enter', 'ArrowDown'].includes(event.key)) {
        event.preventDefault(); event.stopImmediatePropagation(); openCalendar(event.target);
    }
}, true);
document.addEventListener('close', event => {
    if (calendar && event.target.contains(calendar.panel)) closeCalendar();
}, true);
new MutationObserver(enhanceDates).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] });
window.addEventListener('resize', positionCalendar);
window.addEventListener('scroll', positionCalendar, true);
enhanceDates();
