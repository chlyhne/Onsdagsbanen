'use strict';
let boatReturnContext=null;
function boatFleetContext(){
  if(!dialog.open||!$('#fleet-commander-form'))return null;
  const w=fleetWork;
  return {seriesId:w.data.series_id,heatId:w.heat.id,scope:w.scope,source:w.source,query:w.query,memberQuery:w.memberQuery,includeAssigned:w.includeAssigned};
}
async function editBoatProfile(id){
  const context=boatFleetContext(),boat=await api(`/api/boat-profiles?id=${id}`);
  boatReturnContext=context;
  openDialog(`Rediger ${e(boat.name)}`,`<form id="boat-profile-form" class="stack" data-profile-id="${id}"><input type="hidden" name="token" value="${e(boat.token)}"><div class="form-grid">${[['name','Bådnavn'],['skipper','Skipper'],['sail_number','Sejlnummer'],['boat_type','Bådtype'],['club','Klub']].map(([key,label])=>`<label class="field">${label}<input name="${key}" value="${e(boat[key])}" maxlength="160" ${key==='club'?'':'required'}></label>`).join('')}</div><h3>Standardhandicap</h3><div class="rating-grid">${keys.map((key,i)=>`<label class="field">${key}<span class="subtle">${keyNames[i]}</span><input name="${key}" value="${e(boat.ratings[key]??'')}" type="text" inputmode="decimal" data-number placeholder="Ikke udfyldt"></label>`).join('')}</div><label class="field">Ekstra handicap (sekunder)<input name="extra_handicap" type="text" inputmode="numeric" data-number value="${boat.extra_handicap}" required></label><p class="notice info">Oplysningerne gælder båden i alle serier. Nye handicaptal bruges fremover; gemte starttider, måltider og handicapsæt i eksisterende sejladser bevares.</p><div class="dialog-actions"><button type="button" data-action="close-dialog">Annuller</button><button type="submit" class="primary">Gem båd</button></div></form>`);
}
async function deleteBoatDialog(id){
  const context=boatFleetContext(),preview=await api(`/api/boat-profiles/delete-preview?id=${id}`);
  boatReturnContext=context;
  openDialog(`Slet ${e(preview.name)} fra systemet?`,`<form id="boat-delete-form" class="stack" data-profile-id="${id}"><input type="hidden" name="token" value="${e(preview.token)}">${preview.can_delete?`<p>Båden er ikke med i nogen ræs. Den slettes permanent fra hele systemets fælles bådliste; eventuelle tomme serietilmeldinger fjernes også.</p>${preview.series.length?`<p>Tomme serietilmeldinger, der fjernes:</p><ul>${preview.series.map(s=>`<li>${e(s.name)}</li>`).join('')}</ul>`:'<p>Båden er ikke tilmeldt nogen serie.</p>'}`:`<p class="notice" role="alert">${e(preview.reason)}</p><p>Båden er med i følgende ræs:</p><ul class="boat-delete-races">${(preview.rounds||[]).map(r=>`<li><strong>${e(r.series_name)} · ${e(r.label)}</strong><span>${e(fleetLabel(r.course))} · ${e(r.class_name)}</span></li>`).join('')}</ul>`}<div class="dialog-actions"><button type="button" data-action="close-dialog">${preview.can_delete?'Annuller':'Luk'}</button>${preview.can_delete?'<button type="submit" class="danger">Slet båd fra systemet</button>':''}</div></form>`);
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-profile-edit],[data-profile-delete]');
  if(!button||button.disabled||busy)return;
  try{
    if(button.dataset.profileEdit)await editBoatProfile(Number(button.dataset.profileEdit));
    else await deleteBoatDialog(Number(button.dataset.profileDelete));
  }catch(error){toast(error.message);}
});
document.addEventListener('submit',async event=>{
  const form=event.target;
  if(!['boat-profile-form','boat-delete-form'].includes(form.id))return;
  event.preventDefault();if(busy)return;busy=true;form.inert=true;
  try{
    const fields=Object.fromEntries(new FormData(form)),deleting=form.id==='boat-delete-form';
    const data={...fields,id:Number(form.dataset.profileId)};
    if(deleting)data.confirm=true;
    else{data.ratings=Object.fromEntries(keys.map(key=>[key,fields[key]||null]));data.extra_handicap=fields.extra_handicap;}
    await api(deleting?'/api/boat-profiles/delete':'/api/boat-profiles',data);
    await load();await render();dialog.close();
    toast(deleting?'Båden er slettet.':'Bådens oplysninger er gemt.');
  }catch(error){dialogError(error);}finally{busy=false;form.inert=false;}
});
dialog.addEventListener('cancel',event=>{
  if(busy&&($('#boat-profile-form')||$('#boat-delete-form')))event.preventDefault();
});
dialog.addEventListener('close',async()=>{
  const context=boatReturnContext;boatReturnContext=null;if(!context)return;
  try{
    await openFleetCommander(context.heatId,context.seriesId);
    fleetChooseHeat(context.heatId,context.scope);
    Object.assign(fleetWork,{source:context.source,query:context.query,memberQuery:context.memberQuery,includeAssigned:context.includeAssigned});
    renderFleetCommander();
  }catch(error){toast(error.message);}
});
