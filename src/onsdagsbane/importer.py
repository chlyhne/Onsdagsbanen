"""Initial, repeat-safe import of archived public Manage2Sail API responses."""
import csv
from datetime import datetime, timedelta
import json
from pathlib import Path
import unicodedata
from zoneinfo import ZoneInfo

from m2s_combiner.parser import (parse_completed_race_labels_from_result_payload,
    parse_race_rows_from_result_payload, parse_discard_after_races_from_result_payload)
from m2s_combiner.redress.collect import parse_race_date

ROOT = Path(__file__).resolve().parents[2]
SEED = Path(__file__).parent / 'seed'
TZ = ZoneInfo('Europe/Copenhagen')
SOURCE = 'https://www.manage2sail.com/da-DK/event/Onsdagsbanen2026#!/'


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value)).casefold() if c.isalnum())


def optional_float(value):
    try:
        import math
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def repair_imported_text(db):
    """Repair exact Windows-decoded source values, preserving subsequent edits."""
    if db.execute("SELECT 1 FROM metadata WHERE key='seed_utf8'").fetchone():
        return
    for filename in sorted(SEED.glob('*.json')):
        payload = json.loads(filename.read_text(encoding='utf-8'))
        for entry in payload['EntryResults']:
            values = {'name': entry.get('BoatName') or entry['TeamName'],
                      'skipper': entry['TeamName'], 'sail_number': entry.get('SailNumber',''),
                      'boat_type': entry.get('BoatType',''), 'club': entry.get('ClubName','')}
            for field, correct in values.items():
                if not isinstance(correct, str):
                    continue
                try:
                    garbled = correct.encode('utf-8').decode('cp1252')
                except UnicodeDecodeError:
                    # Such a value could not have been imported by the old decoder.
                    continue
                if garbled != correct:
                    db.execute(f'UPDATE boat_profiles SET {field}=? WHERE {field}=? AND id IN '
                               '(SELECT profile_id FROM boats WHERE series_id=1 AND source_id=?)',
                               (correct, garbled, entry['Id']))
    db.execute("INSERT INTO metadata VALUES('seed_utf8','1')")


def seed(store):
    with store.connect(write=True) as db:
        if db.execute("SELECT 1 FROM metadata WHERE key='seed'").fetchone():
            repair_imported_text(db)
            return
        db.execute("INSERT INTO series(id,name,year,discards) VALUES(1,'Onsdagsbanen 2026',2026,'[]')")
        with (ROOT / 'omvendt_respit/resources/participants.csv').open(encoding='utf-8-sig') as f:
            local = {normalize(r['competitor']): r for r in csv.DictReader(f)}
        imported = 0
        for filename in sorted(SEED.glob('*.json')):
            payload = json.loads(filename.read_text(encoding='utf-8'))
            class_name = payload['RegattaName']
            course = 'lille' if class_name.lower().startswith('lille') else 'stor'
            labels = parse_completed_race_labels_from_result_payload(payload)
            parsed = parse_race_rows_from_result_payload(payload, class_name, labels)
            thresholds = parse_discard_after_races_from_result_payload(payload)
            db.execute('UPDATE series SET discards=? WHERE id=1', (json.dumps(thresholds),))
            infos = {f"R{r['RaceIndex']}": r for r in payload['RaceInfos']}
            for entry in payload['EntryResults']:
                participant = local.get(normalize(entry['TeamName']), {})
                ratings = {key: optional_float(participant.get(key)) for key in ('TACIL','TACIM','TACIH','TAUDL','TAUDM','TAUDH')}
                from . import fleet, handicaps
                profile_id=fleet.create_profile(db,{'name':entry.get('BoatName') or entry['TeamName'],'skipper':entry['TeamName'],'sail_number':entry.get('SailNumber',''),'boat_type':entry.get('BoatType',''),'club':entry.get('ClubName','')},ratings,0)
                boat_id=db.execute('INSERT INTO boats(series_id,class_name,course,profile_id,source_id) VALUES(1,?,?,?,?)',(class_name,course,profile_id,entry['Id'])).lastrowid
                for label, frame in parsed.items():
                    matches = frame[frame['competitor'] == entry['TeamName']]
                    if matches.empty:
                        continue
                    row = matches.iloc[0]
                    info = infos.get(label, {})
                    date = parse_race_date(info)
                    if date is None or not info.get('LengthNM'):
                        continue
                    start = date.replace(hour=0, minute=0, second=0, tzinfo=TZ) + timedelta(seconds=info.get('StartTime') or 0)
                    distance = float(info['LengthNM'])
                    wind = {0:'L',1:'M',2:'H'}.get(info.get('WindSpeedType'),'M')
                    db.execute("INSERT OR IGNORE INTO races(series_id,label,course,mode,start,route,distance,rating_key,published,source) VALUES(1,?,?,'normal',?,'[]',?,?,1,?)", (label,course,start.isoformat(),distance,'TACI'+wind,SOURCE))
                    race = db.execute('SELECT * FROM races WHERE series_id=1 AND course=? AND label=?', (course,label)).fetchone()
                    if race['round_course_id'] is None:
                        from .hierarchy import ensure_round
                        round_id=ensure_round(db,1,label)
                        course_id=db.execute('SELECT id FROM round_courses WHERE round_id=? AND course=?',(round_id,course)).fetchone()[0]
                        db.execute('UPDATE races SET round_course_id=? WHERE id=?',(course_id,race['id']))
                    if race['start'] != start.isoformat() or race['distance'] != distance:
                        db.execute("UPDATE races SET warning='Løbene har forskellige starttider eller distancer; samlet visning er vejledende.' WHERE id=?", (race['id'],))
                    set_id=handicaps.for_values(db,profile_id,{race['rating_key']:optional_float(row['hdcp'])},0)
                    db.execute('INSERT INTO entries(race_id,boat_id,class_name,start,distance,wind_category,handicap_set_id) VALUES(?,?,?,?,?,?,?)',(race['id'],boat_id,class_name,start.isoformat(),distance,wind,set_id))
                    elapsed = optional_float(row['sailed_seconds'])
                    corrected = optional_float(row['beregnet_seconds'])
                    status = str(row['race_status_code'] or '').strip()
                    status = status or ('FIN' if corrected is not None else 'DNC')
                    instant = (start + timedelta(seconds=elapsed)).isoformat() if elapsed is not None else None
                    raw = next(r for r in entry['EntryRaceResults'] if r.get('OverallRaceIndex')==int(label[1:]))
                    db.execute('INSERT INTO finishes(race_id,boat_id,status,instant,elapsed,corrected,sequence,note,request_id,source_points,source_discarded) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (race['id'],boat_id,status,instant,elapsed,corrected,boat_id,'Importeret fra Manage2Sail',f"import:{race['id']}:{boat_id}",optional_float(raw.get('Points')),int(bool(raw.get('PointsDiscarded')))))
                    imported += 1
        from .hierarchy import migrate_legacy
        migrate_legacy(db)
        from . import planning, fleet
        planning.migrate(db);fleet.migrate(db)
        from . import handicaps
        handicaps.migrate(db)
        from .timing import migrate
        migrate(db)
        db.execute("INSERT INTO metadata VALUES('seed',?)", (json.dumps({'source':SOURCE, 'imported_at':datetime.now(TZ).isoformat(),'results':imported}),))
        db.execute("INSERT INTO metadata VALUES('seed_utf8','1')")
