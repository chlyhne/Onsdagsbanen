'use strict';
const $ = (s,root=document)=>root.querySelector(s);
const $$ = (s,root=document)=>[...root.querySelectorAll(s)];
const escapeHTML = v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const e=escapeHTML;
const keys=['TACIL','TACIM','TACIH','TAUDL','TAUDM','TAUDH'];
const keyNames=['Cirkel · let','Cirkel · mellem','Cirkel · hård','Op/ned · let','Op/ned · mellem','Op/ned · hård'];
const state={page:'results',course:'stor',className:null,view:'overall',raceId:null,query:'',selected:null,capture:null,draft:false,data:null,result:null,race:null,offset:0,connected:true};
let busy=false, route=[], toastTimer, requestSerial=0;
const dialog=$('#dialog');
const staff=()=>!!state.data?.user?.approved;
const admin=()=>staff()&&state.data.user.role==='admin';
const owner=()=>state.data?.can_approve;
const number=(v,d=0)=>v==null?'–':Number(v).toLocaleString('da-DK',{maximumFractionDigits:d});
const gphNumber=v=>v==null?'–':Number(v).toLocaleString('da-DK',{minimumFractionDigits:1,maximumFractionDigits:1});
const date=v=>new Date(v).toLocaleDateString('da-DK',{day:'numeric',month:'short'});
const clock=v=>new Date(v).toLocaleTimeString('da-DK',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'}).replace(/\./g,':');
const seconds=v=>{if(v==null)return '–';const sign=v<0?'−':'';v=Math.round(Math.abs(v));return `${sign}${Math.floor(v/3600)}:${String(Math.floor(v/60)%60).padStart(2,'0')}:${String(v%60).padStart(2,'0')}`;};
const now=()=>new Date(Date.now()+state.offset);
function localInput(value,precise=false){const d=new Date(value);const suffix=precise?'.'+String(d.getMilliseconds()).padStart(3,'0'):'';return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}${suffix}`;}
const normalize=v=>String(v).normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase().replace(/ø/g,'o').replace(/æ/g,'ae');
const uid=()=>crypto.randomUUID();
const statusText=status=>status==='DUTY'?'Dommertjans · beregnes ved afslutning':status==='RDG'?'Dommertjans · beregnet':status;
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),5000);}
async function api(path,data){const response=await fetch(path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-CSRF-Token':state.data?.user?.csrf||''}:{},body:data?JSON.stringify(data):undefined});let result;try{result=await response.json();}catch{throw new Error('Serveren gav ikke et læsbart svar. Registreringen er ikke bekræftet.');}if(!response.ok)throw new Error(result.error||'Handlingen kunne ikke gennemføres.');return result;}
const resultPreferenceCookie='ob_results';
function restoreResultPreferences(){
  let saved={};
  try{
    const raw=document.cookie.split('; ').find(row=>row.startsWith(resultPreferenceCookie+'='));
    const parsed=raw?JSON.parse(decodeURIComponent(raw.slice(resultPreferenceCookie.length+1))):{};
    if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed))saved=parsed;
  }catch{}
  const params=new URLSearchParams(location.search);
  const input={...saved};
  for(const key of ['series','course','class','view','race'])if(params.has(key))input[key]=params.get(key);
  const identity=value=>/^\d+$/.test(String(value))&&Number.isSafeInteger(Number(value))&&Number(value)>0?Number(value):null;
  state.seriesId=identity(input.series)||undefined;
  state.course=['stor','lille'].includes(input.course)?input.course:'stor';
  state.className=input.class==='all'?'':typeof input.class==='string'&&input.class.length<=160?input.class:null;
  state.view=['daily','overall'].includes(input.view)?input.view:'overall';
  state.raceId=identity(input.race);
  state.draft=false;
}
function resultURL(){
  const url=new URL(location.pathname,location.origin);
  url.search=new URLSearchParams({series:String(state.seriesId),course:state.course,class:state.className||'all',view:state.view,...(state.raceId?{race:String(state.raceId)}:{})}).toString();
  return url;
}
function rememberResults(historyMode='replace'){
  const url=resultURL();
  if(url.href!==location.href)history[historyMode==='push'?'pushState':'replaceState']({},'',url);
  const saved=Object.fromEntries(url.searchParams);
  document.cookie=resultPreferenceCookie+'='+encodeURIComponent(JSON.stringify(saved))+'; Path=/; Max-Age=31536000; SameSite=Lax'+(location.protocol==='https:'?'; Secure':'');
}
async function copyResultLink(){
  const url=resultURL().href;
  try{await navigator.clipboard.writeText(url);toast('Linket er kopieret.');}
  catch{openDialog('Del resultater',`<label class="field">Kopiér dette link<input id="share-url" type="url" value="${e(url)}" readonly></label>`);$('#share-url').select();}
}
async function load(){
  const before=Date.now();
  state.data=await api('/api/state'+(state.seriesId?`?series_id=${state.seriesId}`:''));
  if(!state.data.series.some(series=>series.id===state.data.series_id))state.data=await api('/api/state');
  state.offset=new Date(state.data.server_time).getTime()-(before+Date.now())/2;
  state.seriesId=state.data.series_id;
  state.connected=true;
}
const currentSeries=()=>state.data.series.find(s=>s.id===state.seriesId);
const classOptions=(course=state.course)=>[...new Set([...state.data.boats.filter(b=>b.course===course).map(b=>b.class_name),...(state.data.rounds||[]).flatMap(r=>r.courses.filter(c=>c.course===course).flatMap(c=>c.heats.map(h=>h.name)))])].sort();
const options=(list,value,labels)=>list.map((x,i)=>`<option value="${e(x)}" ${String(x)===String(value)?'selected':''}>${e(labels?labels[i]:x)}</option>`).join('');
const raceOptions=(list,value)=>list.map(r=>`<option value="${r.id}" ${r.id===Number(value)?'selected':''}>${e(r.label)} · ${date(r.start)} · ${r.course==='stor'?'Stor':'Lille'} bane${r.published?'':' · kladde'}</option>`).join('');
function markMap(routeValues=[]){const marks=state.data.marks;const minX=Math.min(...marks.map(m=>m.longitude)),maxX=Math.max(...marks.map(m=>m.longitude));const minY=Math.min(...marks.map(m=>m.latitude)),maxY=Math.max(...marks.map(m=>m.latitude));const p=m=>[22+(m.longitude-minX)/(maxX-minX)*212,140-(m.latitude-minY)/(maxY-minY)*120];const selected=routeValues.length?['J',...routeValues,'X']:[];return `<svg class="course-map" viewBox="0 0 260 160" role="img" aria-label="Banemærker ved Kaløvig"><path d="M0 0 L35 0 17 30 25 48 3 89 0 160Z" fill="#d7e2d7"/><path d="M55 0V160M105 0V160M155 0V160M205 0V160M0 35H260M0 85H260M0 135H260" stroke="#dce8e0" stroke-width=".5"/>${selected.length?`<polyline class="route" points="${selected.map(n=>marks.find(m=>m.name===n)).filter(Boolean).map(p).map(p=>p.join(',')).join(' ')}"/>`:''}${marks.map(m=>{const [x,y]=p(m);return `<circle cx="${x}" cy="${y}" r="3.5" ${selected.includes(m.name)?'class="active"':''}/><text x="${x+7}" y="${y+3}">${e(m.name)}</text>`;}).join('')}</svg>`;}
function shell(content){
  const user=state.data.user;
  const nav=[['results','Resultater'],['timing','Måltagning'],...(admin()?[['series','Serie']]:[]),...(state.data.races.some(r=>r.mode==='pursuit')?[['starts','Startlister']]:[]),['boats','Både & DH'],['admin','Administration']];
  $('#app').innerHTML=`<div class="shell"><header class="topbar"><a class="brand" href="/" aria-label="Onsdagsbanen forside"><span>Onsdagsbanen<small>Kaløvig Bådelaug</small></span></a><div class="toptools"><select id="series-select" aria-label="Vælg serie">${state.data.series.map(s=>`<option value="${s.id}" ${s.id===state.seriesId?'selected':''}>${e(s.name.replace(/^Onsdagsbanen\s+/i,''))}</option>`).join('')}</select>${admin()?'<button data-action="new-series" class="small">Ny serie</button>':''}${user?`<span class="person">${e(user.name)}</span><button data-action="logout" class="small" ${state.data.demo?'disabled':''}>Log ud</button>`:'<button data-action="login">Log ind</button>'}</div></header>${staff()?`<nav class="nav" aria-label="Hovedmenu">${nav.map(([id,name])=>`<button data-page="${id}" class="${state.page===id?'active':''}" ${state.page===id?'aria-current="page"':''}>${name}</button>`).join('')}</nav>`:''}<main>${state.data.demo?'<p class="demo-banner">Lokal demonstration</p>':''}${user&&!user.approved?'<p class="subtle">Din konto afventer godkendelse fra hummesse@gmail.com.</p>':''}${content}</main></div>`;
  fitTableColumns();
}
function intro(title,description,action=''){return `<div class="intro"><div><h1>${title}</h1><p>${description}</p></div>${action}</div>`;}
function selectors(){return `<div class="segmented" aria-label="Vælg bane"><button data-course="stor" class="${state.course==='stor'?'active':''}">Stor bane</button><button data-course="lille" class="${state.course==='lille'?'active':''}">Lille bane</button></div>`;}
function configureResultScroll(){
  const scroller=$('.results-table .table-scroll');
  if(!scroller)return;
  if(scroller.scrollWidth>scroller.clientWidth+1){
    scroller.tabIndex=0;
    scroller.setAttribute('role','region');
    scroller.setAttribute('aria-label','Resultattabel. Rul vandret for at se flere kolonner.');
  }else{
    scroller.removeAttribute('tabindex');
    scroller.removeAttribute('role');
    scroller.removeAttribute('aria-label');
  }
}
function fitTableColumns(){
  for(const table of $$('table')){
    if(!table.getClientRects().length)continue;
    const cells=[...table.rows].flatMap(row=>[...row.cells]);
    for(const cell of cells)if(cell.dataset.proportioned){cell.style.removeProperty('min-width');delete cell.dataset.proportioned;}
    const widths=[];
    for(const row of table.rows){let column=0;for(const cell of row.cells){if(cell.colSpan===1)widths[column]=Math.max(widths[column]||0,Math.ceil(cell.getBoundingClientRect().height)+1);column+=cell.colSpan;}}
    for(const row of table.rows){let column=0;for(const cell of row.cells){if(cell.colSpan===1&&widths[column]){cell.style.minWidth=widths[column]+'px';cell.dataset.proportioned='true';}column+=cell.colSpan;}}
  }
  configureResultScroll();
}
let tableFitFrame;
new MutationObserver(changes=>{
  if(changes.some(change=>change.target.nodeType===1&&change.target.closest('table')||[...change.addedNodes].some(node=>node.nodeType===1&&(node.matches('table')||node.querySelector('table'))))){
    cancelAnimationFrame(tableFitFrame);tableFitFrame=requestAnimationFrame(fitTableColumns);
  }
}).observe(document.body,{childList:true,subtree:true});

window.addEventListener('resize',fitTableColumns);
function resultClassOptions(){
  return ['stor','lille'].map(course=>{
    const classes=classOptions(course);
    return `<optgroup label="${course==='stor'?'Stor bane':'Lille bane'}">${classes.map(name=>`<option data-course="${course}" value="${e(name)}" ${state.course===course&&state.className===name?'selected':''}>${e(name)}</option>`).join('')}<option value="${course}:all" ${state.course===course&&state.className===''?'selected':''}>${course==='stor'?'Stor':'Lille'} bane · alle løb</option></optgroup>`;
  }).join('');
}
async function showResults(historyMode='replace'){
  const classes=classOptions();
  if(state.className===null||(state.className!==''&&!classes.includes(state.className)))state.className=classes[0]||'';
  const id=++requestSerial;
  const data=await api(`/api/results?series_id=${state.seriesId}&course=${state.course}&class_name=${encodeURIComponent(state.className)}${state.draft?'&drafts=1':''}`);
  if(id!==requestSerial||state.page!=='results')return;
  state.result=data;
  const races=data.races;
  if(!races.some(r=>r.id===state.raceId))state.raceId=races.at(-1)?.id||null;
  const chosen=races.find(r=>r.id===state.raceId);
  rememberResults(historyMode);
  let table='',conditions='';
  if(state.view==='overall'){
    table=`<div class="table-scroll"><table class="standings"><caption class="visually-hidden">Samlet pointstilling · ${e(state.className||state.course+' bane · alle løb')}</caption><thead><tr><th scope="col" class="rank">Pl.</th><th scope="col">Båd / skipper</th><th scope="col">Sejlnummer</th>${races.map(r=>`<th scope="col" class="numeric" title="${e(r.label)} · ${date(r.start)}">${e(r.label)}</th>`).join('')}<th scope="col" class="numeric">I alt</th></tr></thead><tbody>${data.overall.map(r=>`<tr><td class="rank">${r.rank}</td><td><div class="boatname">${e(r.name)}</div><div class="boatdetail">${e(r.skipper)}${state.className?'':' · '+e(r.class_name)}</div></td><td class="sail">${e(r.sail_number)}</td>${r.scores.map(score=>`<td class="numeric ${score.discarded?'discarded':''}" title="${e(statusText(score.status))}${score.discarded?' · fratrukket':''}">${number(score.points,1)}${score.status==='RDG'?'*':''}</td>`).join('')}<td class="numeric total">${number(r.total,1)}</td></tr>`).join('')}</tbody></table></div>`;
  }else if(chosen){
    const dayRows=data.daily[chosen.id]||[];
    const distances=[...new Set(dayRows.map(row=>row.distance).filter(value=>value!=null))].sort((a,b)=>a-b);
    if(!distances.length&&chosen.distance!=null)distances.push(chosen.distance);
    const windCategories=[...new Set(dayRows.map(row=>row.wind_category))];
    if(!windCategories.length)windCategories.push(chosen.rating_key?.slice(-1));
    const windNames={L:'Let',M:'Mellem',H:'Hård'};
    const marks=chosen.rounding_marks||[];
    const courseLetters=marks.length?`<span class="course-sequence">${marks.map(mark=>`<span class="course-mark course-${mark.side==='starboard'?'starboard':'port'}" title="${e(mark.name)}: ${mark.side==='starboard'?'styrbord (grøn)':'bagbord (rød)'}" aria-label="${e(mark.name)}: ${mark.side==='starboard'?'styrbord, grøn':'bagbord, rød'}">${e(mark.name)}<sub class="rounding-side" aria-hidden="true">${mark.side==='starboard'?'S':'B'}</sub></span>`).join('')}</span>`:'Ikke registreret';
    conditions=`<p class="race-conditions"><span><strong>Bane:</strong> ${courseLetters}</span><span><strong>Distance:</strong> ${distances.length?distances.map(value=>number(value,3)+' sm').join(' / '):'Ikke angivet'}</span><span><strong>Vindkategori:</strong> ${chosen.rating_key==='GPH'?'Ikke relevant (GPH)':windCategories.map(value=>windNames[value]||'Ikke angivet').join(' / ')}</span></p>`;
    const hasExtra=dayRows.some(row=>row.extra_handicap);
    const hasPenalty=dayRows.some(row=>row.penalty);
    table=`<div class="table-scroll"><table class="daily-results"><caption class="visually-hidden">Dagens tider · ${e(state.className||state.course+' bane · alle løb')} · ${e(chosen.label)}</caption><thead><tr><th scope="col" class="rank">Pl.</th><th scope="col">Båd / skipper</th><th scope="col" class="numeric">Beregnet tid</th><th scope="col" class="numeric points" aria-label="Point" title="Point">Point</th><th scope="col" class="numeric">Sejltid</th><th scope="col">Sejlnr.</th><th scope="col" class="numeric">Hdcp.</th>${hasExtra?'<th scope="col" class="numeric">Ekstra handicap</th>':''}${hasPenalty?'<th scope="col" class="numeric">Straftid</th>':''}</tr></thead><tbody>${dayRows.map(r=>`<tr><td class="rank">${r.rank||'–'}</td><td><div class="boatname">${e(r.name)}${r.status==='RDG'?' *':''}</div><div class="boatdetail">${e(r.skipper)}${state.className?'':' · '+e(r.class_name)}</div></td><td class="numeric">${r.status==='FIN'||r.status==='RDG'?seconds(r.corrected):e(statusText(r.status))}</td><td class="numeric total points">${number(r.points,1)}</td><td class="numeric">${seconds(r.elapsed)}</td><td class="sail">${e(r.sail_number)}</td><td class="numeric">${number(r.rating,1)}</td>${hasExtra?`<td class="numeric">${r.extra_handicap?'+'+number(r.extra_handicap)+' s':'–'}</td>`:''}${hasPenalty?`<td class="numeric">${r.penalty?'+'+number(r.penalty)+' s':'–'}</td>`:''}</tr>`).join('')}</tbody></table></div>`;
  }
  const setupWarnings=(state.view==='daily'?(chosen?.warning?[chosen]:[]):races.filter(r=>r.warning));
  const warnings=[data.reference_warning?`<p>Serien bruger forskellige referencehandicap: ${data.references.map(v=>number(v,1)).join(', ')} s/sm.${admin()?' <button class="quiet-button" data-action="reference">Indstil reference</button>':''}</p>`:'',...setupWarnings.map(r=>`<p>${e(r.label)} · ${date(r.start)}: ${e(r.warning)}</p>`)];
  shell(`<section class="results-page"><div class="results-heading"><h1>Resultater</h1><div class="row"><button class="small" data-action="pdf">PDF</button><button class="small" data-action="copy-link" ${state.draft?'disabled title="Kladder skal offentliggøres før deling"':''}>Kopiér link</button></div></div><div class="results-controls"><label class="field">Løb<select id="class-select">${resultClassOptions()}</select></label><label class="field">Vis<select id="result-view">${options(['overall','daily'],state.view,['Samlet pointstilling','Tider for dagen'])}</select></label>${state.view==='daily'&&races.length?`<label class="field">Sejlads<select id="result-race">${raceOptions([...races].reverse(),state.raceId)}</select></label>`:''}${staff()?`<label class="check"><input id="draft-toggle" type="checkbox" ${state.draft?'checked':''}>Vis kladder</label>`:''}</div>${warnings.some(Boolean)?`<details class="results-warning"><summary>Advarsel om baneopsætning</summary>${warnings.join('')}</details>`:''}${conditions}<div class="panel results-table">${races.length?table:'<p class="empty">Ingen offentliggjorte resultater i dette løb endnu.</p>'}</div>${races.length?`<p class="results-note">${state.view==='overall'?'Overstreget = fratrukket. ':''}* = dommertjans-redress.${data.source_scoring?' Point og fratræk fra Manage2Sail.':''}${state.view==='daily'&&chosen?` Reference: ${number(chosen.reference,1)} s/sm · ${chosen.mode==='pursuit'?'jagtstart':'almindelig respit'}.`:''}</p>`:''}</section>`);
  configureResultScroll();
}
async function showStarts(){let races=state.data.races.filter(r=>r.course===state.course&&r.mode==='pursuit');if(!races.some(r=>r.id===state.startRaceId))state.startRaceId=races[0]?.id;const detail=state.startRaceId?await api(`/api/races/${state.startRaceId}?startlist=1`):null;const race=detail?.race;const entries=(detail?.entries||[]).sort((a,b)=>new Date(a.start)-new Date(b.start)||a.sail_number.localeCompare(b.sail_number));shell(`${intro('Startlister','Starttider og rækkefølge for omvendt respit.',admin()?'<button class="primary" data-action="new-race">Indstil bane</button>':'')}<div class="filters">${selectors()}${races.length?`<select id="start-race" aria-label="Vælg startliste">${raceOptions(races,state.startRaceId)}</select>`:''}</div>${race?`<div class="workspace"><section class="panel"><div class="panel-head"><div><h2>${e(race.label)} · Omvendt respit</h2><p>${date(race.start)} · ${number(race.distance,3)} sm · ${e(race.rating_key==='import'?'Importerede målebrevstal':race.rating_key)}</p></div><button class="small" data-action="print">Udskriv ↗</button></div>${!race.published?'<div class="notice info">Foreløbig startliste · sejladsen er endnu ikke offentliggjort.</div>':''}<div class="table-scroll"><table><thead><tr><th>Nr.</th><th>Starttid</th><th>Båd / skipper</th><th>Sejlnr.</th><th>Løb</th><th class="numeric">Hdcp.</th><th class="numeric">Ekstra handicap</th></tr></thead><tbody>${entries.map((b,i)=>`<tr><td>${i+1}</td><td><strong>${b.start?clock(b.start):'Afventer handicap'}</strong></td><td><div class="boatname">${e(b.name)}</div><div class="boatdetail">${e(b.skipper)}</div></td><td>${e(b.sail_number)}</td><td>${e(b.class_name)}</td><td class="numeric">${number(b.rating,1)}</td><td class="numeric">+${number(b.extra_handicap)} s</td></tr>`).join('')}</tbody></table></div></section><aside class="side"><section class="panel"><h3>Banen</h3>${markMap(JSON.parse(race.route))}<p>${JSON.parse(race.route).length?`J–X → ${JSON.parse(race.route).map(e).join(' → ')} → X`:'Distancen er importeret fra Manage2Sail.'}</p><span class="distance">${number(race.distance,3)} sm</span></section><section class="panel"><h3>DH og jagtstart</h3><p>Langsomste båd starter først. Startforskellene beregnes ud fra det valgte DH-tal og distancen. Ekstra handicap lægges til starttidspunktet.</p><p>Reference til præmietider: ${number(race.reference,1)} s/sm.</p></section></aside></div>`:'<div class="panel empty">Der er ingen omvendt respit-sejladser på den valgte bane.</div>'}`);}
const timingWorkByRace=new Map();
let timingSearchIndex=new Map(),timingRefresh=0;
const timingDay=value=>new Date(value).toLocaleDateString('sv-SE',{timeZone:'Europe/Copenhagen'});
function roundForRace(raceId){return (state.data.rounds||[]).find(round=>round.courses.some(c=>c.race_id===Number(raceId)));}
function chooseTimingRound(rounds=state.data.rounds||[],races=state.data.races,when=now()){
  const today=timingDay(when),items=rounds.map(round=>{const ids=new Set(round.courses.map(c=>c.race_id));const children=races.filter(r=>ids.has(r.id));return {round,day:round.race_date||(children.length?timingDay(children.reduce((a,b)=>Date.parse(a.start)<Date.parse(b.start)?a:b).start):null)};});
  const open=items.filter(item=>!item.round.completed),ascending=(a,b)=>(a.day||'9999').localeCompare(b.day||'9999')||a.round.position-b.round.position;
  return open.filter(item=>item.day===today).sort((a,b)=>b.round.position-a.round.position||b.round.id-a.round.id)[0]?.round||open.filter(item=>item.day>today).sort(ascending)[0]?.round||open.filter(item=>item.day&&item.day<today).sort((a,b)=>b.day.localeCompare(a.day)||a.round.position-b.round.position)[0]?.round||open.filter(item=>!item.day).sort(ascending)[0]?.round||items.filter(item=>item.day===today)[0]?.round||items.filter(item=>item.day).sort((a,b)=>b.day.localeCompare(a.day))[0]?.round||items[0]?.round;
}
function timingRoundOptions(){return (state.data.rounds||[]).map(round=>{const ids=new Set(round.courses.map(c=>c.race_id)),races=state.data.races.filter(r=>ids.has(r.id)),start=races.length?races.reduce((a,b)=>Date.parse(a.start)<Date.parse(b.start)?a:b).start:null;return `<option value="${round.id}" ${round.id===state.timingRoundId?'selected':''}>${e(round.label)} · ${round.race_date?danishDay(round.race_date):start?date(start):'Ikke indstillet'}${round.completed?' · afsluttet':''}</option>`;}).join('');}
function timingStorageKey(roundId){return `ob_timing_round:${state.seriesId}:${roundId}`;}
function timingWork(roundId=state.timingRoundId){
  if(!timingWorkByRace.has(roundId)){
    let saved={};
    try{
      const raw=localStorage.getItem(timingStorageKey(roundId));
      if(raw)saved=JSON.parse(raw)||{};
      else{
        saved={pins:[],drafts:{},activeKey:null};
        const planned=(state.data.rounds||[]).find(r=>r.id===roundId);
        for(const course of planned?.courses||[]){
          const old=JSON.parse(localStorage.getItem(`ob_timing:${state.seriesId}:${course.race_id}`)||'null');if(!old)continue;
          saved.pins.push(...(old.pins||[]));for(const [key,draft] of Object.entries(old.drafts||{}))saved.drafts[key]={...draft,raceId:draft.boatId?course.race_id:undefined};
          saved.activeKey=saved.activeKey||old.activeKey;
        }
      }
    }catch{}
    const work={pins:[...new Set(Array.isArray(saved.pins)?saved.pins.filter(Number.isInteger):[])],drafts:saved.drafts&&typeof saved.drafts==='object'&&!Array.isArray(saved.drafts)?saved.drafts:{},activeKey:saved.activeKey||null};
    for(const draft of Object.values(work.drafts)){draft.saving=false;if(draft.capturing){draft.capturing=false;draft.error='Tiden er gemt lokalt. Prøv at gemme tidsstemplet igen.';}}
    timingWorkByRace.set(roundId,work);
  }
  return timingWorkByRace.get(roundId);
}
function saveTimingWork(roundId=state.timingRoundId){try{localStorage.setItem(timingStorageKey(roundId),JSON.stringify(timingWork(roundId)));}catch{}}
function activeTimingDraft(){const work=timingWork();return work.drafts[work.activeKey]||null;}
function timingCourseForBoat(boatId){const row=state.race.entries.find(r=>r.boat_id===boatId);return state.race.courses.find(r=>r.id===row?.race_id);}
function timingRevisions(){return Object.fromEntries(state.race.courses.map(r=>[String(r.id),r.revision]));}
function timingLocked(){return !!(state.race?.race.completed||state.race?.race.published);}
function buildTimingSearch(){
  timingSearchIndex=new Map(state.race.entries.map(row=>{const master=state.data.boats.find(b=>b.id===row.boat_id)||{};return [row.boat_id,normalize([master.name,master.skipper,master.sail_number,master.boat_type,master.club,master.class_name,master.course,JSON.stringify(master.ratings),master.extra_handicap,row.name,row.skipper,row.sail_number,row.boat_type,row.class_name,row.course,row.rating,statusText(row.status)].join(' '))];}));
}
function matchesTimingSearch(row){const text=timingSearchIndex.get(row.boat_id)||'';return normalize(state.query.replace(/,/g,'.')).split(/\s+/).filter(Boolean).every(word=>text.includes(word));}
function boatIdentity(row){return `<div class="timing-identity"><strong>${e(row.name)}</strong> <span class="timing-sail">${e(row.sail_number)}</span><div>${e(row.skipper)} · ${row.course==='stor'?'Stor':'Lille'} bane · ${e(row.class_name)}</div></div>`;}
function pinIcon(){return '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="m16 3 5 5-4 2-3 5-2-2-6 6-1-1 6-6-2-2 5-3z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';}
function searchBoats(){renderTimingLists();}
function renderTimingLists(){
  if(!$('#unmeasured-boats')||!state.race)return;
  const work=timingWork(),locked=timingLocked(),pins=new Set(work.pins);
  const remaining=state.race.entries.filter(row=>row.sequence===null);
  const measured=state.race.entries.filter(row=>row.sequence!==null).sort((a,b)=>a.sequence-b.sequence);
  const pinned=remaining.filter(row=>pins.has(row.boat_id)).sort((a,b)=>work.pins.indexOf(a.boat_id)-work.pins.indexOf(b.boat_id));
  const visible=[...pinned,...remaining.filter(row=>!pins.has(row.boat_id)&&matchesTimingSearch(row))];
  $('#unmeasured-count').textContent=visible.length===remaining.length?remaining.length:`${visible.length} / ${remaining.length}`;
  const completed=measured.filter(matchesTimingSearch);
  $('#measured-count').textContent=completed.length===measured.length?measured.length:`${completed.length} / ${measured.length}`;
  $('#unmeasured-boats').innerHTML=visible.map(row=>{
    const draft=work.drafts[`boat:${row.boat_id}`];
    return `<div class="timing-boat ${pins.has(row.boat_id)?'is-pinned':''}" data-unmeasured="${row.boat_id}">${boatIdentity(row)}<div class="timing-row-actions"><button class="pin-button" data-pin="${row.boat_id}" aria-pressed="${pins.has(row.boat_id)}" aria-label="${pins.has(row.boat_id)?'Fjern pin fra':'Pin'} ${e(row.name)}" title="${pins.has(row.boat_id)?'Fjern pin':'Pin øverst, også under søgning'}">${pinIcon()}</button><button class="primary" data-measure="${row.boat_id}" ${locked?'disabled':''}>${draft?'Tid klar '+clock(draft.instant):'Måltag'}</button></div></div>`;
  }).join('')||'<p class="timing-empty">Ingen både matcher søgningen.</p>';
  $('#measured-boats').innerHTML=completed.map(row=>`<div class="timing-boat" data-measured="${row.boat_id}">${boatIdentity(row)}<div class="timing-row-actions"><strong class="measured-time ${row.status==='DUTY'?'pending-duty':''}">${row.instant?clock(row.instant):row.status==='FIN'?'Nr. '+row.sequence:e(statusText(row.status))}</strong><button data-edit-finish="${row.boat_id}" ${locked?'disabled':''}>Ret</button>${row?.race_mode==='pursuit'&&!locked&&row.status==='FIN'?`<button class="square-button" data-move="${row.boat_id}" data-direction="-1" aria-label="Flyt ${e(row.name)} op">↑</button><button class="square-button" data-move="${row.boat_id}" data-direction="1" aria-label="Flyt ${e(row.name)} ned">↓</button>`:''}</div></div>`).join('')||'<p class="timing-empty">Ingen måltagne både matcher søgningen.</p>';
  const publish=$('[data-action="publish"]');if(publish)publish.disabled=Object.values(work.drafts).some(d=>d.saving||d.capturing);
}
function renderTimingCaptures(){
  if(!$('#timing-captures')||!state.race)return;
  const work=timingWork();
  const items=[...state.race.captures.map(cap=>({capture_id:cap.id,instant:cap.instant})),...Object.values(work.drafts).filter(d=>!d.boatId&&!d.capture_id).map(d=>({key:d.key,instant:d.instant,capturing:d.capturing}))];
  $('#timing-captures').innerHTML=items.length?`<span>Tider uden båd:</span>${items.map(item=>{const draft=Object.values(work.drafts).find(d=>item.capture_id?d.capture_id===item.capture_id:d.key===item.key);return `<span class="captured-time-pair"><button class="${activeTimingDraft()?.capture_id===item.capture_id&&item.capture_id?'selected-time':''}" ${item.capture_id?`data-capture="${item.capture_id}"`:`data-loose-time="${e(item.key)}"`}>${clock(item.instant)}${item.capturing?' · gemmer…':''}</button><button class="square-button" data-timing-action="discard" data-capture-id="${item.capture_id||''}" data-loose-key="${e(item.key||'')}" aria-label="Kassér tiden ${clock(item.instant)}" ${timingLocked()||draft?.capturing||draft?.saving?'disabled':''}>×</button></span>`;}).join('')}`:'';
}
function timingInputValues(draft){
  const [day,time]=localInput(draft.instant||state.race.race.start,true).split('T');
  return {date:draft.dateInput??day.split('-').reverse().join('-'),time:draft.timeInput??(draft.instant?time.split('.')[0]:'')};
}
function timingInputInstant(form){
  const dateField=$('#finish-date',form),timeField=$('#finish-time',form);
  const match=/^(\d{2})-(\d{2})-(\d{4})$/.exec(dateField.value);
  let validDate=false,day='';
  if(match){
    const [,d,m,y]=match,check=new Date(Date.UTC(Number(y),Number(m)-1,Number(d)));
    validDate=Number(y)>=1000&&check.getUTCFullYear()===Number(y)&&check.getUTCMonth()===Number(m)-1&&check.getUTCDate()===Number(d);
    day=`${y}-${m}-${d}`;
  }
  dateField.setCustomValidity(dateField.value&&!validDate?'Indtast en gyldig dato som DD-MM-ÅÅÅÅ.':'');
  const validTime=/^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$/.test(timeField.value);
  timeField.setCustomValidity(timeField.value&&!validTime?'Indtast et gyldigt klokkeslæt som HH:MM:SS.':'');
  return validDate&&validTime?`${day}T${timeField.value}`:'';
}
function renderActiveTiming(){
  const container=$('#active-timing');if(!container)return;
  const draft=activeTimingDraft();state.selected=draft?.boatId||null;state.capture=draft?.capture_id||null;
  if(!draft){container.innerHTML='';return;}
  const row=draft.boatId?state.race.entries.find(r=>r.boat_id===draft.boatId):null;
  const locked=timingLocked(),disabled=locked||draft.saving,fields=timingInputValues(draft);
  container.innerHTML=`<section class="active-measurement" aria-label="Aktiv måltagning"><div class="active-heading"><h2>${row?e(row.name)+' · '+e(row.sail_number):'Tid uden båd'}</h2><button class="square-button" data-timing-action="cancel" aria-label="Annuller aktiv måltagning" ${draft.saving?'disabled':''}>×</button></div>${row?`<p>${e(row.skipper)} · ${row.course==='stor'?'Stor':'Lille'} bane · ${e(row.class_name)}${draft.mode==='edit'?' · Ret registrering':''}</p>`:'<p>Find båden nedenfor, og tryk Måltag for at knytte denne tid til den.</p>'}<form id="active-measure-form"><div class="active-time-row"><label class="field timing-time-field">Måltid (HH:MM:SS)<input id="finish-time" name="time" type="text" inputmode="numeric" autocomplete="off" maxlength="8" pattern="[0-9]{2}:[0-9]{2}:[0-9]{2}" placeholder="HH:MM:SS" value="${e(fields.time)}" ${disabled?'disabled':''} ${draft.status==='FIN'?'required':''}></label><label class="field timing-date-field">Dato (dd-mm-åååå)<input id="finish-date" name="date" type="text" inputmode="numeric" autocomplete="off" maxlength="10" pattern="[0-9]{2}-[0-9]{2}-[0-9]{4}" placeholder="DD-MM-ÅÅÅÅ" value="${e(fields.date)}" ${disabled?'disabled':''} ${draft.status==='FIN'?'required':''}></label>${row?`<button type="button" data-timing-action="duty" ${disabled?'disabled':''}>Dommertjans</button>`:''}<button class="primary" type="submit" ${disabled||!row||draft.capturing?'disabled':''}>${draft.saving?'Gemmer…':'Bekræft'}</button>${draft.mode==='edit'?`<button type="button" data-timing-action="return" ${disabled?'disabled':''}>Flyt tilbage til ikke måltaget</button>`:''}${row?.race_mode==='pursuit'&&row&&draft.mode==='new'&&!draft.capture_id?`<button type="button" data-timing-action="order" ${disabled?'disabled':''}>Kun rækkefølge</button>`:''}</div><details class="timing-extra" ${draft.status!=='FIN'||Number(draft.penalty)?'open':''}><summary>Status og straftid</summary><div class="row"><label class="field">Status<select name="status" id="finish-status" ${disabled?'disabled':''}>${options(['FIN','DNC','DNS','DNF','RET','DSQ','DNE',...(staff()||draft.status==='DUTY'?['DUTY']:[]),...(draft.status==='RDG'?['RDG']:[])],draft.status,['FIN','DNC','DNS','DNF','RET','DSQ','DNE',...(staff()||draft.status==='DUTY'?['Dommertjans – afventer beregning']:[]),...(draft.status==='RDG'?['RDG']:[])])}</select></label><label class="field">Straftid (sekunder)<input id="finish-penalty" name="penalty" type="text" inputmode="decimal" data-number="true" value="${e(draft.penalty)}" ${disabled?'disabled':''}></label></div></details><p id="duty-notice" ${draft.status==='DUTY'?'':'hidden'}>Dommertjans registreres nu. Tiden beregnes først efter endt sejlads, når de øvrige deltageres resultater er indtastet.</p>${draft.error?`<p class="error" role="alert">${e(draft.error)} Den indtastede tid er bevaret.</p>`:''}</form></section>`;
}
function activateTimingBoat(boatId,editing=false){
  if(timingLocked())return;
  const work=timingWork(),row=state.race.entries.find(r=>r.boat_id===boatId);if(!row)return;
  const key=`boat:${boatId}`;let draft=work.drafts[key];
  if(!draft||draft.mode!==(editing?'edit':'new')){
    const loose=!editing&&activeTimingDraft()&&!activeTimingDraft().boatId?activeTimingDraft():null;
    if(loose){delete work.drafts[loose.key];draft=loose;draft.key=key;}
    else draft={key,instant:editing?(row.instant||''):now().toISOString(),request_id:uid(),penalty:editing?row.penalty:0,status:editing?row.status:'FIN'};
    draft.boatId=boatId;draft.raceId=row.race_id;draft.mode=editing?'edit':'new';
    if(editing)draft.original={instant:row.instant,status:row.status,penalty:row.penalty};
    work.drafts[key]=draft;
  }
  work.activeKey=key;saveTimingWork();renderActiveTiming();renderTimingLists();$('#active-timing').scrollIntoView({block:'nearest'});
}
function rememberActiveTiming(){
  const draft=activeTimingDraft(),form=$('#active-measure-form');if(!draft||!form||draft.saving)return;
  const before=JSON.stringify([draft.instant,draft.status,draft.penalty]);
  const raw=timingInputInstant(form),previous=draft.instant?localInput(draft.instant).slice(0,19):'';
  draft.dateInput=$('#finish-date',form).value;draft.timeInput=$('#finish-time',form).value;
  // Show whole seconds, but retain the precise click-time until date or time is changed.
  if(raw!==previous)draft.instant=raw;
  draft.status=$('[name="status"]',form).value;draft.penalty=$('[name="penalty"]',form).value;
  $('#finish-date',form).required=draft.status==='FIN';$('#finish-time',form).required=draft.status==='FIN';
  $('#duty-notice',form).hidden=draft.status!=='DUTY';$('#finish-penalty',form).disabled=draft.status!=='FIN';
  if(draft.status!=='FIN'){ $('#finish-date',form).setCustomValidity('');$('#finish-time',form).setCustomValidity(''); }
  if(before!==JSON.stringify([draft.instant,draft.status,draft.penalty]))draft.request_id=uid();
  delete draft.error;saveTimingWork();
}
function removeTimingDraft(key,roundId=state.timingRoundId){const work=timingWork(roundId);delete work.drafts[key];if(work.activeKey===key)work.activeKey=null;saveTimingWork(roundId);}
function reconcileTimingCaptures(){
  for(const draft of Object.values(timingWork().drafts)){
    if(draft.capture_request_id&&!draft.capture_id){const cap=state.race.captures.find(c=>c.request_id===draft.capture_request_id);if(cap){draft.capture_id=cap.id;draft.capturing=false;delete draft.error;}}
  }
  saveTimingWork();
}
async function refreshTiming(roundId=state.timingRoundId){
  const version=++timingRefresh,detail=await api(`/api/rounds/${roundId}/timing`);
  if(version!==timingRefresh||state.page!=='timing'||state.timingRoundId!==roundId)return;
  state.race=detail;state.timingRaceId=detail.race.id;reconcileTimingCaptures();buildTimingSearch();renderTimingLists();renderTimingCaptures();updateTimingContext();
}
async function confirmActiveTiming(orderOnly=false){
  rememberActiveTiming();const draft=activeTimingDraft();if(!draft||!draft.boatId||draft.saving||draft.capturing)return;
  const form=$('#active-measure-form');if(!orderOnly&&form&&!form.reportValidity())return;
  const roundId=state.timingRoundId,raceId=timingCourseForBoat(draft.boatId).id,key=draft.key;
  const payload={boat_id:draft.boatId,status:draft.status,penalty:draft.status==='FIN'?draft.penalty:0,request_id:draft.request_id};
  if(draft.status==='FIN'&&!orderOnly){payload.instant=draft.instant;if(draft.capture_id)payload.capture_id=draft.capture_id;}
  if(draft.status==='DUTY'&&draft.capture_id)payload.capture_id=draft.capture_id;
  if(draft.mode==='edit'){
    const row=state.race.entries.find(r=>r.boat_id===draft.boatId);
    if(JSON.stringify({instant:row.instant,status:row.status,penalty:row.penalty})!==JSON.stringify(draft.original)){draft.error='Bådens resultat er ændret. Åbn Ret igen og gennemgå den nye registrering.';renderActiveTiming();return;}
    payload.revision=timingCourseForBoat(draft.boatId).revision;payload.note='Rettet ved måltagning';
  }
  draft.saving=true;saveTimingWork();renderActiveTiming();renderTimingLists();
  try{
    await api(`/api/races/${raceId}/${draft.mode==='edit'?'edit':'finish'}`,payload);
    const wasActive=timingWork(roundId).activeKey===key;
    removeTimingDraft(key,roundId);
    if(state.page==='timing'&&state.timingRoundId===roundId){await refreshTiming(roundId);if(wasActive&&!timingWork(roundId).activeKey){renderActiveTiming();$('#boat-search')?.focus();}toast(payload.status==='DUTY'?'Dommertjans er registreret. Tiden beregnes efter endt sejlads.':'Måltiden er bekræftet og gemt.');}
  }catch(error){
    draft.saving=false;draft.error=error.message;saveTimingWork(roundId);
    if(state.page==='timing'&&state.timingRoundId===roundId){
      try{await refreshTiming(roundId);}catch{}
      const row=state.race.entries.find(r=>r.boat_id===draft.boatId);
      const same=row&&row.sequence!==null&&row.status===payload.status&&Number(row.penalty)===Number(payload.penalty)&&((!row.instant&&!payload.instant)||(row.instant&&payload.instant&&Date.parse(row.instant)===Date.parse(payload.instant)));
      if(same){removeTimingDraft(key,roundId);renderActiveTiming();toast('Måltiden er bekræftet på serveren.');}
      else if(activeTimingDraft()===draft)renderActiveTiming();
      renderTimingLists();
    }
  }
}
async function returnActiveTiming(){
  const draft=activeTimingDraft();if(!draft||draft.mode!=='edit'||draft.saving)return;
  const row=state.race.entries.find(r=>r.boat_id===draft.boatId);
  if(JSON.stringify({instant:row.instant,status:row.status,penalty:row.penalty})!==JSON.stringify(draft.original)){draft.error='Bådens resultat er ændret. Åbn Ret igen før den flyttes tilbage.';renderActiveTiming();return;}
  draft.saving=true;renderActiveTiming();
  try{await api(`/api/races/${row.race_id}/undo`,{boat_id:draft.boatId,revision:timingCourseForBoat(draft.boatId).revision});removeTimingDraft(draft.key);await refreshTiming();renderActiveTiming();toast('Båden er flyttet tilbage til ikke måltaget.');}
  catch(error){draft.saving=false;try{await refreshTiming();}catch{}const current=state.race.entries.find(r=>r.boat_id===draft.boatId);if(current?.sequence===null){removeTimingDraft(draft.key);renderActiveTiming();toast('Båden er tilbage i ikke måltaget.');}else{draft.error=error.message;saveTimingWork();renderActiveTiming();}}
}
async function newLooseTiming(takeNow){
  const work=timingWork(),roundId=state.timingRoundId,raceId=state.timingRaceId,key='loose:'+uid();
  const draft={key,boatId:null,mode:'new',instant:now().toISOString(),request_id:uid(),status:'FIN',penalty:0,capturing:takeNow,capture_request_id:takeNow?key:null};
  work.drafts[key]=draft;work.activeKey=key;saveTimingWork();renderActiveTiming();renderTimingCaptures();
  if(!takeNow){$('#finish-time')?.focus();return;}
  try{const cap=await api(`/api/races/${raceId}/capture`,{request_id:draft.capture_request_id,instant:draft.instant});draft.capture_id=cap.id;draft.capturing=false;saveTimingWork(roundId);if(state.timingRoundId===roundId&&state.page==='timing'){await refreshTiming(roundId);renderTimingCaptures();const button=$('#active-measure-form button[type="submit"]');if(button&&activeTimingDraft()===draft)button.disabled=!draft.boatId;}}
  catch(error){draft.capturing=false;draft.error=error.message;saveTimingWork(roundId);if(state.timingRoundId===roundId&&state.page==='timing'){try{await refreshTiming(roundId);}catch{}if(draft.capture_id)delete draft.error;if(activeTimingDraft()===draft)renderActiveTiming();}}
}
function activateTimingCapture(id){
  const work=timingWork(),cap=state.race.captures.find(c=>c.id===id);if(!cap)return;
  const existing=Object.values(work.drafts).find(d=>d.capture_id===id);
  const draft=existing||{key:`loose:cap:${id}`,boatId:null,mode:'new',instant:cap.instant,capture_id:id,request_id:uid(),status:'FIN',penalty:0};
  work.drafts[draft.key]=draft;work.activeKey=draft.key;saveTimingWork();renderActiveTiming();renderTimingCaptures();$('#boat-search')?.focus();
}
function updateTimingContext(){
  const context=$('#timing-context');if(!context)return;
  context.innerHTML=state.race.courses.map(r=>`<span><strong>${r.course==='stor'?'Stor':'Lille'} bane:</strong> ${number(r.distance,3)} sm · start ${clock(r.start)} · ${r.mode==='pursuit'?'Jagtstart':'Almindelig respit'}</span>`).join('');
  const locked=timingLocked();for(const button of $$('[data-timing-action="capture"],[data-timing-action="manual"]'))button.disabled=locked;
  const finish=$('[data-action="complete-round"],[data-action="reopen-round"]');if(finish){finish.dataset.action=locked?'reopen-round':'complete-round';finish.textContent=locked?'Genåbn ræs':'Afslut ræs';}
  $('#timing-status').textContent=state.race.race.completed?'Ræset er afsluttet for begge baner. Resterende både har fået DNC.':'Fælles måltagning for stor og lille bane.';
}
async function showTiming(){
  if(!staff()){state.page='results';return showResults();}
  if(!state.timingRoundId&&state.timingRaceId)state.timingRoundId=roundForRace(state.timingRaceId)?.id;
  if(!(state.data.rounds||[]).some(r=>r.id===state.timingRoundId))state.timingRoundId=chooseTimingRound()?.id;
  if(!state.timingRoundId){shell(`${intro('Måltagning','Serien har endnu ingen ræs.')}<button data-page="series">Åbn serien</button>`);return;}
  state.race=await api(`/api/rounds/${state.timingRoundId}/timing`);state.timingRaceId=state.race.race.id;
  reconcileTimingCaptures();buildTimingSearch();const race=state.race.race,locked=timingLocked();
  shell(`<section class="timing-page"><div class="timing-header"><h1>Måltagning</h1><div class="row">${admin()?'<button data-action="new-race">Indstil bane</button>':''}${admin()&&state.race.courses.length?'<button data-action="historical-calculation">Beregningsgrundlag</button>':''}${state.race.courses.length?`<button data-action="${locked?'reopen-round':'complete-round'}">${locked?'Genåbn ræs':'Afslut ræs'}</button>${!race.published?'<button data-action="publish">Offentliggør resultater →</button>':''}`:''}</div></div><label class="field timing-race-label">Ræs · begge baner<select id="timing-race">${timingRoundOptions()}</select></label><p id="timing-context" class="timing-context"></p><p id="timing-status"></p>${state.race.unconfigured_courses.length?`<p class="results-warning">Indstil også ${state.race.unconfigured_courses.map(c=>c==='stor'?'stor bane':'lille bane').join(' og ')} med de tilmeldte både, før ræset afsluttes.</p>`:''}${state.race.courses.length?`<div class="time-tools"><strong id="live-clock">${clock(now())}</strong><button class="primary" data-timing-action="capture" ${locked?'disabled':''}>Tag tid nu</button><button data-timing-action="manual" ${locked?'disabled':''}>Indtast tid uden båd</button><span id="connection">Forbundet</span></div><div id="timing-captures" class="timing-captures"></div><div id="active-timing"></div><label class="field timing-search-label">Søg i begge lister<input id="boat-search" class="search-input" type="search" placeholder="Sejlnummer, bådnavn, skipper…" autocomplete="off" value="${e(state.query)}"></label><div class="timing-lists"><section><h2>Ikke måltaget <span id="unmeasured-count"></span></h2><div id="unmeasured-boats"></div></section><section><h2>Måltaget / registreret <span id="measured-count"></span></h2><div id="measured-boats"></div></section></div>`:'<p>Begge baner mangler dato, start og baneforløb. Indstil banen for at begynde måltagning.</p>'}</section>`);
  updateTimingContext();renderActiveTiming();renderTimingCaptures();renderTimingLists();
}
async function completionDialog(reopen=false){
  if(!reopen&&Object.keys(timingWork().drafts).length){toast('Bekræft eller annullér de aktive tider først.');return;}
  await refreshTiming();
  if(!reopen&&state.race.captures.length){toast('Tildel eller kassér alle tidsstempler uden båd før afslutning.');return;}
  const missing=state.race.entries.filter(row=>row.sequence===null);
  openDialog(reopen?'Genåbn ræset for begge baner':'Afslut ræset for begge baner',`<form id="round-completion-form" data-reopen="${reopen}" class="stack"><p><strong>${e(state.race.race.label)} · stor og lille bane</strong></p>${reopen?'<p>Automatiske DNC-registreringer fjernes, så bådene igen kan måltages. Faktiske måltider og manuelt registrerede statusser bevares. Offentliggjorte resultater bliver kladder igen.</p>':`<p>${missing.length} ikke-måltagne både får DNC. Gemte tider bevares. Dommertider beregnes automatisk ved afslutningen.</p>${missing.length?`<p>${missing.map(row=>e(row.name)+' ('+(row.course==='stor'?'stor':'lille')+' bane)').join(', ')}</p>`:''}`}<div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">${reopen?'Genåbn ræs':'Afslut ræs'}</button></div></form>`);
}
function showBoats(){const fleet=state.data.boats.filter(b=>b.course===state.course);shell(`${intro('Både & DH','Både i den valgte serie. Bådstamdata og handicapsæt genbruges på tværs af serier.',admin()?'<div class="row"><button data-action="catalog">Tilføj fra fælles bådliste</button><button class="primary" data-action="new-boat">+ Opret båd</button></div>':'')}<div class="filters">${selectors()}<span class="subtle">Tomme felter betyder, at tallet mangler.</span></div><section class="panel"><div class="table-scroll"><table><thead><tr><th>Båd / skipper</th><th>Sejlnr.</th><th>Løb</th>${keys.map(k=>`<th class="numeric">${k}</th>`).join('')}<th class="numeric">GPH</th><th class="numeric">Ekstra handicap</th>${admin()?'<th></th>':''}</tr></thead><tbody>${fleet.map(b=>`<tr><td><div class="boatname">${e(b.name)}</div><div class="boatdetail">${e(b.skipper)} · ${e(b.boat_type)}</div></td><td>${e(b.sail_number)}</td><td>${e(b.class_name)}</td>${keys.map(k=>`<td class="numeric">${number(b.ratings[k],1)}</td>`).join('')}<td class="numeric">${gphNumber(b.gph)}</td><td class="numeric">+${number(b.extra_handicap)} s</td>${admin()?`<td><button class="small" data-edit-boat="${b.id}">Rediger</button></td>`:''}</tr>`).join('')}</tbody></table></div></section><p class="subtle">DH-tal angives i sekunder pr. sømil. Ændringer gælder nye sejladser. Historiske deltagelser beholder deres gemte tal, medmindre en administrator udtrykkeligt vælger genberegning.</p>`);}
async function showAdmin(){if(!staff()){state.page='results';return showResults();}const references=[...new Set(state.data.races.map(r=>r.reference))];const users=owner()?await api('/api/users'):[];const history=admin()?await api('/api/audit'):[];shell(`${intro('Administration','Serier, indstillinger for den valgte serie, adgang og ændringshistorik.')}<div class="admin-grid"><div class="stack">${admin()?`<section class="panel padded"><div class="row between"><h2>Serier</h2><button class="primary" data-action="new-series">Opret serie</button></div><div class="admin-series-list">${state.data.series.map(series=>`<div class="row between admin-series-row"><span><strong>${e(series.name)}</strong> · ${series.year}</span><button data-select-series="${series.id}" aria-label="${series.id===state.seriesId?'Valgt serie:':'Vælg serie:'} ${e(series.name)}" ${series.id===state.seriesId?'disabled':''}>${series.id===state.seriesId?'Valgt':'Vælg'}</button></div>`).join('')}</div></section>`:''}<section class="panel padded"><h2>Seriens indstillinger</h2><p>${e(currentSeries().name)}</p><p class="subtle">Referencehandicap: <strong>${number(currentSeries().reference,1)} s/sm</strong><br>Fratræk efter ${JSON.parse(currentSeries().discards).join(', ')||'ingen'} sejladser.</p>${references.length>1?'<div class="notice">Sejladserne bruger forskellige referencehandicap.</div>':''}${admin()?'<div class="row"><button data-action="edit-series">Rediger serie</button><button class="primary" data-action="new-rounds">Tilføj ræs</button><button data-page="series">Vis ræs og løb</button><button data-action="reference">Reference på eksisterende ræs</button></div>':''}</section><section class="panel padded"><h2>Brugere og godkendelser</h2><p class="subtle">Nye konti godkendes af hummesse@gmail.com via Google.</p>${owner()?users.map(u=>`<div class="audit-entry"><strong>${e(u.name)}</strong> · ${e(u.email)}<p class="subtle">${e(u.provider)} · ${u.approved?'Godkendt':'Afventer godkendelse'}</p>${u.email==='hummesse@gmail.com'&&u.provider==='google'?'<span class="pill">Ejer</span>':`<div class="row"><select aria-label="Rolle for ${e(u.name)}" id="user-role-${u.id}"><option value="recorder" ${u.role==='recorder'?'selected':''}>Resultatindtaster</option><option value="admin" ${u.role==='admin'?'selected':''}>Administrator</option></select><button class="small primary" data-user="${u.id}" data-approved="true">${u.approved?'Gem rolle':'Godkend'}</button>${u.approved?`<button class="small danger" data-user="${u.id}" data-approved="false">Fjern adgang</button>`:''}</div>`}</div>`).join(''):'<p class="subtle">Godkendelseslisten er kun tilgængelig for ejeren.</p>'}</section></div><section class="panel padded"><h2>Ændringshistorik</h2><p class="subtle">Seneste 200 handlinger på serveren.</p>${history.map(h=>`<details class="audit-entry"><summary>${new Date(h.at*1000).toLocaleString('da-DK')} · ${e(h.action)} · ${e(h.name||'Lokal demo')}</summary><pre>${e(JSON.stringify(JSON.parse(h.payload),null,2))}</pre></details>`).join('')||'<p class="subtle">Ingen synlige ændringer endnu.</p>'}</section></div>`);}
async function render(historyMode='replace'){try{if(!staff())state.page='results';if(state.page==='results')await showResults(historyMode);else if(state.page==='timing')await showTiming();else if(state.page==='starts')await showStarts();else if(state.page==='boats')showBoats();else if(state.page==='series')showSeries();else await showAdmin();enhancePage();}catch(error){toast(error.message);}}
function openDialog(title,body){dialog.innerHTML=`<div class="dialog-head"><h2>${title}</h2><button data-action="close-dialog" aria-label="Luk">×</button></div><div class="body">${body}<div class="dialog-error" role="alert"></div></div>`;if(!dialog.open)dialog.showModal();fitTableColumns();}
function dialogError(error){$('.dialog-error',dialog).innerHTML=`<p class="error">${e(error.message||error)}</p>`;}
function showLogin(){if(state.data.preview_login_path){openDialog('Log ind',`<div class="login-options"><a class="preview-login" href="${e(state.data.preview_login_path)}">Log ind som administrator</a></div><p class="subtle">Midlertidig adgang til det lokale preview i op til to timer. Adgangen ophører, når previewserveren stoppes.</p>`);return;}openDialog('Log ind på Onsdagsbanen',`<p class="subtle">Brug din egen konto. Første gang oprettes en bruger, som hummesse@gmail.com skal godkende.</p><div class="login-options">${state.data.preview_login_path?`<a class="preview-login" href="${e(state.data.preview_login_path)}">Midlertidig administrator</a>`:''}<a href="/auth/google" class="${state.data.providers.google?'':'disabled'}">Fortsæt med Google</a><a href="/auth/facebook" class="${state.data.providers.facebook?'':'disabled'}">Fortsæt med Facebook</a></div>${!state.data.providers.google&&!state.data.providers.facebook?'<p class="notice info">Login åbner, når hjemmesiden er sat i drift.</p>':''}<p class="subtle">Resultater kan ses uden login. Resultatindtastere kan registrere og offentliggøre tider. Administratorer kan også oprette sejladser og administrere stamdata.</p>`);}
async function selectSeries(identity){
  state.seriesId=Number(identity);state.className=null;state.raceId=null;state.timingRaceId=null;state.timingRoundId=null;state.startRaceId=null;state.query='';
  const url=new URL(location.href);url.searchParams.set('series',String(state.seriesId));url.searchParams.delete('class');url.searchParams.delete('race');history.pushState(null,'',url);
  await load();await render();
}
function showSeries(){
  const races=new Map(state.data.races.map(r=>[r.id,r]));
  function courseCell(round,course){
    const child=round.courses.find(c=>c.course===course),race=races.get(child.race_id);
    return `<td><div class="series-heats">${child.heats.map(heat=>`<button class="heat-link" data-heat="${heat.id}">${e(heat.name)} · ${heat.boat_ids.length} ${heat.boat_ids.length===1?'båd':'både'}</button>`).join('')}</div><div class="series-course-actions">${race?`<span>${date(race.start)} · ${clock(race.start)} · ${race.mode==='pursuit'?'Omvendt respit':'Almindelig respit'}</span><button class="small" data-open-timing="${race.id}">Måltagning</button>`:`<span>Start ${e(child.start_time||'–')} · bane ikke indstillet</span><button class="small" data-configure-round="${round.id}" data-configure-course="${course}">Indstil bane</button>`}</div></td>`;
  }
  shell(`<section class="series-page"><div class="results-heading"><h1>${e(currentSeries().name)}</h1><div class="row"><button class="primary" data-action="new-rounds">Tilføj ræs</button><button data-action="edit-series">Rediger serie</button></div></div><p>Vælg et løb for at tilføje både. Indstil dato, startform og baneforløb på hver bane før måltagning.</p><div class="table-scroll"><table class="series-table"><thead><tr><th scope="col">Ræs</th><th scope="col">Stor bane</th><th scope="col">Lille bane</th></tr></thead><tbody>${(state.data.rounds||[]).map(round=>`<tr><th scope="row">${e(round.label)}<div class="round-date">${round.race_date?danishDay(round.race_date):'Dato ikke sat'}</div><button class="small" data-round-starts="${round.id}">Rediger ræs</button><label class="field round-system-label">Beregningsmål<select data-round-system="${round.id}" aria-label="Beregningsmål ${e(round.label)}">${options(['circle','gph','updown'],round.handicap_system||'circle',['Cirkelmål','GPH','Op/ned-mål'])}</select></label></th>${courseCell(round,'stor')}${courseCell(round,'lille')}</tr>`).join('')}</tbody></table></div></section>`);
}
function startFields(values={},prefix='default_start_'){
  return `<div class="form-grid">${['stor','lille'].map(course=>`<label class="field">${course==='stor'?'Stor':'Lille'} bane · start (HH:MM:SS)<input name="${prefix+course}" type="text" data-start-time inputmode="numeric" maxlength="8" pattern="[0-9]{2}:[0-9]{2}:[0-9]{2}" value="${e(values[prefix+course]||values[course]||(course==='stor'?'18:30:00':'18:35:00'))}" required></label>`).join('')}</div>`;
}
function normalizeStartTime(field,complete=false){
  field.value=field.value.replace(/[.,]/g,':');if(complete&&/^\d{2}:\d{2}$/.test(field.value))field.value+=':00';
  field.setCustomValidity(field.value&&!/^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$/.test(field.value)?'Angiv et gyldigt klokkeslæt som HH:MM:SS.':'');
}
function danishDay(day){return day?day.split('-').reverse().join('-'):'';}
function isoDay(value){
  if(!value.trim())return null;
  const match=/^(\d{2})-(\d{2})-(\d{4})$/.exec(value.trim().replace(/[./]/g,'-'));
  if(!match)throw new Error('Angiv datoen som DD-MM-ÅÅÅÅ.');
  const [,d,m,y]=match;return `${y}-${m}-${d}`;
}
async function roundStartsDialog(id){
  const round=state.data.rounds.find(r=>r.id===id),detail=await api(`/api/rounds/${id}/timing`);
  const locked=!!round.completed||detail.entries.some(r=>r.sequence!==null&&r.status!=='DUTY')||detail.captures.length>0;
  const times=Object.fromEntries(round.courses.map(c=>[c.course,c.start_time]));
  openDialog(`Ræs ${e(round.label)} · dato og starttider`,`<form id="round-starts-form" class="stack"><input type="hidden" name="round_id" value="${id}"><label class="field">Dato (dd-mm-åååå)<input name="race_date" type="text" inputmode="numeric" maxlength="10" placeholder="DD-MM-ÅÅÅÅ" value="${danishDay(round.race_date)}"></label>${startFields(times,'start_')}<p class="subtle">Disse tider gælder kun ${e(round.label)}. Seriens standardtider ændres ikke.</p>${locked?'<p>Der er allerede måltagning eller resultater i ræset. Starttiderne er beskyttet.</p>':''}<div class="dialog-actions"><button type="button" data-action="close-dialog">Luk</button><button class="primary" ${locked?'disabled':''}>Gem ræsindstillinger</button></div></form>`);
  if(locked)for(const input of $$('#round-starts-form input'))input.disabled=true;
}
async function catalogDialog(){
  state.catalog=await api(`/api/boats/catalog?series_id=${state.seriesId}`);
  openDialog('Tilføj fra fælles bådliste',`<form id="catalog-form" class="stack"><p><strong>${e(currentSeries().name)}</strong></p><div class="form-grid"><label class="field">Bane<select name="course" id="catalog-course">${options(['stor','lille'],state.course,['Stor bane','Lille bane'])}</select></label><label class="field">Løb i denne serie<select name="class_name" id="catalog-class">${options(classOptions(),classOptions()[0])}</select></label></div><label class="field">Find både<input type="search" id="catalog-search" placeholder="Båd, sejlnummer, skipper…"></label><button type="button" data-action="catalog-all">Vælg alle viste</button><div class="boat-checks" id="catalog-boats">${state.catalog.map(b=>`<label class="check catalog-boat" data-search="${e(normalize([b.name,b.skipper,b.sail_number,b.boat_type,b.club].join(' ')))}"><input name="profile_ids" type="checkbox" value="${b.id}" ${b.registration_id?'disabled':''}><span><strong>${e(b.name)}</strong> · ${e(b.sail_number)}<br>${e(b.skipper)}${b.registration_id?' · allerede i serien':''}</span></label>`).join('')||'<p>Bådlisten er tom. Opret den første båd under Både &amp; DH.</p>'}</div><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Tilføj valgte både til serien</button></div></form>`);
}
function roundsDialog(){openDialog('Tilføj ræs til serien',`<form id="rounds-form" class="stack"><p><strong>${e(currentSeries().name)}</strong></p><label class="field">Ræs<textarea name="round_labels" required placeholder="R19–R24 eller ét navn pr. linje"></textarea></label><h3>Starttider til de nye ræs</h3>${startFields({stor:currentSeries().default_start_stor,lille:currentSeries().default_start_lille},'start_')}<p>Hvert ræs får egne starttider og begge baners løb. Tiderne kan rettes på det enkelte ræs.</p><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Tilføj ræs</button></div></form>`);}
function heatDialog(id){
  const round=(state.data.rounds||[]).find(r=>r.courses.some(c=>c.heats.some(h=>h.id===id)));
  const course=round?.courses.find(c=>c.heats.some(h=>h.id===id)),heat=course?.heats.find(h=>h.id===id);if(!heat)return;
  const fleet=state.data.boats,otherIds=new Set(round.courses.flatMap(c=>c.heats).filter(h=>h.id!==id).flatMap(h=>h.boat_ids));
  openDialog(`${e(round.label)} · ${course.course==='stor'?'Stor':'Lille'} bane · ${e(heat.name)}`,`<form id="heat-members-form" class="stack"><input type="hidden" name="heat_id" value="${id}"><p>En båd kan kun være tilmeldt ét løb denne aften, på tværs af begge baner.</p><div class="boat-checks">${fleet.map(b=>`<label class="check"><input type="checkbox" name="boat_ids" value="${b.id}" ${heat.boat_ids.includes(b.id)?'checked':''} ${otherIds.has(b.id)?'disabled':''}><span><strong>${e(b.name)}</strong> · ${e(b.sail_number)}<br>${e(b.skipper)}${otherIds.has(b.id)?' · tilmeldt et andet løb':''}</span></label>`).join('')||'<p>Ingen både på denne bane endnu. Opret dem under Både &amp; DH.</p>'}</div><label class="check"><input type="checkbox" name="whole_series">Brug også i seriens øvrige ræs uden resultater</label><p class="subtle">Uden markering ændres kun ${e(round.label)}. Registrerede resultater og gemte handicaptal beskyttes.</p><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Gem deltagere</button></div></form>`);
}
async function raceDialog(roundId=null,course=state.course){
  const available=(state.data.rounds||[]).filter(r=>r.courses.some(c=>!c.race_id));
  if(!available.length){state.page='series';showSeries();toast('Alle planlagte baner er indstillet. Tilføj ræs på seriens side.');return;}
  const planned=available.find(r=>r.id===Number(roundId))||available.find(r=>r.courses.some(c=>c.course===course&&!c.race_id))||available[0];
  if(planned.courses.find(c=>c.course===course)?.race_id)course=planned.courses.find(c=>!c.race_id).course;
  const sibling=state.data.races.find(r=>planned.courses.some(c=>c.race_id===r.id));const day=planned.race_date||(sibling?timingDay(sibling.start):'');const plannedTime=planned.courses.find(c=>c.course===course)?.start_time||currentSeries()['default_start_'+course];
  route=[];openDialog('Indstil bane i et ræs',`<form id="race-form" class="stack"><div class="form-grid"><label class="field">Ræs<select id="race-round" name="round_id">${options(available.map(r=>r.id),planned.id,available.map(r=>r.label))}</select></label><label class="field">Bane<select name="course" id="race-course">${options(['stor','lille'],course,['Stor bane','Lille bane'])}</select></label><label class="field">Startform<select name="mode">${options(['normal','pursuit'],'normal',['Almindelig respit','Jagtstart / omvendt respit'])}</select></label><label class="field">Dato (dd-mm-åååå)<input name="race_date" type="text" inputmode="numeric" maxlength="10" placeholder="DD-MM-ÅÅÅÅ" value="${danishDay(day)}" required></label><label class="field">Første start (HH:MM:SS)<input name="start_time" type="text" data-start-time inputmode="numeric" maxlength="8" pattern="[0-9]{2}:[0-9]{2}:[0-9]{2}" value="${e(plannedTime)}" required></label><label class="field">Beregningsmål · hele ræset<select name="handicap_system" id="race-system">${options(['circle','gph','updown'],planned.handicap_system||'circle',['Cirkelmål','GPH','Op/ned-mål'])}</select></label><label class="field">Vindkategori<select name="wind_category" id="race-wind"><option value="" disabled>Ikke relevant ved GPH</option>${options(['L','M','H'],'M',['Let','Mellem','Hård'])}</select></label><label class="field">Referencehandicap (s/sm)<input name="reference" type="text" inputmode="decimal" data-number="true" value="${currentSeries().reference}" required></label></div><div><h3>Rundingsmærker i rækkefølge</h3><p class="subtle">Start på linjen J–X. Mål ved X. Tryk på mærkerne, også ved gentagelser.</p><div class="row">${state.data.marks.filter(m=>!['J','X'].includes(m.name)).map(m=>`<button type="button" data-mark="${e(m.name)}">${e(m.name)}</button>`).join('')}<button type="button" class="small" data-action="route-undo">↶</button></div><div id="route-preview" class="route-builder">Vælg mindst ét rundingsmærke.</div><p id="route-distance" class="distance">– sm</p></div><div><h3>Løb og deltagere</h3><div id="race-boats"></div><p class="subtle">Deltagere tilføjes til løbene på siden Serie.</p></div><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Gem bane og gå til måltagning</button></div></form>`);updateRaceBoats();
}
function fillPlannedStart(){const round=state.data.rounds.find(r=>r.id===Number($('#race-round').value)),course=$('#race-course').value,sibling=state.data.races.find(r=>round.courses.some(c=>c.race_id===r.id));$('[name="race_date"]',$('#race-form')).value=danishDay(round.race_date||(sibling?timingDay(sibling.start):''));$('[name="start_time"]',$('#race-form')).value=round.courses.find(c=>c.course===course)?.start_time||currentSeries()['default_start_'+course];}
function updateRaceBoats(){
  const round=state.data.rounds.find(r=>r.id===Number($('#race-round').value)),courseField=$('#race-course'),system=$('#race-system').value,wind=$('#race-wind');
  wind.disabled=system==='gph';if(wind.disabled)wind.value='';else if(!wind.value)wind.value='M';
  const key=system==='gph'?'GPH':(system==='circle'?'TACI':'TAUD')+wind.value;
  for(const option of courseField.options)option.disabled=!!round.courses.find(c=>c.course===option.value)?.race_id;
  if(courseField.selectedOptions[0]?.disabled)courseField.value=round.courses.find(c=>!c.race_id)?.course;
  const child=round.courses.find(c=>c.course===courseField.value);
  $('#race-boats').innerHTML=child.heats.map(heat=>`<div class="race-fleet"><strong>${e(heat.name)}</strong><p>${heat.boat_ids.map(id=>{const b=state.data.boats.find(b=>b.id===id);return b?e(b.name)+((key==='GPH'?b.gph:b.ratings[key])==null?` (${e(key)} mangler)`:''):'';}).filter(Boolean).join(', ')||'Ingen tilmeldte både'}</p></div>`).join('');
}
async function updateRoute(){const serial=++requestSerial;$('#route-preview').innerHTML=route.length?`<span>J–X → </span>${route.map((r,i)=>`<button class="route-token" type="button" data-remove-mark="${i}" title="Fjern mærke">${e(r)} ×</button>`).join('')}<span> → X</span>`:'Vælg mindst ét rundingsmærke.';if(!route.length){$('#route-distance').textContent='– sm';return;}const result=await api('/api/geometry',{route});if(serial===requestSerial)$('#route-distance').textContent=number(result.distance,3)+' sm';}
async function boatDialog(id){const b=state.data.boats.find(b=>b.id===id)||{course:state.course,ratings:{},extra_handicap:0};const [history,changes,sets]=id?await Promise.all([api(`/api/boats/history?id=${id}`),api(`/api/boats/changes?id=${id}`),api(`/api/boats/handicaps?id=${id}`)]):[[],[],[]];state.boatSets=sets;openDialog(id?`Rediger ${e(b.name)}`:'Opret båd',`<form id="boat-form" data-id="${id||''}" class="stack"><div class="form-grid">${[['name','Bådnavn'],['skipper','Skipper'],['sail_number','Sejlnummer'],['boat_type','Bådtype'],['club','Klub']].map(([k,label])=>`<label class="field">${label}<input name="${k}" value="${e(b[k]||'')}" ${k==='club'?'':'required'} maxlength="160"></label>`).join('')}<label class="field">Bane<select name="course" id="boat-course">${options(['stor','lille'],b.course,['Stor bane','Lille bane'])}</select></label><label class="field">Løb i serien<select name="class_name" id="boat-class" required>${options(classOptions(b.course),b.class_name||classOptions(b.course)[0])}</select></label><label class="field">Ekstra handicap (sekunder)<input type="text" inputmode="decimal" data-number="true" name="extra_handicap" min="0" max="3600" step="1" value="${b.extra_handicap||0}" required></label></div><h3>Handicapsæt</h3>${id?`<label class="field">Bådens standardsæt<select name="handicap_set_id" id="boat-set"><option value="">Nyt eller ændret sæt</option>${sets.map(set=>`<option value="${set.id}" ${set.id===b.current_handicap_set_id?'selected':''}>Sæt #${set.id}${set.id===b.current_handicap_set_id?' · aktuelt standardsæt':''}</option>`).join('')}</select></label>`:''}<h3>DH-tal · sekunder pr. sømil</h3><div class="rating-grid">${keys.map((key,i)=>`<label class="field">${key}<span class="subtle">${keyNames[i]}</span><input name="${key}" type="text" inputmode="decimal" data-number="true" min="0.1" step="0.1" value="${b.ratings[key]??''}" placeholder="Ikke udfyldt"></label>`).join('')}</div><label class="field">GPH · beregnet (sekunder pr. sømil)<input id="boat-gph" readonly aria-describedby="gph-description"></label><p id="gph-description" class="subtle">(3 × TACIL + 12 × TACIM + 5 × TACIH) / 20. Alle tre cirkeltal kræves.</p><div class="notice info">Ændringer gælder nye sejladser. Eksisterende sejladser beholder deres gemte DH-tal og ekstra handicap.</div>${id&&history.length?`<label class="check"><input id="recalculate-toggle" name="recalculate" type="checkbox">Jeg ønsker også at genberegne valgte tidligere sejladser</label><div id="historical-options" hidden><h3>Vælg de ræs, som skal rettes</h3>${history.map(r=>`<label class="check"><input type="checkbox" name="historical_races" value="${r.id}">${e(r.label)} · ${date(r.start)} · ${e(r.rating_key)} · Hdcp. ${number(r.rating,1)}</label>`).join('')}<p class="subtle">For importerede resultater skal du vælge DH-tallet for hver sejlads. De berørte resultater bliver kladder og skal offentliggøres igen.</p>${history.filter(r=>r.rating_key==='import').map(r=>`<label class="field">${e(r.label)} · ${date(r.start)} · anvendt ${number(r.rating,1)}<select name="historical_${r.id}"><option value="">Vælg DH-tal udtrykkeligt</option>${options(keys,'')}</select></label>`).join('')}<button type="button" data-action="recalculation-preview">Forhåndsvis ændringer</button><div id="recalculation-preview"></div><input type="hidden" name="recalculation_token"></div>`:''}<div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Gem båd</button></div></form>`);updateBoatGph();if(id&&sets.length)$('#boat-form .dialog-actions').insertAdjacentHTML('beforebegin',`<details><summary>Bådens handicapsæt (${sets.length})</summary>${sets.map(set=>`<div class="audit-entry"><strong>Sæt #${set.id}</strong> · ${keys.filter(key=>set.ratings[key]!=null).map(key=>e(key)+' '+number(set.ratings[key],1)).join(' · ')} · ekstra +${number(set.extra_handicap)} s<p>${set.races.length?set.races.map(r=>e(r.series_name)+' / '+e(r.label)+' / '+e(r.class_name)).join(', '):'Ikke brugt i et ræs'}</p></div>`).join('')}</details>`);if(id&&changes.length)$('#boat-form .dialog-actions').insertAdjacentHTML('beforebegin',`<details><summary>Ændringer i stamdata (${changes.length})</summary>${changes.map(change=>`<details class="audit-entry"><summary>${new Date(change.at*1000).toLocaleString('da-DK')} · ${e(change.name||'Administrator')}</summary><pre>${e(JSON.stringify(JSON.parse(change.payload),null,2))}</pre></details>`).join('')}</details>`);}
function updateBoatGph(){
  const output=$('#boat-gph');if(!output)return;
  const values=['TACIL','TACIM','TACIH'].map(key=>{const value=$(`[name="${key}"]`,$('#boat-form')).value.replace(',','.');return /^\d+(?:\.\d+)?$/.test(value)&&Number(value)>0?Number(value):null;});
  output.value=values.some(value=>value===null)?'Mangler TACIL, TACIM eller TACIH':gphNumber(Math.round((3*values[0]+12*values[1]+5*values[2])/2+1e-9)/10);
}
function boatFormData(){const form=$('#boat-form'),fields=Object.fromEntries(new FormData(form));return {...fields,id:form.dataset.id?Number(form.dataset.id):undefined,series_id:state.seriesId,ratings:Object.fromEntries(keys.map(k=>[k,fields[k]||null])),extra_handicap:Number(fields.extra_handicap),recalculate:!!fields.recalculate,race_ids:new FormData(form).getAll('historical_races').map(Number),historical_rating_keys:Object.fromEntries(Object.entries(fields).filter(([k])=>k.startsWith('historical_')).map(([k,v])=>[k.slice(11),v]))};}
function referenceDialog(){openDialog('Referencehandicap',`<form id="reference-form" class="stack"><p class="subtle">Seriens værdi bruges ved oprettelse af nye sejladser. Du kan også ensrette alle eksisterende sejladser. På samme distance får alle både samme tidsforskydning, og stillingen bevares.</p><label class="field">Seriens reference (s/sm)<input type="text" inputmode="decimal" data-number="true" name="reference" min="0.1" step="0.1" value="${currentSeries().reference}" required></label><label class="check"><input type="checkbox" name="apply_all">Sæt også denne reference på alle sejladser i serien</label><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Gem seriens reference</button></div></form><hr><h3>Indstil én sejlads</h3><form id="race-reference-form" class="stack"><label class="field">Sejlads<select name="race_id">${raceOptions(state.data.races,state.data.races[0]?.id)}</select></label><label class="field">Reference (s/sm)<input name="reference" type="text" inputmode="decimal" data-number="true" step="0.1" min="0.1" required value="${state.data.races[0]?.reference||currentSeries().reference}"></label><button>Gem for denne sejlads</button></form>`);}
function seriesSettingsDialog(){
  const series=currentSeries(),hasRaces=state.data.races.length>0;
  openDialog('Rediger den valgte serie',`<form id="series-settings-form" class="stack"><label class="field">Serienavn<input name="name" required maxlength="100" value="${e(series.name)}"></label><div class="form-grid"><label class="field">År<input name="year" type="text" inputmode="numeric" data-number="true" required value="${series.year}" ${hasRaces?'disabled':''}></label><label class="field">Standardreference til nye sejladser (s/sm)<input name="reference" type="text" inputmode="decimal" data-number="true" required value="${series.reference}"></label></div><h3>Standardstarttider til nye ræs</h3>${startFields(series)}<label class="field">Fratræk efter antal sejladser (kommasepareret)<input name="discards" value="${e(JSON.parse(series.discards).join(', '))}" placeholder="6, 9, 12, 15, 18"></label><p class="subtle">Fratræk gælder seriens pointstilling. Standardreferencen bruges til nye sejladser. Eksisterende sejladser beholder deres reference.</p>${hasRaces?'<p class="subtle">Årstallet er låst, fordi serien har indstillede sejladser.</p>':''}<div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Gem seriens indstillinger</button></div></form>`);
}
function discardsInput(value){
  const parts=value.trim()?value.trim().split(/[\s,;]+/):[];
  if(parts.some(part=>!/^\d+$/.test(part)))throw new Error('Angiv fratræk som hele antal sejladser, fx 6, 9, 12.');
  return parts.map(Number);
}
function seriesDialog(){openDialog('Opret ny serie',`<form id="series-form" class="stack"><label class="field">Serienavn<input name="name" required maxlength="100" placeholder="Onsdagsbanen 2027"></label><div class="form-grid"><label class="field">År<input name="year" type="text" inputmode="numeric" data-number="true" value="${new Date().getFullYear()+1}" required></label><label class="field">Referencehandicap (s/sm)<input name="reference" type="text" inputmode="decimal" data-number="true" value="1000" required></label></div><h3>Standardstarttider</h3>${startFields()}<label class="field">Ræs i serien<textarea name="round_labels" required rows="2">R1–R18</textarea></label><p class="subtle">Skriv et interval som R1–R18 eller ét ræsnavn pr. linje. Hvert ræs får begge baner med løbene nedenfor.</p><div class="form-grid"><label class="field">Løb på stor bane · ét pr. linje<textarea name="stor_fleets" required rows="2">Stor bane 1
Stor bane 2</textarea></label><label class="field">Løb på lille bane · ét pr. linje<textarea name="lille_fleets" required rows="2">Lille bane 1
Lille bane 2</textarea></label></div><label class="field">Fratræk efter antal sejladser (kommasepareret)<input name="discards" placeholder="6, 9, 12, 15, 18"></label><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Opret serie med ræs og løb</button></div></form>`);}
async function historicalCalculationDialog(raceId=null){
  await refreshTiming();
  if(state.race.courses.length>1&&!raceId){
    openDialog('Vælg bane for historisk beregning',`<form id="course-action-form" class="stack"><label class="field">Bane<select name="race_id">${state.race.courses.map(r=>`<option value="${r.id}">${r.course==='stor'?'Stor':'Lille'} bane · ${e(r.label)}</option>`).join('')}</select></label><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Åbn beregningsgrundlag</button></div></form>`);return;
  }
  state.historicalRace=await api(`/api/races/${raceId||state.timingRaceId}`);
  openDialog('Historisk beregningsgrundlag',`<form id="calculation-form" class="stack"><p><strong>${e(state.historicalRace.race.label)} · ${state.historicalRace.race.course==='stor'?'Stor':'Lille'} bane</strong>. Genberegningen bruger sejladsens gemte deltagere, handicaptal, distancer og faktiske start- og måltider.</p><label class="field">Ret en deltagers historiske handicap<select id="calculation-boat" name="boat_id"><option value="">Bevar alle gemte handicaptal</option>${state.historicalRace.entries.map(row=>`<option value="${row.boat_id}">${e(row.name)} · ${e(row.sail_number)} · ${e(row.class_name)}</option>`).join('')}</select></label><div id="calculation-boat-fields"></div><label class="field">Årsag til genberegning<input name="note" required maxlength="500" placeholder="Fx fejl i målebrev anvendt ved R4"></label><p class="subtle">Nutidens stamdata og deltagelse bruges ikke som erstatning. Kun denne sejlads ændres og skal derefter offentliggøres igen. Dommertid skal beregnes på ny.</p><button type="button" data-action="historical-preview">Forhåndsvis genberegning</button><div id="calculation-preview"></div><input type="hidden" name="token" id="calculation-token"><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary" disabled>Genberegn kun denne sejlads</button></div></form>`);
}
async function updateHistoricalBoat(){
  const row=state.historicalRace.entries.find(r=>r.boat_id===Number($('#calculation-boat').value));
  if(!row){$('#calculation-boat-fields').innerHTML='';return;}
  const sets=await api(`/api/boats/handicaps?id=${row.boat_id}`);if(Number($('#calculation-boat').value)!==row.boat_id)return;state.historicalSets=sets;
  const saved={...row.handicap_set.ratings};saved[state.historicalRace.race.rating_key]=row.rating;
  $('#calculation-boat-fields').innerHTML=`<label class="field">Bådens handicapsæt<select name="handicap_set_id" id="historical-set"><option value="">Nyt eller ændret sæt</option>${sets.map(set=>`<option value="${set.id}" ${set.id===row.handicap_set_id?'selected':''}>Sæt #${set.id}${set.id===row.handicap_set_id?' · anvendt i dette ræs':''}</option>`).join('')}</select></label><p>Gemte værdier fra sejladsen · ${number(row.distance,3)} sm · start ${clock(row.start)}. Tomme tal er ukendte, ikke nul.</p><div class="rating-grid">${keys.map(key=>`<label class="field">${key}${key===state.historicalRace.race.rating_key?' · anvendt':''}<input name="${key}" type="text" inputmode="decimal" data-number="true" value="${saved[key]??''}" ${key===state.historicalRace.race.rating_key||(state.historicalRace.race.rating_key==='GPH'&&['TACIL','TACIM','TACIH'].includes(key))?'required':''}></label>`).join('')}</div>${keys.includes(state.historicalRace.race.rating_key)||state.historicalRace.race.rating_key==='GPH'?'':`<label class="field">Anvendt Hdcp.<input name="rating" type="text" inputmode="decimal" data-number="true" value="${row.rating??''}" required></label>`}<label class="field">Gemt ekstra handicap (sekunder)<input name="extra_handicap" type="text" inputmode="numeric" data-number="true" value="${row.extra_handicap}" required></label>`;
}
function historicalData(){
  const form=$('#calculation-form'),data=Object.fromEntries(new FormData(form));
  return {...data,boat_id:data.boat_id?Number(data.boat_id):null,ratings:Object.fromEntries(keys.filter(k=>k in data).map(k=>[k,data[k]||null]))};
}
async function previewHistorical(){
  const form=$('#calculation-form');if(!form.reportValidity())return;
  const result=await api(`/api/races/${state.historicalRace.race.id}/calculation-preview`,historicalData());
  $('#calculation-token').value=result.token;
  $('#calculation-preview').innerHTML=`<p>${e(result.notice)}</p><div class="table-scroll"><table><thead><tr><th>Båd</th><th>Hdcp. før → efter</th><th>Beregnet tid før → efter</th></tr></thead><tbody>${result.changes.map(row=>`<tr><td>${e(row.name)}</td><td>${number(row.old_rating,1)} → ${number(row.rating,1)}</td><td>${seconds(row.old_corrected)} → ${row.status==='DUTY'||row.status==='RDG'?'Afventer dommertidsberegning':seconds(row.corrected)}</td></tr>`).join('')}</tbody></table></div>`;
  $('button.primary',form).disabled=false;
}
async function redressDialog(){
  await refreshTiming();
  const candidates=state.race.entries.filter(row=>row.status==='DUTY');
  openDialog('Dommertid efter sejladsen',`<form id="redress-form" class="stack"><p>Dommertiden beregnes ud fra de øvrige deltageres tider. Markér gerne dommertjans under måltagningen. Selve beregningen sker først efter endt sejlads, når de øvrige både har en måltid eller en afsluttende status, fx DNC eller DNF.</p><label class="field">Båd med dommertjans<select id="redress-boat" name="boat_id" required>${candidates.length?candidates.map(row=>`<option value="${row.boat_id}">${e(row.name)} · ${e(row.sail_number)}</option>`).join(''):'<option value="">Ingen både afventer dommertid</option>'}</select></label><p id="redress-readiness" role="status"></p><input type="hidden" name="request_id" value="${uid()}"><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Beregn dommertid</button></div></form>`);
  updateRedressReadiness();
}
function updateRedressReadiness(){
  const form=$('#redress-form');if(!form)return;
  const boatId=Number($('#redress-boat',form).value),target=state.race.entries.find(r=>r.boat_id===boatId),entries=state.race.entries.filter(r=>r.race_id===target?.race_id);
  const missing=entries.filter(row=>row.boat_id!==boatId&&row.sequence===null);
  const timed=entries.filter(row=>row.status==='FIN'&&row.corrected!==null);
  let reason='';
  if(!boatId)reason='Markér først båden med status Dommertjans under måltagningen.';
  else if(Object.keys(timingWork().drafts).length)reason='Bekræft eller annullér de aktive tider før beregning af dommertid.';
  else if(!state.race.race.completed)reason='Afslut først ræset for begge baner. Dommertiden beregnes derefter.';
  else if(missing.length)reason='Sejladsen er ikke færdigregistreret. Mangler måltid eller status for: '+missing.map(row=>row.name).join(', ')+'.';
  else if(state.race.captures.length)reason='Tildel eller kassér alle tidsstempler uden båd først.';
  else if(entries.some(row=>row.status==='FIN'&&row.corrected===null))reason='Alle målgangsbåde skal have tider for at beregne dommertid. Rækkefølgen alene er ikke nok.';
  else if(timed.length<2)reason='Beregningen kræver mindst to tidsmålte både og brugbar historik.';
  $('#redress-readiness',form).textContent=reason||'Alle øvrige deltagere er registreret. Dommertiden kan nu beregnes ud fra deres tider og bådens historik.';
  $('button.primary',form).disabled=!!reason;
}
function publishDialog(){const race=state.race.race;if(!race.published&&state.race.entries.some(row=>row.status==='DUTY')){toast('Afslut ræset først. Dommertid beregnes automatisk ved afslutningen.');return;}if(!race.published&&Object.keys(timingWork().drafts).length){toast('Bekræft eller annullér de aktive tider før offentliggørelse.');return;}const missing=state.race.entries.filter(r=>r.sequence===null);openDialog(race.published?'Åbn resultater for rettelser':'Offentliggør resultater',`<form id="publish-form" class="stack"><p>${e(race.label)} · ${date(race.start)} · Stor og lille bane</p><p class="subtle">${race.published?'Sejladsen bliver en kladde og vises igen offentligt, når resultaterne offentliggøres på ny.':'Resultaterne bliver synlige for alle, og indtastningen låses indtil sejladsen åbnes for rettelser.'}</p>${!race.published&&missing.length?`<div class="notice">${missing.length} både har ingen registrering: ${missing.map(b=>e(b.name)).join(', ')}.</div><label class="check"><input type="checkbox" name="confirm_dnc" required>Jeg bekræfter, at disse både skal have DNC</label>`:''}<div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">${race.published?'Åbn for rettelser':'Offentliggør'}</button></div></form>`);}
function editFinishDialog(boatId){activateTimingBoat(boatId,true);}
async function afterSaved(message,close=true){if(close&&dialog.open)dialog.close();await load();await render();toast(message);}
function normalizeNumberInput(input){
  const value=input.value.replace(/,/g,'.');
  if(input.value!==value){const cursor=input.selectionStart;input.value=value;try{input.setSelectionRange(cursor,cursor);}catch{}}
  input.setCustomValidity(value&&!/^\d+(?:\.\d+)?$/.test(value)?'Angiv ét tal. Komma og punktum kan begge bruges som decimaltegn.':'');
}
function enhancePage(){
  if(admin()&&['results','timing','starts','admin'].includes(state.page))$('.intro')?.insertAdjacentHTML('afterend','<div class="row between"><span></span><button class="small" data-action="day-settings">Indstil dagens distance og vind</button></div>');
}
function daySettingsDialog(){
  route=[];const dates=[...new Set(state.data.races.map(r=>r.start.slice(0,10)))].sort().reverse();
  openDialog('Dagens distance og vindkategori',`<form id="day-settings-form" class="stack"><label class="field">Sejladsdag<select name="date">${options(dates,dates[0])}</select></label><label class="field">Vindkategori for alle løb på begge baner<select name="wind_category"><option value="">Behold eksisterende vindkategori</option><option value="L">Let vind</option><option value="M">Mellem vind</option><option value="H">Hård vind</option></select></label><p class="subtle">Ændring af vindkategori bruger bådenes DH-tal for den valgte vind. Forhåndsvisningen kontrollerer, om alle har det nødvendige tal.</p><label class="field">Distance ensrettes på denne bane<select name="course">${options(['stor','lille'],state.course,['Stor bane','Lille bane'])}</select></label><h3>Vælg rundingsmærker for at sætte samme distance</h3><div class="row">${state.data.marks.filter(m=>!['J','X'].includes(m.name)).map(m=>`<button type="button" data-mark="${e(m.name)}">${e(m.name)}</button>`).join('')}<button type="button" data-action="route-undo">↶</button></div><div class="route-builder" id="route-preview">Ingen distanceændring, før der er valgt mærker.</div><div class="distance" id="route-distance">– sm</div><button type="button" data-action="day-preview">Forhåndsvis ændringer</button><div id="day-preview-result"></div><input id="day-token" name="token" type="hidden"><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button class="primary">Gennemfør for alle valgte løb</button></div></form>`);updateDayWind();
}
function updateDayWind(){const form=$('#day-settings-form');if(!form)return;const races=state.data.races.filter(r=>r.start.slice(0,10)===$('[name="date"]',form).value),field=$('[name="wind_category"]',form);field.disabled=!!races.length&&races.every(r=>r.rating_key==='GPH');field.options[0].textContent=field.disabled?'Ikke relevant ved GPH':'Behold eksisterende vindkategori';if(field.disabled)field.value='';field.title=field.disabled?'Vindkategori er ikke relevant ved GPH':'';}
function dayFormData(apply){const data=Object.fromEntries(new FormData($('#day-settings-form')));return {...data,series_id:state.seriesId,route,apply};}
document.addEventListener('input',event=>{
  if(event.target.closest('#active-measure-form')){
    if(event.target.matches('[data-number]'))normalizeNumberInput(event.target);
    if(event.target.id==='finish-time')event.target.value=event.target.value.replace(/[.,]/g,':');
    if(event.target.id==='finish-date')event.target.value=event.target.value.replace(/[./]/g,'-');
    rememberActiveTiming();
  }
},true);
document.addEventListener('submit',event=>{
  if(event.target.id!=='active-measure-form')return;
  event.preventDefault();event.stopImmediatePropagation();confirmActiveTiming();
},true);
document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button||button.disabled||state.page!=='timing')return;
  const action=button.dataset.timingAction;
  if(!action&&!button.dataset.pin&&!button.dataset.measure&&!button.dataset.capture&&!button.dataset.looseTime)return;
  event.preventDefault();event.stopImmediatePropagation();
  try{
    if(button.dataset.pin){
      const id=Number(button.dataset.pin),work=timingWork();
      work.pins=work.pins.includes(id)?work.pins.filter(value=>value!==id):[...work.pins,id];
      saveTimingWork();renderTimingLists();return;
    }
    if(button.dataset.measure){activateTimingBoat(Number(button.dataset.measure));return;}
    if(button.dataset.capture){activateTimingCapture(Number(button.dataset.capture));return;}
    if(button.dataset.looseTime){timingWork().activeKey=button.dataset.looseTime;saveTimingWork();renderActiveTiming();return;}
    if(action==='capture')await newLooseTiming(true);
    else if(action==='manual')await newLooseTiming(false);
    else if(action==='discard'){
      const capId=Number(button.dataset.captureId),work=timingWork(),activeKey=work.activeKey,source=state.race.captures.find(cap=>cap.id===capId);
      const keys=capId?Object.keys(work.drafts).filter(key=>work.drafts[key].capture_id===capId):[button.dataset.looseKey];
      if(capId)await api(`/api/races/${source.race_id}/discard-capture`,{id:capId});
      for(const key of keys)removeTimingDraft(key);
      await refreshTiming();if(keys.includes(activeKey))renderActiveTiming();renderTimingCaptures();
    }else if(action==='cancel'){
      const draft=activeTimingDraft();if(draft&&!draft.saving){removeTimingDraft(draft.key);renderActiveTiming();renderTimingCaptures();renderTimingLists();}
    }else if(action==='return')await returnActiveTiming();
    else if(action==='order')await confirmActiveTiming(true);else if(action==='duty'){$('#finish-status').value='DUTY';rememberActiveTiming();await confirmActiveTiming();}

  }catch(error){const draft=activeTimingDraft();if(draft){draft.error=error.message;saveTimingWork();renderActiveTiming();}else toast(error.message);}
},true);

document.addEventListener('input',ev=>{if(ev.target.matches('[data-number]'))normalizeNumberInput(ev.target);if(keys.includes(ev.target.name)||ev.target.name==='extra_handicap'){const selector=ev.target.closest('#boat-form')?$('#boat-set'):ev.target.closest('#calculation-form')?$('#historical-set'):null;if(selector)selector.value='';}if(ev.target.matches('[data-start-time]'))normalizeStartTime(ev.target);if(ev.target.id==='catalog-search'){const query=normalize(ev.target.value);for(const row of $$('.catalog-boat'))row.hidden=!row.dataset.search.includes(query);}if(ev.target.closest('#calculation-form')&&ev.target.id!=='calculation-token'){$('#calculation-token').value='';$('#calculation-form button.primary').disabled=true;}if(ev.target.closest('#day-settings-form')&&ev.target.id!=='day-token')$('#day-token').value='';if(ev.target.id==='boat-search'){state.query=ev.target.value;searchBoats();}if(ev.target.closest('#boat-form')&&['TACIL','TACIM','TACIH'].includes(ev.target.name))updateBoatGph();if(ev.target.closest('#boat-form')&&!ev.target.name.startsWith('recalculation_token')){const token=$('[name="recalculation_token"]',dialog);if(token)token.value='';}});
document.addEventListener('change',async ev=>{if(busy)return;try{const t=ev.target;if(t.id==='catalog-course'){$('#catalog-class').innerHTML=options(classOptions(t.value),'');return;}if(t.name==='date'&&t.closest('#day-settings-form')){updateDayWind();return;}if(t.dataset.roundSystem){const round=state.data.rounds.find(r=>r.id===Number(t.dataset.roundSystem));try{await api('/api/rounds/handicap-system',{round_id:round.id,handicap_system:t.value});await load();showSeries();}catch(error){t.value=round.handicap_system||'circle';throw error;}return;}if(t.id==='boat-set'||t.id==='historical-set'){const set=(t.id==='boat-set'?state.boatSets:state.historicalSets).find(set=>set.id===Number(t.value));if(set){const form=t.form;for(const key of keys)$(`[name="${key}"]`,form).value=set.ratings[key]??'';$('[name="extra_handicap"]',form).value=set.extra_handicap;if(t.id==='boat-set')updateBoatGph();else{$('#calculation-token').value='';$('#calculation-form button.primary').disabled=true;}}return;}if(t.id==='calculation-boat'){await updateHistoricalBoat();$('#calculation-token').value='';$('#calculation-form button.primary').disabled=true;return;}if(t.id==='boat-course'){$('#boat-class').innerHTML=options(classOptions(t.value),'');return;}if(t.id==='redress-boat'){updateRedressReadiness();return;}if(t.id==='series-select'){await selectSeries(t.value);}else if(t.id==='class-select'){const previousCourse=state.course;if(t.value.endsWith(':all')){state.course=t.value.split(':')[0];state.className='';}else{const course=t.selectedOptions[0]?.dataset.course;if(!course)return;state.course=course;state.className=t.value;}if(previousCourse!==state.course)state.raceId=null;await showResults('push');}else if(t.id==='result-view'){state.view=t.value;await showResults('push');}else if(t.id==='draft-toggle'){state.draft=t.checked;await showResults();}else if(t.id==='result-race'){state.raceId=Number(t.value);await showResults('push');}else if(t.id==='timing-race'){state.timingRoundId=Number(t.value);state.timingRaceId=null;state.selected=null;state.capture=null;state.query='';await showTiming();}else if(t.id==='start-race'){state.startRaceId=Number(t.value);await showStarts();}else if(['race-round','race-course','race-system','race-wind'].includes(t.id)){if(t.id==='race-round')$('#race-system').value=state.data.rounds.find(r=>r.id===Number(t.value)).handicap_system||'circle';updateRaceBoats();if(['race-round','race-course'].includes(t.id))fillPlannedStart();}else if(t.id==='recalculate-toggle')$('#historical-options').hidden=!t.checked;else if(t.name==='race_id'&&t.closest('#race-reference-form'))$('[name="reference"]',t.form).value=state.data.races.find(r=>r.id===Number(t.value)).reference;}catch(error){toast(error.message);}});
document.addEventListener('click',async ev=>{const target=ev.target.closest('button,[data-page]');if(!target||target.disabled||busy)return;try{if(target.dataset.roundStarts){await roundStartsDialog(Number(target.dataset.roundStarts));return;}if(target.dataset.selectSeries){await selectSeries(target.dataset.selectSeries);return;}if(target.dataset.heat){heatDialog(Number(target.dataset.heat));return;}if(target.dataset.configureRound){await raceDialog(Number(target.dataset.configureRound),target.dataset.configureCourse);return;}if(target.dataset.openTiming){state.timingRaceId=Number(target.dataset.openTiming);state.timingRoundId=roundForRace(state.timingRaceId)?.id;state.page='timing';await render();return;}if(target.dataset.page){state.page=target.dataset.page;state.selected=null;if(state.page==='timing'){await load();state.timingRoundId=chooseTimingRound()?.id;state.query='';}await render();return;}if(target.dataset.course){state.course=target.dataset.course;state.className=null;state.raceId=null;await render('push');return;}if(target.dataset.view){state.view=target.dataset.view;await showResults();return;}if(target.dataset.boat){state.selected=Number(target.dataset.boat);await showTiming();$('#finish-time')?.focus();return;}if(target.dataset.capture){state.capture=Number(target.dataset.capture);await showTiming();if(!state.selected)$('#boat-search')?.focus();return;}if(target.dataset.mark){route.push(target.dataset.mark);await updateRoute();return;}if(target.dataset.removeMark){route.splice(Number(target.dataset.removeMark),1);await updateRoute();return;}if(target.dataset.editBoat){await boatDialog(Number(target.dataset.editBoat));return;}if(target.dataset.editFinish){editFinishDialog(Number(target.dataset.editFinish));return;}const action=target.dataset.action;if(action==='copy-link'){await copyResultLink();return;}if(action==='login'){showLogin();return;}if(action==='close-dialog'){dialog.close();return;}if(action==='new-race'){await raceDialog(state.page==='timing'?state.timingRoundId:null);return;}if(action==='catalog'){await catalogDialog();return;}if(action==='catalog-all'){for(const row of $$('.catalog-boat'))if(!row.hidden&&!$('input',row).disabled)$('input',row).checked=true;return;}if(action==='new-boat'){await boatDialog();return;}if(action==='new-series'){seriesDialog();return;}if(action==='edit-series'){seriesSettingsDialog();return;}if(action==='new-rounds'){roundsDialog();return;}if(action==='reference'){referenceDialog();return;}if(action==='day-settings'){daySettingsDialog();return;}if(action==='route-undo'){route.pop();await updateRoute();return;}if(action==='print'){window.print();return;}if(action==='pdf'){window.location.href=`/api/pdf?series_id=${state.seriesId}&course=${state.course}&class_name=${encodeURIComponent(state.className)}${state.view==='daily'&&state.raceId?'&race_id='+state.raceId:''}`;return;}if(action==='historical-calculation'){await historicalCalculationDialog();return;}if(action==='historical-preview'){await previewHistorical();return;}if(action==='redress-dialog'){await redressDialog();return;}if(action==='complete-round'){await completionDialog();return;}if(action==='reopen-round'){await completionDialog(true);return;}if(action==='publish'){publishDialog();return;}if(busy)return;busy=true;target.disabled=true;
if(action==='logout'){await api('/api/logout',{});state.page='results';state.draft=false;await afterSaved('Du er logget ud.');}
else if(target.dataset.undo){await api(`/api/races/${state.timingRaceId}/undo`,{boat_id:Number(target.dataset.undo),revision:state.race.race.revision});await afterSaved('Registreringen er fortrudt. Den kan findes i ændringshistorikken.');}
else if(target.dataset.discard){await api(`/api/races/${source.race_id}/discard-capture`,{id:Number(target.dataset.discard)});if(state.capture===Number(target.dataset.discard))state.capture=null;await afterSaved('Tidsstemplet er kasseret.');}
else if(target.dataset.move){const selectedCourse=timingCourseForBoat(Number(target.dataset.move));const order=state.race.entries.filter(r=>r.race_id===selectedCourse.id&&r.status==='FIN'&&r.sequence!==null).sort((a,b)=>a.sequence-b.sequence).map(r=>r.boat_id);const i=order.indexOf(Number(target.dataset.move)),j=i+Number(target.dataset.direction);if(j>=0&&j<order.length){[order[i],order[j]]=[order[j],order[i]];await api(`/api/races/${selectedCourse.id}/reorder`,{boat_ids:order,revision:selectedCourse.revision});await afterSaved('Målgangsrækkefølgen er rettet.');}}
else if(target.dataset.user){await api('/api/users',{id:Number(target.dataset.user),role:$(`#user-role-${target.dataset.user}`).value,approved:target.dataset.approved==='true'});await afterSaved('Brugerens adgang er opdateret.');}
else if(action==='day-preview'){const result=await api('/api/day-settings',dayFormData(false));$('#day-token').value=result.token;$('#day-preview-result').innerHTML=`<p class="notice">${e(result.notice)}</p>${result.changes.map(r=>`<p class="subtle">${e(r.label)} · ${r.course==='stor'?'Stor':'Lille'} bane · ${r.updates.length} både · ${e(r.rating_key)} · ${[...new Set(r.updates.map(u=>number(u.distance,3)))].join(', ')} sm</p>`).join('')}`;}else if(action==='recalculation-preview'){const result=await api('/api/boats/recalculation-preview',boatFormData());$('[name="recalculation_token"]',dialog).value=result.token;$('#recalculation-preview').innerHTML=`<p class="notice">${e(result.notice)}</p><div class="table-scroll"><table><thead><tr><th>Sejlads</th><th>DH før → efter</th><th>Ekstra handicap</th><th>Beregnet før → efter</th></tr></thead><tbody>${result.changes.map(r=>`<tr><td>${e(r.label)}</td><td>${number(r.old_rating,1)} → ${number(r.rating,1)}</td><td>+${r.old_extra_handicap} → +${r.extra_handicap} s</td><td>${seconds(r.old_corrected)} → ${seconds(r.corrected)}</td></tr>`).join('')}</tbody></table></div><p class="subtle">Tryk Gem båd for udtrykkeligt at gennemføre disse ændringer.</p>`;}
}catch(error){if(dialog.open)dialogError(error);else toast(error.message);if(state.page==='timing'){try{const selection=state.selected;await showTiming();state.selected=selection;}catch{}}}finally{busy=false;if(target.isConnected)target.disabled=false;}});
document.addEventListener('submit',async ev=>{ev.preventDefault();if(busy)return;busy=true;const form=ev.target,submit=$('button.primary',form);if(submit)submit.disabled=true;try{const data=Object.fromEntries(new FormData(form));if(form.id==='catalog-form'){await api('/api/boats/enroll',{...data,series_id:state.seriesId,profile_ids:new FormData(form).getAll('profile_ids').map(Number)});await afterSaved('Bådene er tilmeldt serien fra den fælles bådliste.');}else if(form.id==='round-starts-form'){await api('/api/rounds/starts',{round_id:Number(data.round_id),race_date:isoDay(data.race_date),start_times:{stor:data.start_stor,lille:data.start_lille}});await afterSaved('Ræsets dato og starttider er gemt.');}else if(form.id==='course-action-form'){await historicalCalculationDialog(Number(data.race_id));}else if(form.id==='round-completion-form'){const finished=await api(`/api/rounds/${state.timingRoundId}/${form.dataset.reopen==='true'?'reopen':'complete'}`,{revisions:timingRevisions()});await afterSaved(form.dataset.reopen==='true'?'Ræset er genåbnet for begge baner.':`Ræset er afsluttet for begge baner. ${finished.redress?.length||0} dommertider er beregnet. Resterende både har fået DNC.`);}else if(form.id==='series-settings-form'){await api('/api/series/settings',{...data,series_id:state.seriesId,...(data.year?{year:Number(data.year)}:{}),discards:discardsInput(data.discards)});await afterSaved('Indstillingerne for den valgte serie er gemt.');}else if(form.id==='calculation-form'){if(Object.keys(timingWork().drafts).length)throw new Error('Bekræft eller annullér aktive måltider før historisk genberegning.');await api(`/api/races/${state.historicalRace.race.id}/recalculate`,historicalData());await afterSaved('Kun den valgte sejlads er genberegnet ud fra det historiske grundlag. Gennemgå og offentliggør igen.');}else if(form.id==='rounds-form'){await api('/api/rounds',{...data,series_id:state.seriesId,start_times:{stor:data.start_stor,lille:data.start_lille}});state.page='series';await afterSaved('Ræs med begge baner og løb er tilføjet.');}else if(form.id==='heat-members-form'){await api('/api/heats/members',{heat_id:Number(data.heat_id),boat_ids:new FormData(form).getAll('boat_ids').map(Number),scope:data.whole_series?'series':'round'});await afterSaved('Deltagerne i løbet er gemt.');}else if(form.id==='redress-form'){if(Object.keys(timingWork().drafts).length)throw new Error('Bekræft eller annullér de aktive tider før beregning af dommertid.');await api(`/api/races/${timingCourseForBoat(Number(data.boat_id)).id}/redress`,{boat_id:Number(data.boat_id),request_id:data.request_id});await afterSaved('Dommertiden er beregnet ud fra de registrerede tider.');}else if(form.id==='day-settings-form'){const result=await api('/api/day-settings',dayFormData(true));await afterSaved('Dagens opsætning er ensrettet. Resultaterne skal gennemgås og offentliggøres igen.');}else if(form.id==='race-form'){const result=await api('/api/races',{...data,start:isoDay(data.race_date)+'T'+data.start_time,series_id:state.seriesId,route});state.timingRaceId=result.id;state.timingRoundId=null;state.page='timing';await afterSaved('Sejladsen er oprettet. Startlisten er klar.');}else if(form.id==='boat-form'){await api('/api/boats',boatFormData());await afterSaved(data.recalculate?'Båden og de valgte historiske resultater er opdateret.':'Båden er gemt. Historiske resultater er bevaret.');}else if(form.id==='reference-form'){await api('/api/series/reference',{series_id:state.seriesId,reference:data.reference,apply_all:!!data.apply_all,revisions:Object.fromEntries(state.data.races.map(r=>[String(r.id),r.revision]))});await afterSaved('Referencehandicappet er opdateret.');}else if(form.id==='race-reference-form'){const race=state.data.races.find(r=>r.id===Number(data.race_id));await api('/api/race-reference',{...data,race_id:race.id,revision:race.revision});await afterSaved('Sejladsens reference er opdateret.');}else if(form.id==='series-form'){const created=await api('/api/series',{...data,year:Number(data.year),fleets:{stor:data.stor_fleets,lille:data.lille_fleets},discards:data.discards.trim()?data.discards.split(',').map(s=>Number(s.trim())):[]});state.seriesId=created.id;state.page='series';state.timingRaceId=null;state.timingRoundId=null;state.startRaceId=null;await afterSaved('Serien med ræs, baner og løb er oprettet. Tilføj både til løbene.');}else if(form.id==='publish-form'){await api(`/api/rounds/${state.timingRoundId}/${state.race.race.published?'reopen':'publish'}`,{revisions:timingRevisions(),confirm_dnc:!!data.confirm_dnc});await afterSaved('Sejladsens offentliggørelse er opdateret.');}else if(form.id==='edit-finish-form'){await api(`/api/races/${state.timingRaceId}/edit`,{...data,boat_id:Number(form.dataset.boatId),revision:state.race.race.revision,request_id:uid()});await afterSaved('Rettelsen er gemt i historikken.');}}catch(error){dialogError(error);}finally{busy=false;if(submit?.isConnected)submit.disabled=false;}});
setInterval(()=>{const live=$('#live-clock');if(live)live.textContent=clock(now());},250);
setInterval(async()=>{if(state.page!=='timing'||!state.timingRoundId||busy||dialog.open)return;try{const before=Date.now(),fresh=await api(`/api/state?series_id=${state.seriesId}`);state.offset=new Date(fresh.server_time).getTime()-(before+Date.now())/2;state.connected=true;const badge=$('#connection');if(badge){badge.textContent='Forbundet';badge.className='pill dot';}const latest=fresh.races.find(r=>r.id===state.timingRaceId);state.data=fresh;await refreshTiming();}catch{state.connected=false;const badge=$('#connection');if(badge){badge.textContent='Ingen forbindelse';badge.className='pill amber';}}},10000);
window.addEventListener('popstate',async()=>{restoreResultPreferences();state.page='results';if(dialog.open)dialog.close();try{await load();await render();}catch(error){toast(error.message);}});
(async()=>{try{restoreResultPreferences();await load();await render();}catch(error){$('#app').innerHTML=`<div class="screen-error"><h1>Forbindelsen mangler</h1><p>${e(error.message)}</p><p>Genindlæs siden for at prøve igen.</p></div>`;}})();

document.addEventListener('blur',event=>{if(event.target.matches('[data-start-time]'))normalizeStartTime(event.target,true);},true);
