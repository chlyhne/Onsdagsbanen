"""DH and series scoring. No implicit substitutions for missing ratings."""
from decimal import Decimal, ROUND_HALF_UP
import math
import re

RATINGS = ('TACIL', 'TACIM', 'TACIH', 'TAUDL', 'TAUDM', 'TAUDH')
PENALTIES = {'DNC', 'DNS', 'DNF', 'DSQ', 'DNE', 'RET', 'OCS', 'BFD', 'UFD'}


def number(value, *, optional=False):
    if optional and value in (None, ''):
        return None
    try:
        raw = str(value).strip()
        if not re.fullmatch(r'\d+(?:[.,]\d+)?',raw):
            raise ValueError
        result = float(raw.replace(',', '.'))
    except (ValueError, TypeError):
        raise ValueError('Angiv et gyldigt tal.') from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError('Tallet skal være positivt og endeligt.')
    return result


def gph(ratings):
    """DH 6.1.1: three parts TACIL, twelve TACIM and five TACIH, s/nm."""
    keys=('TACIL','TACIM','TACIH')
    values=[number(ratings.get(key),optional=True) for key in keys]
    if any(value is None for value in values):return None
    weighted=sum(Decimal(str(value))*weight for value,weight in zip(values,(3,12,5)))/Decimal(20)
    return float(weighted.quantize(Decimal('0.1'),rounding=ROUND_HALF_UP))


def rating_value(ratings,key):
    return gph(ratings) if key=='GPH' else ratings.get(key)


def rounded(value):
    return int(Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def correction(elapsed, rating, distance, reference=1000):
    return rounded(Decimal(str(elapsed)) + (Decimal(str(reference)) - Decimal(str(rating))) * Decimal(str(distance)))


def daily(rows, *, pursuit=False):
    """Low point, including A7 ties. Roster is frozen on each race."""
    rows = [dict(row) for row in rows]
    for row in rows:
        row['points'] = len(rows) + 1
        row['rank'] = None
    finished = [r for r in rows if r.get('status') in ('FIN', 'RDG')]
    def key(row):
        return row['sequence'] if pursuit else row['corrected']
    finished.sort(key=lambda r: (key(r), r['boat_id']))
    i = 0
    while i < len(finished):
        j = i + 1
        while j < len(finished) and key(finished[j]) == key(finished[i]):
            j += 1
        for row in finished[i:j]:
            row['rank'] = i + 1
            row['points'] = sum(range(i + 1, j + 1)) / (j - i)
        i = j
    return finished + sorted([r for r in rows if r.get('status') not in ('FIN', 'RDG')], key=lambda r: r['boat_id'])


def overall(boats, races, results, thresholds, source=False):
    """A2 discards, A8.1 score counts, A8.2 latest race, shared ranks."""
    count = sum(len(races) >= t for t in thresholds)
    entries = []
    for boat in boats:
        scores = []
        for race in races:
            rows = results[race['id']]
            row = next((r for r in rows if r['boat_id'] == boat['id']), None)
            if source and row is None:
                scores.append({'race_id':race['id'],'points':None,'status':'NA','discarded':False})
                continue
            scores.append({'race_id': race['id'], 'points': row['points'] if row else len(rows) + 1,
                           'status': row.get('status', 'DNC') if row else 'DNC', 'discarded': bool(row.get('source_discarded')) if source and row else False})
        eligible = sorted((i for i, s in enumerate(scores) if s['status'] != 'DNE' and s['points'] is not None), key=lambda i: (scores[i]['points'], i), reverse=True)
        if not source:
            for i in eligible[:min(count, max(0, len(scores) - 1))]:
                scores[i]['discarded'] = True
        kept = sorted(s['points'] for s in scores if not s['discarded'] and s['points'] is not None)
        entries.append({**boat, 'scores': scores, 'total': sum(kept)})
    # Equal totals compare the sorted kept scores, then all races from last to first.
    for entry in entries:
        entry['_key'] = (entry['total'], tuple(sorted(s['points'] for s in entry['scores'] if not s['discarded'] and s['points'] is not None)), tuple(s['points'] if s['points'] is not None else float('inf') for s in reversed(entry['scores'])))
    entries.sort(key=lambda r: (r['_key'], r['id']))
    previous = None
    for i, entry in enumerate(entries):
        entry['rank'] = previous['rank'] if previous and previous['_key'] == entry['_key'] else i + 1
        previous = entry
    for entry in entries:
        del entry['_key']
    return entries
