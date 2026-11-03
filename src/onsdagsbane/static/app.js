import './core/calendar.js';
import './core/events.js';
import './features/assignments.js';
import './features/boats.js';
import { load } from './core/api.js';
import { render, readLocation } from './core/router.js';
import { restoreResultPreferences } from './features/results.js';
import { $, e } from './core/ui.js';
(async () => {
    try {
        restoreResultPreferences();
        await load();
        await readLocation();
        await render();
    }
    catch (error) {
        $('#app').innerHTML = `<main><h1>Forbindelsen mangler</h1><p>${e(error.message)}</p></main>`;
    }
})();
