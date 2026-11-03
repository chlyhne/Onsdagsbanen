/* Uses an isolated --demo server. Never run mutation tests against production. */
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/home/casper/Documents/sejlrelateret/racetimer/node_modules/playwright');
const base=process.env.ONSDAGSBANE_BASE_URL||'http://127.0.0.1:8096';
const localTime=date=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Copenhagen',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(date).replace(' ','T');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:1000},timezoneId:'Europe/Copenhagen'});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const timingValue=async()=>`${(await page.locator('#finish-date').inputValue()).split('-').reverse().join('-')}T${await page.locator('#finish-time').inputValue()}`;
 const setTime=async value=>page.locator('#finish-time').fill(value.split('T')[1]);
 const detail=async id=>(await page.request.get(base+`/api/races/${id}`)).json();
 const left=id=>page.locator(`[data-unmeasured="${id}"]`),right=id=>page.locator(`[data-measured="${id}"]`);
 async function search(text){await page.locator('#boat-search').fill(text);}
 async function confirm(id){const saved=page.waitForResponse(response=>response.request().method()==='POST'&&/\/(finish|edit)$/.test(new URL(response.url()).pathname));await page.getByRole('button',{name:'Bekræft',exact:true}).click();const response=await saved;assert.equal(response.status(),200,await response.text());await page.waitForFunction(()=>!document.querySelector('#active-measure-form'));}
 async function createRace(mode){
  const label=`Test ${mode} ${Date.now()}`;
  await page.evaluate(async label=>{await api('/api/rounds',{series_id:state.seriesId,round_labels:[label]});await load();const planned=state.data.rounds.find(r=>r.label===label);for(const heat of planned.courses.find(c=>c.course==='lille').heats)await api('/api/heats/members',{heat_id:heat.id,boat_ids:[]});await load();},label);
  await page.getByRole('button',{name:'Indstil bane',exact:true}).click();
  await page.locator('#race-round').selectOption({label});await page.locator('#race-course').selectOption('stor');
  await page.locator('dialog [name="mode"]').selectOption(mode);
  const start=localTime(new Date(Date.now()-3600000));await page.locator('dialog [name="race_date"]').fill(start.slice(0,10).split('-').reverse().join('-'));await page.locator('dialog [name="start_time"]').fill(start.slice(11));
  for(const name of ['A','B','C','D'])await page.getByRole('button',{name,exact:true}).click();
  await page.getByRole('button',{name:'Gem bane og gå til måltagning',exact:true}).click();
  await page.locator('dialog').waitFor({state:'hidden'});
  await page.waitForFunction(label=>document.querySelector('#timing-race')?.selectedOptions[0].textContent.startsWith(label),label);
  return page.evaluate(()=>state.race.courses.find(r=>r.course==='stor').id);
 }
 try{
  await page.goto(base+'/?series=1');await page.getByRole('heading',{name:'Resultater',exact:true}).waitFor();
  const initial=await(await page.request.get(base+'/api/state')).json();assert.equal(initial.demo,true);
  await page.getByRole('button',{name:'Måltagning',exact:true}).click();
  const race=await createRace('normal');
  const entries=(await detail(race)).entries;
  const nord=entries.find(b=>b.name==='Nordlys'),frema=entries.find(b=>b.name==='FremA');
  const others=entries.filter(b=>![nord.boat_id,frema.boat_id].includes(b.boat_id));
  assert.equal(await page.locator('[data-unmeasured]').count(),entries.length);
  // Local filtering and pins: no network connection is needed to search.
  await page.locator(`[data-pin="${frema.boat_id}"]`).click();
  await page.route('**/api/**',route=>route.abort('failed'));
  await search('nor');await left(nord.boat_id).waitFor();await left(frema.boat_id).waitFor();
  assert.equal(await page.locator('[data-unmeasured]').count(),2);
  await search('zz-no-match');assert.equal(await page.locator('[data-unmeasured]').count(),1);
  assert.equal(await page.locator('[data-unmeasured]').first().getAttribute('data-unmeasured'),String(frema.boat_id));
  await page.unroute('**/api/**');await search('nor');
  // Clicking freezes a draft; it must not submit or keep advancing with the clock.
  await page.locator(`[data-measure="${nord.boat_id}"]`).click();
  const frozen=await timingValue();
  const danishDate=await page.locator('#finish-date').inputValue();
  assert.match(danishDate,/^\d{2}-\d{2}-\d{4}$/);
  assert.match(await page.locator('#finish-time').inputValue(),/^\d{2}:\d{2}:\d{2}$/);
  assert.equal(await page.locator('#finish-time').getAttribute('type'),'text');
  // Impossible dates and times cannot be confirmed; punctuation is normalized locally.
  await page.locator('#finish-date').fill('31-02-2026');
  assert.equal(await page.locator('#finish-date').evaluate(input=>input.checkValidity()),false);
  await page.getByRole('button',{name:'Bekræft',exact:true}).click();
  assert.equal((await detail(race)).entries.find(b=>b.boat_id===nord.boat_id).sequence,null);
  await page.locator('#finish-date').fill(danishDate.replaceAll('-','.'));
  assert.equal(await page.locator('#finish-date').inputValue(),danishDate);
  await page.locator('#finish-time').fill('25:00:00');
  assert.equal(await page.locator('#finish-time').evaluate(input=>input.checkValidity()),false);
  await page.locator('#finish-time').fill(frozen.split('T')[1].replaceAll(':','.'));
  assert.equal(await timingValue(),frozen);
  await page.waitForTimeout(1100);assert.equal(await timingValue(),frozen);
  assert.equal((await detail(race)).entries.find(b=>b.boat_id===nord.boat_id).sequence,null);
  const entered=localTime(new Date(Date.now()-5000));
  await setTime(entered);assert.equal(await page.locator('#finish-date').inputValue(),danishDate);
  await page.locator('.timing-extra summary').click();
  await page.locator('#finish-penalty').fill('30,0');assert.equal(await page.locator('#finish-penalty').inputValue(),'30.0');
  await confirm(nord.boat_id);
  let row=(await detail(race)).entries.find(b=>b.boat_id===nord.boat_id);
  assert.equal(localTime(new Date(row.instant)),entered);assert.equal(row.penalty,30);
  // One query filters both sides, while the unrelated pinned boat stays visible.
  await search('nor');assert.equal(await page.locator('[data-unmeasured]').count(),1);assert.equal(await page.locator('[data-measured]').count(),1);
  await right(nord.boat_id).getByRole('button',{name:'Ret',exact:true}).click();
  const corrected=localTime(new Date(Date.now()-3000));await setTime(corrected);await confirm(nord.boat_id);
  assert.equal(localTime(new Date((await detail(race)).entries.find(b=>b.boat_id===nord.boat_id).instant)),corrected);
  await right(nord.boat_id).getByRole('button',{name:'Ret',exact:true}).click();
  await page.getByRole('button',{name:'Flyt tilbage til ikke måltaget',exact:true}).click();
  await left(nord.boat_id).waitFor();assert.equal(await page.locator('[data-measured]').count(),0);
  // Two near-simultaneous arrivals can both remain unconfirmed without lost times.
  await page.locator(`[data-measure="${nord.boat_id}"]`).click();const firstDraft=await timingValue();
  await page.locator(`[data-measure="${frema.boat_id}"]`).click();const secondDraft=await timingValue();
  await page.locator(`[data-measure="${nord.boat_id}"]`).click();assert.equal(await timingValue(),firstDraft);
  const precise=await page.evaluate(()=>{const work=JSON.parse(localStorage.getItem(`ob_timing_round:${state.seriesId}:${state.timingRoundId}`));return work.drafts[work.activeKey].instant;});
  await confirm(nord.boat_id);assert.equal(Date.parse((await detail(race)).entries.find(b=>b.boat_id===nord.boat_id).instant),Date.parse(precise));await page.locator(`[data-measure="${frema.boat_id}"]`).click();assert.equal(await timingValue(),secondDraft);await confirm(frema.boat_id);
  // Time first: capture, find boat, and correct the captured time before confirmation.
  await page.getByRole('button',{name:'Tag tid nu',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-capture]'));
  const captureId=Number(await page.locator('[data-capture]').first().getAttribute('data-capture'));
  const originalCap=(await detail(race)).captures.find(c=>c.id===captureId);
  await search(others[0].name);await page.locator(`[data-measure="${others[0].boat_id}"]`).click();
  const manualCapture=localTime(new Date(Date.parse(originalCap.instant)-2000));await setTime(manualCapture);await confirm(others[0].boat_id);
  row=(await detail(race)).entries.find(b=>b.boat_id===others[0].boat_id);assert.equal(localTime(new Date(row.instant)),manualCapture);assert.equal((await detail(race)).captures.length,0);
  // A known time can be typed before its boat is selected.
  await page.getByRole('button',{name:'Indtast tid uden båd',exact:true}).click();
  const known=localTime(new Date(Date.now()-20000));await setTime(known);
  await search(others[1].name);await page.locator(`[data-measure="${others[1].boat_id}"]`).click();assert.equal(await timingValue(),known);await confirm(others[1].boat_id);
  // Pins and frozen unconfirmed drafts survive a reload.
  await search(others[2].name);await page.locator(`[data-pin="${others[2].boat_id}"]`).click();await page.locator(`[data-measure="${others[2].boat_id}"]`).click();
  const pending=await timingValue();
  await page.reload();await page.getByRole('button',{name:'Måltagning',exact:true}).click();
  await page.waitForSelector('#finish-time');assert.equal(await timingValue(),pending);
  await search('zz-no-match');await left(others[2].boat_id).waitFor();
  // A failed save keeps its original time and can be retried without duplication.
  let fail=true;await page.route('**/finish',route=>fail?(fail=false,route.abort('failed')):route.continue());
  await page.getByRole('button',{name:'Bekræft',exact:true}).click();await page.locator('#active-measure-form .error').waitFor();
  assert.equal(await timingValue(),pending);await page.unroute('**/finish');
  await search('');await confirm(others[2].boat_id);
  const finishers=(await detail(race)).entries.filter(b=>b.status==='FIN');assert.equal(finishers.length,5);
  await page.screenshot({path:'build/onsdagsbane-timing-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'build/onsdagsbane-timing-mobile.png',fullPage:true});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.setViewportSize({width:1440,height:1000});
  // Duty may be designated early without running the model or inventing a time.
  const judge=others[3];await search(judge.name);await page.locator(`[data-measure="${judge.boat_id}"]`).click();
  await page.locator('.timing-extra summary').click();await page.locator('#finish-status').selectOption('DUTY');
  assert.equal(await page.locator('#duty-notice').isVisible(),true);
  assert.match(await page.locator('#duty-notice').innerText(),/først efter endt sejlads/);
  await confirm(judge.boat_id);await right(judge.boat_id).waitFor();
  assert.match(await right(judge.boat_id).innerText(),/Dommertjans.*beregnes ved afslutning/);
  let duty=(await detail(race)).entries.find(row=>row.boat_id===judge.boat_id);
  assert.equal(duty.status,'DUTY');assert.equal(duty.corrected,null);assert.equal(duty.instant,null);
  assert.equal(await page.getByRole('button',{name:'Dommertid',exact:true}).count(),0);
  const premature=await page.evaluate(async({race,boat})=>{try{await api(`/api/races/${race}/redress`,{boat_id:boat,request_id:uid()});return '';}catch(error){return error.message;}},{race,boat:judge.boat_id});
  assert.match(premature,/alle øvrige deltagere/);
  // Explicitly close the other nonparticipants before the end-of-race calculation.
  const unregistered=(await detail(race)).entries.filter(row=>row.sequence===null);
  for(const row of unregistered)await page.evaluate(async({race,boat})=>api(`/api/races/${race}/finish`,{boat_id:boat,status:'DNC',request_id:uid()}),{race,boat:row.boat_id});
  await search('');await page.getByRole('button',{name:'Afslut ræs',exact:true}).click();await page.locator('#round-completion-form').getByRole('button',{name:'Afslut ræs',exact:true}).click();await page.locator('dialog').waitFor({state:'hidden'});
  duty=(await detail(race)).entries.find(row=>row.boat_id===judge.boat_id);
  assert.equal(duty.status,'RDG');assert.ok(duty.corrected>0);
  await page.getByRole('button',{name:'Offentliggør resultater →',exact:true}).click();if(await page.locator('dialog [name="confirm_dnc"]').count())await page.locator('dialog [name="confirm_dnc"]').check();await page.getByRole('button',{name:'Offentliggør',exact:true}).click();await page.locator('dialog').waitFor({state:'hidden'});
  assert.equal((await detail(race)).race.published,1);
  await page.getByRole('button',{name:'Resultater',exact:true}).click();await page.locator('#result-view').selectOption('daily');await page.locator('#result-race').selectOption(String(race));
  await page.waitForFunction(()=>[...document.querySelectorAll('.course-mark')].map(m=>m.firstChild.textContent).join('')==='ABCD');
  assert.equal(await page.locator('.course-starboard').count(),1);assert.equal(await page.locator('.course-port').count(),3);
  await page.waitForFunction(()=>[...document.querySelectorAll('.results-table th,.results-table td')].every(cell=>{const r=cell.getBoundingClientRect();return r.width+.5>=r.height;}));
  await page.getByRole('button',{name:'Måltagning',exact:true}).click();const pursuit=await createRace('pursuit');
  const pursuitBoat=(await detail(pursuit)).entries[0];await page.locator(`[data-measure="${pursuitBoat.boat_id}"]`).click();await page.getByRole('button',{name:'Kun rækkefølge',exact:true}).click();await right(pursuitBoat.boat_id).waitFor();
  assert.equal((await detail(pursuit)).entries.find(b=>b.boat_id===pursuitBoat.boat_id).instant,null);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,normalRace:race,pursuitRace:pursuit,localSearch:true,pinsAlwaysVisible:true,separateDateTime:true,danishDate:true,wholeSeconds:true,dutyPending:true,redressAfterRace:true,frozenDrafts:true,timeFirst:true,boatFirst:true,editAndReturn:true,reload:true,retry:true,squareCells:true,javascriptErrors:errors.length}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
