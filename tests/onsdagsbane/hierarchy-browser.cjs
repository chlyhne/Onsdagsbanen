/* Isolated --demo only: calendar setup, per-round fleets and historical correction. */
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/home/casper/Documents/sejlrelateret/racetimer/node_modules/playwright');
const base=process.env.ONSDAGSBANE_BASE_URL||'http://127.0.0.1:8096';
const local=date=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Copenhagen',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(date).replace(' ','T');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:1000},timezoneId:'Europe/Copenhagen'});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const getState=async()=>(await page.request.get(base+`/api/state?series_id=${await page.evaluate(()=>state.seriesId)}`)).json();
 const getRace=async id=>(await page.request.get(base+`/api/races/${id}`)).json();
 async function closeSaved(){await page.locator('dialog').waitFor({state:'hidden'});}
 async function series(){await page.getByRole('button',{name:'Serie',exact:true}).click();await page.locator('.series-table').waitFor();}
 async function heat(roundLabel,course,name){const state=await getState();return state.rounds.find(r=>r.label===roundLabel).courses.find(c=>c.course===course).heats.find(h=>h.name===name);}
 async function changeMembership(roundLabel,course,name,boatId,checked){
  const item=await heat(roundLabel,course,name);await page.locator(`[data-heat="${item.id}"]`).click();
  assert.equal(await page.locator('[name="whole_series"]').isChecked(),false);
  await page.locator(`dialog [name="boat_ids"][value="${boatId}"]`).setChecked(checked);
  await page.getByRole('button',{name:'Gem deltagere',exact:true}).click();await closeSaved();
 }
 async function configure(roundLabel,course,start,system='circle'){
  const s=await getState(),round=s.rounds.find(r=>r.label===roundLabel);
  await page.locator(`[data-configure-round="${round.id}"][data-configure-course="${course}"]`).click();
  assert.equal(await page.locator('#race-round').inputValue(),String(round.id));
  assert.equal(await page.locator('#race-course').inputValue(),course);
  assert.equal(await page.locator('#race-system').inputValue(),system);
  assert.equal(await page.locator('#race-wind').isDisabled(),system==='gph');
  if(system==='gph'){assert.equal(await page.locator('#race-wind').inputValue(),'');assert.equal(await page.locator('#race-form').evaluate(form=>new FormData(form).has('wind_category')),false);}
  await page.locator('dialog [name="race_date"]').fill(start.slice(0,10).split('-').reverse().join('-'));await page.locator('dialog [name="start_time"]').fill(start.slice(11));
  for(const mark of ['A','B','C','D'])await page.getByRole('button',{name:mark,exact:true}).click();
  await page.getByRole('button',{name:'Gem bane og gå til måltagning',exact:true}).click();await closeSaved();
  return page.evaluate(course=>state.race.courses.find(r=>r.course===course).id,course);
 }
 async function finish(boatId,stamp){
  await page.locator(`[data-measure="${boatId}"]`).click();
  const [day,time]=stamp.split('T');await page.locator('#finish-date').fill(day.split('-').reverse().join('-'));await page.locator('#finish-time').fill(time);
  await page.getByRole('button',{name:'Bekræft',exact:true}).click();await page.locator(`[data-measured="${boatId}"]`).waitFor();
 }
 async function publish(){await page.getByRole('button',{name:'Offentliggør resultater →',exact:true}).click();if(await page.locator('[name="confirm_dnc"]').count())await page.locator('[name="confirm_dnc"]').check();await page.getByRole('button',{name:'Offentliggør',exact:true}).click();await closeSaved();}
 try{
  await page.goto(base);await page.getByRole('heading',{name:'Resultater',exact:true}).waitFor();assert.equal((await getState()).demo,true);
  await series();await page.getByRole('button',{name:'Ny serie',exact:true}).click();
  await page.locator('[name="name"]').fill('Test sæsonplan '+Date.now());await page.locator('[name="year"]').fill('2026');
  await page.locator('[name="round_labels"]').fill('R1–R3');
  await page.locator('[name="stor_fleets"]').fill('Stor A\nStor B');await page.locator('[name="lille_fleets"]').fill('Lille A');
  await page.getByRole('button',{name:'Opret serie med ræs og løb',exact:true}).click();await closeSaved();
  assert.equal(await page.locator('.series-table tbody tr').count(),3);
  let s=await getState();assert.equal(s.races.length,0);assert.equal(s.rounds.length,3);assert.equal(await page.getByRole('button',{name:'Startlister',exact:true}).count(),0);
  const createdId=s.series_id,seriesCount=s.series.length;
  await page.getByRole('button',{name:'Administration',exact:true}).click();
  const settings=page.locator('section').filter({has:page.getByRole('heading',{name:'Seriens indstillinger',exact:true})});
  await settings.getByRole('button',{name:'Tilføj ræs',exact:true}).waitFor();
  assert.equal(await settings.getByRole('button',{name:'Tilføj ræs',exact:true}).isVisible(),true);
  assert.equal(await settings.getByRole('button',{name:'Ny serie',exact:true}).count(),0);
  await settings.getByRole('button',{name:'Rediger serie',exact:true}).click();
  await page.locator('dialog [name="name"]').fill('Test redigeret sæsonplan '+Date.now());
  await page.locator('dialog [name="reference"]').fill('1010,0');
  await page.locator('dialog [name="discards"]').fill('3, 5');
  await page.getByRole('button',{name:'Gem seriens indstillinger',exact:true}).click();await closeSaved();
  s=await getState();assert.equal(s.series_id,createdId);assert.equal(s.series.length,seriesCount);assert.equal(s.rounds.length,3);assert.equal(s.series.find(item=>item.id===createdId).reference,1010);
  await settings.getByRole('button',{name:'Tilføj ræs',exact:true}).click();
  assert.equal(await page.locator('#series-form').count(),0);
  await page.locator('#rounds-form [name="round_labels"]').fill('R4');
  await page.locator('#rounds-form').getByRole('button',{name:'Tilføj ræs',exact:true}).click();await closeSaved();
  s=await getState();assert.equal(s.series_id,createdId);assert.equal(s.series.length,seriesCount);assert.deepEqual(s.rounds.map(round=>round.label),['R1','R2','R3','R4']);
  await page.getByRole('button',{name:'Både & DH',exact:true}).click();await page.getByRole('button',{name:'+ Opret båd',exact:true}).click();
  for(const [name,value] of Object.entries({name:'Vandrefuglen',skipper:'Test skipper',sail_number:'DEN 4242',boat_type:'X-332',TACIL:'711,6',TACIM:'570,2',TACIH:'507,6',TAUDM:'750'}))await page.locator(`dialog [name="${name}"]`).fill(value);
  await page.locator('#boat-class').selectOption('Stor A');assert.equal(await page.locator('#boat-gph').inputValue(),'575,8');
  await page.getByRole('button',{name:'Gem båd',exact:true}).click();await closeSaved();
  s=await getState();const boat=s.boats[0];assert.equal(boat.gph,575.8);
  const days=[21,14,7].map(offset=>{const date=new Date(Date.now()-offset*86400000);return local(date).slice(0,10);});
  await series();const first=await configure('R1','stor',days[0]+'T18:30:00');await finish(boat.id,days[0]+'T19:30:00');await publish();
  await series();await changeMembership('R2','stor','Stor A',boat.id,false);await changeMembership('R2','stor','Stor B',boat.id,true);
  assert.deepEqual((await heat('R1','stor','Stor A')).boat_ids,[boat.id]);assert.deepEqual((await heat('R3','stor','Stor A')).boat_ids,[boat.id]);
  let systemSaved=page.waitForResponse(response=>response.url().endsWith('/rounds/handicap-system'));await page.locator(`[data-round-system="${(await getState()).rounds.find(r=>r.label==='R2').id}"]`).selectOption('gph');assert.equal((await systemSaved).status(),200);
  const second=await configure('R2','stor',days[1]+'T18:30:00','gph');await finish(boat.id,days[1]+'T19:30:00');await publish();
  await series();const little=await heat('R3','lille','Lille A');await page.locator(`[data-heat="${little.id}"]`).click();
  assert.equal(await page.locator(`dialog [name="boat_ids"][value="${boat.id}"]`).isDisabled(),true);await page.getByRole('button',{name:'Annuller',exact:true}).click();
  await changeMembership('R3','stor','Stor A',boat.id,false);await changeMembership('R3','lille','Lille A',boat.id,true);
  await page.screenshot({path:'build/onsdagsbane-series-desktop.png',fullPage:true});await page.setViewportSize({width:390,height:844});await page.screenshot({path:'build/onsdagsbane-series-mobile.png',fullPage:true});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.setViewportSize({width:1440,height:1000});
  systemSaved=page.waitForResponse(response=>response.url().endsWith('/rounds/handicap-system'));await page.locator(`[data-round-system="${(await getState()).rounds.find(r=>r.label==='R3').id}"]`).selectOption('updown');assert.equal((await systemSaved).status(),200);
  const third=await configure('R3','lille',days[2]+'T18:30:00','updown');await finish(boat.id,days[2]+'T19:30:00');await publish();
  const before=[await getRace(first),await getRace(second),await getRace(third)];
  assert.equal(before[1].race.rating_key,'GPH');assert.equal(before[1].entries[0].rating,575.8);assert.equal(before[1].entries[0].wind_category,'');assert.equal(before[2].race.rating_key,'TAUDM');
  await page.getByRole('button',{name:'Administration',exact:true}).click();await page.getByRole('button',{name:'Indstil dagens distance og vind',exact:true}).click();await page.locator('dialog [name="date"]').selectOption(days[1]);assert.equal(await page.locator('dialog [name="wind_category"]').isDisabled(),true);await page.getByRole('button',{name:'Annuller',exact:true}).click();
  await page.getByRole('button',{name:'Både & DH',exact:true}).click();await page.locator(`[data-edit-boat="${boat.id}"]`).click();
  await page.locator('#boat-course').selectOption('lille');await page.locator('#boat-class').selectOption('Lille A');await page.locator('dialog [name="TACIM"]').fill('900');await page.locator('dialog [name="extra_handicap"]').fill('60');
  await page.getByRole('button',{name:'Gem båd',exact:true}).click();await closeSaved();
  await series();await page.locator(`[data-open-timing="${first}"]`).click();await page.getByRole('button',{name:'Beregningsgrundlag',exact:true}).click();
  await page.locator('#calculation-boat').selectOption(String(boat.id));assert.equal(await page.locator('dialog [name="TACIM"]').inputValue(),'570.2');assert.equal(await page.locator('dialog [name="extra_handicap"]').inputValue(),'0');
  await page.locator('dialog [name="TACIM"]').fill('580,2');await page.locator('dialog [name="note"]').fill('Gammelt målebrev var tastet forkert');
  await page.getByRole('button',{name:'Forhåndsvis genberegning',exact:true}).click();await page.locator('#calculation-preview table').waitFor();
  await page.screenshot({path:'build/onsdagsbane-historical-calculation.png',fullPage:true});
  await page.getByRole('button',{name:'Genberegn kun denne sejlads',exact:true}).click();await closeSaved();
  const after=[await getRace(first),await getRace(second),await getRace(third)];
  assert.equal(after[0].entries[0].rating,580.2);assert.equal(after[0].entries[0].instant,before[0].entries[0].instant);assert.equal(after[0].entries[0].start,before[0].entries[0].start);
  assert.deepEqual(after[1].entries,before[1].entries);assert.deepEqual(after[2].entries,before[2].entries);
  s=await getState();assert.equal(s.boats[0].ratings.TACIM,900);assert.equal(s.boats[0].extra_handicap,60);await publish();
  const stor=await(await page.request.get(base+`/api/results?series_id=${s.series_id}&course=stor`)).json();const lille=await(await page.request.get(base+`/api/results?series_id=${s.series_id}&course=lille`)).json();
  assert.equal(stor.overall.length,1);assert.equal(lille.overall.length,1);assert.equal(stor.overall[0].id,boat.id);assert.equal(lille.overall[0].id,boat.id);assert.equal(stor.overall[0].total,2);assert.equal(lille.overall[0].total,1);
  assert.equal(stor.overall[0].course,'stor');assert.equal(lille.overall[0].course,'lille');
  assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,calendar:true,editExistingSeries:true,addRoundFromSettings:true,fleetsPerRound:true,oneFleetPerEvening:true,oneBoatPerCourseStanding:true,coursesSeparate:true,historicalCorrection:true,snapshotsPreserved:true,gph:true,perRoundRatingSystem:true,gphWindDisabled:true,javascriptErrors:0}));
 }catch(error){await page.screenshot({path:'build/onsdagsbane-hierarchy-error.png',fullPage:true});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
