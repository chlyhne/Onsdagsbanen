import { e, intro, openDialog, owner, shell } from '../core/ui.js';
import { api } from '../core/api.js';
import { state } from '../core/state.js';
export async function showAdmin() {
    const users = owner() ? await api('/api/users') : [], history = await api('/api/audit');
    shell(`${intro('Administration', 'Brugeradgang og systemets ændringshistorik.')}<div class="course-cards"><section class="panel padded"><h2>Brugere og adgang</h2>${users.map(u => `<div class="audit-entry"><strong>${e(u.name)}</strong> · ${e(u.email)}<p>${e(u.provider)} · ${u.approved ? 'Godkendt' : 'Afventer godkendelse'}</p>${u.email === 'hummesse@gmail.com' && u.provider === 'google' ? '<span>Ejer</span>' : `<div class="row"><select id="user-role-${u.id}"><option value="recorder" ${u.role === 'recorder' ? 'selected' : ''}>Resultatindtaster</option><option value="admin" ${u.role === 'admin' ? 'selected' : ''}>Administrator</option></select><button data-user="${u.id}" data-approved="true">${u.approved ? 'Gem rolle' : 'Godkend'}</button></div>`}</div>`).join('') || '<p>Ingen brugere at vise.</p>'}</section><section class="panel padded"><h2>Ændringshistorik</h2>${history.map(h => `<details class="audit-entry"><summary>${new Date(h.at * 1000).toLocaleString('da-DK')} · ${e(h.action)}</summary><pre>${e(JSON.stringify(JSON.parse(h.payload), null, 2))}</pre></details>`).join('')}</section></div>`);
}
export function showLogin() {
    if (state.session.data.preview_login_path) {
        openDialog('Log ind', `<div class="login-options"><a class="preview-login" href="${e(state.session.data.preview_login_path)}">Log ind som administrator</a></div><p class="subtle">Midlertidig adgang til det lokale preview i op til to timer. Adgangen ophører, når previewserveren stoppes.</p>`);
        return;
    }
    openDialog('Log ind på Onsdagsbanen', `<p class="subtle">Brug din egen konto. Første gang oprettes en bruger, som hummesse@gmail.com skal godkende.</p><div class="login-options">${state.session.data.preview_login_path ? `<a class="preview-login" href="${e(state.session.data.preview_login_path)}">Midlertidig administrator</a>` : ''}<a href="/auth/google" class="${state.session.data.providers.google ? '' : 'disabled'}">Fortsæt med Google</a><a href="/auth/facebook" class="${state.session.data.providers.facebook ? '' : 'disabled'}">Fortsæt med Facebook</a></div>${!state.session.data.providers.google && !state.session.data.providers.facebook ? '<p class="notice info">Login åbner, når hjemmesiden er sat i drift.</p>' : ''}<p class="subtle">Resultater kan ses uden login. Resultatindtastere kan registrere og offentliggøre tider. Administratorer kan også oprette sejladser og administrere stamdata.</p>`);
}
