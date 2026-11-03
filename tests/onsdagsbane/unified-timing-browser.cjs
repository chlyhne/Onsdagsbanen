/* Isolated --demo only. One evening, both courses, atomic completion and reopening. */
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/home/casper/Documents/sejlrelateret/racetimer/node_modules/playwright');
const base=process.env.ONSDAGSBANE_BASE_URL||'http://127.0.0.1:8096';
const local=date=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Copenhagen',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(date).replace(' ','T');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:1000},timezoneId:'Europe/Copenhagen'});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const call=(path,data)=>page.evaluate(({path,data})=>api(path,data),{path,data});
 const roundDetail=id=>call(`/api/rounds/${id}/timing`);
 async function saved(){await page.locator('dialog').waitFor({state:'hidden'});await page.waitForFunction(()=>!busy);}
 async function finish(boatId){await page.locator(`[data-measure="${boatId}"]`).click();await page.getByRole('button',{name:'Bekræft',exact:true}).click();await page.locator(`[data-measured="${boatId}"]`).waitFor();}
 try{
  await page.goto(base);await page.getByRole('heading',{name:'Resultater',exact:true}).waitFor();assert.equal((await call('/api/state')).demo,true);
  const stamp=local(new Date()),year=Number(stamp.slice(0,4)),today=stamp.slice(0,10),previous=local(new Date(Date.now()-7*86400000)).slice(0,10),next=local(new Date(Date.now()+86400000)).slice(0,10);
  const series=await call('/api/series',{name:'Test fælles måltagning '+Date.now(),year,round_labels:['Før','I dag','Næste'],fleets:{stor:['Stor A'],lille:['Lille A']}});
  const boats=[];
  for(const [name,course] of [['Alpha','stor'],['Bravo','stor'],['Jury','stor'],['Large DNC','stor'],['Gamma','lille'],['Little DNC','lille']])boats.push({name,course,...await call('/api/boats',{series_id:series.id,name,course,class_name:course==='stor'?'Stor A':'Lille A',skipper:'Test '+name,sail_number:'DEN '+(700+boats.length),boat_type:'X-332',ratings:{TACIL:700,TACIM:600,TACIH:500}})});
  const state=await call(`/api/state?series_id=${series.id}`),rounds=Object.fromEntries(state.rounds.map(round=>[round.label,round]));
  const races={};
  for(const [label,day] of [['Før',previous],['I dag',today],['Næste',next]]){
   races[label]={};
   for(const course of ['lille','stor'])races[label][course]=(await call('/api/races',{series_id:series.id,round_id:rounds[label].id,course,mode:'normal',handicap_system:'circle',wind_category:'M',start:day+'T00:01:00',route:course==='stor'?['A','D']:['A']})).id;
  }
  // Real prior observations for the duty model.
  for(const [index,boat] of boats.entries())await call(`/api/races/${races['Før'][boat.course]}/finish`,{boat_id:boat.id,instant:previous+`T01:${String(index).padStart(2,'0')}:00`,request_id:crypto.randomUUID()});
  let prior=await roundDetail(rounds['Før'].id);await call(`/api/rounds/${rounds['Før'].id}/publish`,{revisions:Object.fromEntries(prior.courses.map(r=>[String(r.id),r.revision]))});
  await page.evaluate(async id=>{state.seriesId=id;state.timingRaceId=null;state.timingRoundId=null;await load();await render();},series.id);await page.getByRole('button',{name:'Måltagning',exact:true}).click();await page.locator('#timing-race').waitFor();
  assert.equal(await page.locator('#timing-race').inputValue(),String(rounds['I dag'].id));
  assert.equal(await page.locator('[data-unmeasured]').count(),6);assert.match(await page.locator('#timing-context').innerText(),/Stor bane:/);assert.match(await page.locator('#timing-context').innerText(),/Lille bane:/);
  const alpha=boats.find(b=>b.name==='Alpha'),bravo=boats.find(b=>b.name==='Bravo'),jury=boats.find(b=>b.name==='Jury'),gamma=boats.find(b=>b.name==='Gamma');
  // The shared clock is attached to the first course (little); assign it to a large-course boat.
  await page.getByRole('button',{name:'Tag tid nu',exact:true}).click();await page.waitForSelector('[data-capture]');
  await page.locator('#boat-search').fill('Alpha');await page.locator(`[data-measure="${alpha.id}"]`).click();await page.getByRole('button',{name:'Bekræft',exact:true}).click();await page.locator(`[data-measured="${alpha.id}"]`).waitFor();
  await page.locator('#boat-search').fill('Gamma');await finish(gamma.id);assert.match(await page.locator(`[data-measured="${gamma.id}"]`).innerText(),/Lille bane/);
  await page.locator('#boat-search').fill('Bravo');await finish(bravo.id);
  await page.locator('#boat-search').fill('Jury');await page.locator(`[data-measure="${jury.id}"]`).click();await page.getByRole('button',{name:'Dommertjans',exact:true}).click();await page.locator(`[data-measured="${jury.id}"]`).waitFor();await page.locator('#boat-search').fill('');
  // Unconfirmed captures cannot disappear into automatic DNC.
  await page.getByRole('button',{name:'Tag tid nu',exact:true}).click();await page.waitForSelector('[data-capture]');await page.getByRole('button',{name:'Annuller aktiv måltagning',exact:true}).click();
  await page.getByRole('button',{name:'Afslut ræs',exact:true}).click();assert.equal(await page.locator('#round-completion-form').count(),0);
  await page.locator('[data-timing-action="discard"]').click();await page.waitForFunction(()=>document.querySelectorAll('[data-capture]').length===0);
  const before=await roundDetail(rounds['I dag'].id);assert.equal(before.entries.filter(row=>row.sequence===null).length,2);
  await page.getByRole('button',{name:'Afslut ræs',exact:true}).click();await page.locator('#round-completion-form').waitFor();assert.match(await page.locator('#round-completion-form').innerText(),/2 ikke-måltagne både/);await page.locator('#round-completion-form').getByRole('button',{name:'Afslut ræs',exact:true}).click();await saved();
  let closed=await roundDetail(rounds['I dag'].id);assert.equal(closed.round.completed,1);assert.equal(closed.entries.filter(r=>r.status==='DNC').length,2);assert.equal(closed.entries.find(r=>r.boat_id===jury.id).status,'RDG');assert.equal(closed.entries.filter(r=>r.sequence===null).length,0);
  assert.equal(await page.locator('[data-unmeasured]').count(),0);assert.equal(await page.getByRole('button',{name:'Tag tid nu',exact:true}).isDisabled(),true);
  for(const row of before.entries.filter(r=>r.instant))assert.equal(closed.entries.find(r=>r.boat_id===row.boat_id).instant,row.instant);
  await page.screenshot({path:'build/onsdagsbane-unified-timing-completed.png',fullPage:true});
  assert.ok(closed.entries.find(r=>r.boat_id===jury.id).corrected>0);assert.equal(await page.getByRole('button',{name:'Dommertid',exact:true}).count(),0);
  await page.getByRole('button',{name:'Offentliggør resultater →',exact:true}).click();await page.getByRole('button',{name:'Offentliggør',exact:true}).click();await saved();assert.equal((await roundDetail(rounds['I dag'].id)).courses.every(r=>r.published),true);
  await page.getByRole('button',{name:'Resultater',exact:true}).click();await page.getByRole('button',{name:'Måltagning',exact:true}).click();await page.waitForFunction(id=>document.querySelector('#timing-race')?.value===String(id),rounds['Næste'].id);
  // The automatic choice ignores a previously selected completed round.
  await page.locator('#timing-race').selectOption(String(rounds['I dag'].id));await page.getByRole('button',{name:'Genåbn ræs',exact:true}).click();await page.locator('#round-completion-form').getByRole('button',{name:'Genåbn ræs',exact:true}).click();await saved();
  const reopened=await roundDetail(rounds['I dag'].id);assert.equal(reopened.round.completed,0);assert.equal(reopened.courses.every(r=>!r.published),true);assert.equal(reopened.entries.filter(r=>r.sequence===null).length,2);assert.equal(reopened.entries.find(r=>r.boat_id===jury.id).status,'DUTY');
  for(const row of before.entries.filter(r=>r.instant))assert.equal(reopened.entries.find(r=>r.boat_id===row.boat_id).instant,row.instant);
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'build/onsdagsbane-unified-timing-mobile.png',fullPage:true});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,todaySelected:true,nextUnfinishedSelected:true,bothCoursesTogether:true,captureAcrossCourses:true,allCoursesDnc:true,dutyPreserved:true,publicationTogether:true,reopenPreservesTimes:true,javascriptErrors:0}));
 }catch(error){await page.screenshot({path:'build/onsdagsbane-unified-timing-error.png',fullPage:true});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
