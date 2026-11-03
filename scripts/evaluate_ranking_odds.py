"""Compare prospective seasonal ranking and empirical pair predictions.

Tune prior SD on 2025 only; report untouched 2026 evaluation separately.
All forecasts precede the current race. Score each race equally, not each pair.
"""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from ranking_odds import fit, probabilities, ranking_orders


def evaluate(frame, prior_sd, years):
    rows=[]
    for (year,group), season in frame[frame.year.isin(years)].groupby(['year','group']):
        names=sorted(season.competitor.unique());lookup={n:i for i,n in enumerate(names)};n=len(names)
        races=[];mean=np.zeros(n);cov=np.eye(n)*prior_sd**2;wins=np.zeros((n,n));counts=np.zeros(n)
        for date,day in season.groupby('race_date',sort=True):
            forecast=probabilities(mean,cov)
            ids=np.array([lookup[n] for n in day.competitor]);times=day.beregnet_seconds.to_numpy()
            scores={'ranking':[],'empirical':[]};records=[]
            for i in range(len(ids)):
                for j in range(i+1,len(ids)):
                    a,b=ids[i],ids[j]
                    if times[i]==times[j]:continue
                    outcome=float(times[i]<times[j])
                    # Only score an established matchup, equally for both models.
                    if len(races)>=3 and counts[a]>=3 and counts[b]>=3:
                        empir=(wins[a,b]+.5)/(wins[a,b]+wins[b,a]+1)
                        for model,p in [('ranking',forecast[a,b]),('empirical',empir)]:
                            p=np.clip(p,1e-9,1-1e-9)
                            scores[model].append(((p-outcome)**2,-outcome*np.log(p)-(1-outcome)*np.log(1-p)))
                    records.append((a,b,outcome))
            for model,values in scores.items():
                if values:
                    rows.append(dict(year=int(year),group=group,date=date,model=model,prior_sd=prior_sd,
                        pairs=len(values),brier=np.mean([v[0] for v in values]),log_loss=np.mean([v[1] for v in values])))
            for a,b,y in records:wins[a,b]+=y;wins[b,a]+=1-y
            counts[ids]+=1
            races.append(ranking_orders(ids,times))
            mean,cov=fit(races,n,prior_sd,mean)
    return pd.DataFrame(rows)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output-dir',type=Path,default=ROOT/'analysis/results/odds_validation_2025_2026');args=p.parse_args()
    frame=pd.read_csv(ROOT/'analysis/results/redress_2025_state_history.csv')
    frame=frame[frame.year.isin([2025,2026]) & frame.observed & frame.beregnet_seconds.gt(0)].copy()
    out=args.output_dir;out.mkdir(parents=True,exist_ok=True)
    tuning=[]
    for sd in [.5,1,2,4]:
        r=evaluate(frame,sd,[2025]);r.to_csv(out/f'tuning_sd_{sd:g}.csv',index=False)
        score=r[r.model.eq('ranking')].log_loss.mean()
        tuning.append({'prior_sd':sd,'mean_race_log_loss':score})
        print('Tuning',sd,score,flush=True)
    chosen=min(tuning,key=lambda v:v['mean_race_log_loss'])['prior_sd']
    result=evaluate(frame,chosen,[2026]);result.to_csv(out/'held_out_races.csv',index=False)
    summary=result.groupby(['year','model']).agg(races=('date','size'),brier=('brier','mean'),log_loss=('log_loss','mean')).reset_index()
    summary.to_csv(out/'held_out_summary.csv',index=False)
    configuration={'prior_sd':chosen,'tuning_years':[2025],'evaluation_years':[2026],
        'selection_metric':'mean per-race pairwise log loss on tuning years', 'candidates':tuning}
    (out/'configuration.json').write_text(json.dumps(configuration,indent=2)+'\n')
    print(summary.to_string(index=False),flush=True)


if __name__=='__main__':main()
