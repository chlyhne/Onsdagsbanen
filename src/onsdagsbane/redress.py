"""Adapter to the repository's real time-based redress model."""
from datetime import datetime
import math
from pathlib import Path
import pandas as pd

from m2s_combiner.redress.model import run_group_filter, load_group_qa_cache, estimate_initial_x0_from_first_observations
from m2s_combiner.redress.pipeline import _enrich_history
from .importer import normalize
from .service import editable, rows, instant, changed
from .store import get, audit
from .domain import rounded
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[2]


def apply_redress(db, race_id, boat_id, user, request_id):
    race = editable(db,race_id,allow_completed=True)
    boat = get(db,'boats',boat_id)
    roster = rows(db,race_id)
    target = next((r for r in roster if r['boat_id'] == boat_id),None)
    if not target:
        raise ValueError('Båden er ikke tilmeldt sejladsen.')
    if target['sequence'] is not None and target['status']!='DUTY':
        raise ValueError('Båden har allerede et resultat. Ret det først.')
    missing = [r['name'] for r in roster if r['boat_id'] != boat_id and r['sequence'] is None]
    if missing:
        raise ValueError('Dommertid kan først beregnes, når sejladsen er afsluttet og alle øvrige deltagere har et resultat. Mangler: ' + ', '.join(missing))
    if db.execute('SELECT 1 FROM captures WHERE race_id=? AND used=0',(race_id,)).fetchone():
        raise ValueError('Tildel eller kassér alle tidsstempler uden båd, før dommertid beregnes.')
    if any(r['status']=='FIN' and r['corrected'] is None for r in roster):
        raise ValueError('Redress kræver tidsmålte målgangsbåde. Ved jagtstart skal alle målgangsbåde have tider; rækkefølgen alene giver ikke et tidsgrundlag.')
    observed = [r for r in roster if r['status']=='FIN' and r['corrected'] is not None]
    if len(observed) < 2:
        raise ValueError('Redress-modellen kræver mindst to tidsmålte både på dagen. Ved ren målgangsrækkefølge mangler modellen tidsgrundlaget.')
    completed=db.execute('SELECT p.completed FROM rounds p JOIN round_courses c ON c.round_id=p.id WHERE c.id=?',(race['round_course_id'],)).fetchone()[0]
    if not completed:raise ValueError('Afslut først ræset for begge baner, før dommertid beregnes.')
    group_name = 'Lille Bane' if race['course']=='lille' else 'Stor Bane'
    q, q_gamma, k, warm = load_group_qa_cache(ROOT / 'analysis/models/redress_group_q_cache_mle.json')
    previous = [dict(r) for r in db.execute('SELECT * FROM races WHERE series_id=? AND course=? AND (published=1 OR id=?) AND start<=? ORDER BY start,id',(race['series_id'],race['course'],race_id,race['start']))]
    records, labels, dates = [], [], {}
    for item in previous:
        label = f"R{item['id']}"
        entries = rows(db,item['id'])
        if sum(r['status']=='FIN' and r['corrected'] is not None for r in entries)<2:
            continue
        labels.append(label)
        dates[label] = instant(item['start']).replace(tzinfo=None)
        for row in entries:
            is_observed = row['status']=='FIN' and row['corrected'] is not None
            records.append({'competitor':str(row['boat_id']), 'race':label, 'race_local':label,
                'race_date':dates[label],
                'series':row['class_name'], 'race_status_code':'' if is_observed else 'DNC',
                'beregnet_seconds':row['corrected']-row['extra_handicap']-row['penalty']-rounded((Decimal(str(item['reference']))-Decimal('1000'))*Decimal(str(row['distance']))) if is_observed else None,
                'sailed_seconds':row['elapsed'] if is_observed else None,
                'hdcp':row['rating'], 'length_nm':row['distance']})
    group = {'group':group_name,'combined':pd.DataFrame(records),'selected_races':labels,'race_dates':dates}
    warm_by_name = {normalize(name):state for name,state in warm.get(group_name,{}).items()}
    initial = {}
    for row in db.execute('SELECT b.id,p.skipper FROM boats b JOIN boat_profiles p ON p.id=b.profile_id WHERE b.series_id=?',(race['series_id'],)):
        if normalize(row['skipper']) in warm_by_name:
            initial[str(row['id'])] = warm_by_name[normalize(row['skipper'])]
    if str(boat_id) not in initial and not any(r['competitor']==str(boat_id) and r['beregnet_seconds'] is not None and r['race']!=f'R{race_id}' for r in records):
        raise ValueError('Båden mangler tidsmålt historik til en forsvarlig redress-beregning.')
    group_q = q[group_name]
    prediction = run_group_filter(group,group_q,q_gamma=q_gamma[group_name],initial_p0=group_q*k[group_name],
        initial_x0=estimate_initial_x0_from_first_observations(group),initial_state_by_competitor=initial,
        collect_history=True).history
    mapping = {(str(row['competitor']),int(row['year'])):group_name for _,row in prediction.iterrows()}
    enriched = _enrich_history(prediction,mapping)
    predicted = enriched[(enriched['competitor']==str(boat_id)) & (enriched['race']==f'R{race_id}')]
    if len(predicted)!=1:
        raise ValueError('Modellen kunne ikke lave én entydig forudsigelse.')
    result = predicted.iloc[0]
    corrected, elapsed = float(result['pred_cf_beregnet_seconds']),float(result['pred_cf_sailed_seconds'])
    if not all(math.isfinite(v) and v>0 for v in (corrected,elapsed)):
        raise ValueError('Modellen gav ingen gyldig tid. Kontrollér data.')
    if race['mode']=='pursuit':
        # A predicted finish time is inserted between the observed finishers.
        # Pure ordinal finishes have no time axis and are rejected above/below.
        finish_axis = elapsed + (instant(target['start'])-instant(race['start'])).total_seconds()
        before = sum((r['elapsed']+(instant(r['start'])-instant(race['start'])).total_seconds()+r['penalty']) < finish_axis for r in observed)
        ordered = sorted(observed,key=lambda r:r['sequence'])
        lower = ordered[before-1]['sequence'] if before else 0
        upper = ordered[before]['sequence'] if before<len(ordered) else (ordered[-1]['sequence']+2)
        # Reserve integer slots while preserving existing order.
        db.execute('UPDATE finishes SET sequence=sequence*2 WHERE race_id=?',(race_id,))
        sequence = lower+upper
    else:
        sequence = db.execute('SELECT coalesce(max(sequence),0)+1 FROM finishes WHERE race_id=?',(race_id,)).fetchone()[0]
    corrected = corrected + rounded((Decimal(str(race['reference']))-Decimal('1000'))*Decimal(str(target['distance']))) + target['extra_handicap']
    note = f"Dommertjans · repoets redress-model · Q={group_q:.6g} · modeltid {corrected:.3f} s · ekstra handicap +{target['extra_handicap']:g} s"
    db.execute('INSERT INTO finishes(race_id,boat_id,status,instant,elapsed,corrected,sequence,note,request_id) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(race_id,boat_id) DO UPDATE SET status=excluded.status,instant=NULL,elapsed=excluded.elapsed,corrected=excluded.corrected,sequence=excluded.sequence,note=excluded.note,request_id=excluded.request_id,penalty=0,source_points=NULL,source_discarded=NULL',(race_id,boat_id,'RDG',None,rounded(elapsed),rounded(corrected),sequence,note,request_id))
    changed(db,race_id)
    audit(db,user,'redress.apply',{'race_id':race_id,'boat_id':boat_id,'prediction':corrected,'q':group_q,'q_gamma':q_gamma[group_name]})
    return {'corrected':rounded(corrected),'elapsed':rounded(elapsed),'note':note}
