from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import time
from uuid import uuid4

SCHEMA = '''
CREATE TABLE IF NOT EXISTS series(id INTEGER PRIMARY KEY, name TEXT NOT NULL, year INTEGER NOT NULL, discards TEXT NOT NULL DEFAULT '[]', reference REAL NOT NULL DEFAULT 1000);
CREATE TABLE IF NOT EXISTS rounds(id INTEGER PRIMARY KEY, uid TEXT NOT NULL UNIQUE, series_id INTEGER NOT NULL REFERENCES series(id), label TEXT NOT NULL, position INTEGER NOT NULL, UNIQUE(series_id,label));
CREATE TABLE IF NOT EXISTS round_courses(id INTEGER PRIMARY KEY, round_id INTEGER NOT NULL REFERENCES rounds(id), course TEXT NOT NULL CHECK(course IN ('lille','stor')), UNIQUE(round_id,course));
CREATE TABLE IF NOT EXISTS heats(id INTEGER PRIMARY KEY, round_course_id INTEGER NOT NULL REFERENCES round_courses(id), name TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, UNIQUE(round_course_id,name));
CREATE TABLE IF NOT EXISTS heat_boats(heat_id INTEGER NOT NULL REFERENCES heats(id), boat_id INTEGER NOT NULL REFERENCES boats(id), PRIMARY KEY(heat_id,boat_id));
CREATE TABLE IF NOT EXISTS boat_profiles(id INTEGER PRIMARY KEY, name TEXT NOT NULL, skipper TEXT NOT NULL, sail_number TEXT NOT NULL, boat_type TEXT NOT NULL, club TEXT NOT NULL DEFAULT '', current_handicap_set_id INTEGER REFERENCES handicap_sets(id));
CREATE TABLE IF NOT EXISTS handicap_sets(id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES boat_profiles(id), ratings TEXT NOT NULL, extra_handicap REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL, UNIQUE(profile_id,ratings,extra_handicap));
CREATE TABLE IF NOT EXISTS boats(id INTEGER PRIMARY KEY, series_id INTEGER NOT NULL REFERENCES series(id), class_name TEXT NOT NULL, course TEXT NOT NULL CHECK(course IN ('lille','stor')), profile_id INTEGER NOT NULL REFERENCES boat_profiles(id), source_id TEXT, UNIQUE(series_id,source_id));
CREATE TABLE IF NOT EXISTS races(id INTEGER PRIMARY KEY, series_id INTEGER NOT NULL REFERENCES series(id), label TEXT NOT NULL, course TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('normal','pursuit')), start TEXT NOT NULL, route TEXT NOT NULL, distance REAL NOT NULL, rating_key TEXT NOT NULL, reference REAL NOT NULL DEFAULT 1000, published INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0, source TEXT, warning TEXT NOT NULL DEFAULT '', UNIQUE(series_id,course,label));
CREATE TABLE IF NOT EXISTS entries(race_id INTEGER NOT NULL REFERENCES races(id), boat_id INTEGER NOT NULL REFERENCES boats(id), class_name TEXT NOT NULL, start TEXT NOT NULL, distance REAL NOT NULL, wind_category TEXT NOT NULL DEFAULT 'M', handicap_set_id INTEGER NOT NULL REFERENCES handicap_sets(id), PRIMARY KEY(race_id,boat_id));
CREATE TABLE IF NOT EXISTS finishes(race_id INTEGER NOT NULL, boat_id INTEGER NOT NULL, status TEXT NOT NULL, instant TEXT, elapsed REAL, corrected REAL, sequence INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', request_id TEXT NOT NULL UNIQUE, source_points REAL, source_discarded INTEGER, penalty REAL NOT NULL DEFAULT 0 CHECK(penalty>=0), PRIMARY KEY(race_id,boat_id), FOREIGN KEY(race_id,boat_id) REFERENCES entries(race_id,boat_id));
CREATE TABLE IF NOT EXISTS captures(id INTEGER PRIMARY KEY, race_id INTEGER NOT NULL REFERENCES races(id), instant TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0, request_id TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, provider TEXT NOT NULL, subject TEXT NOT NULL, email TEXT NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'recorder' CHECK(role IN ('recorder','admin')), approved INTEGER NOT NULL DEFAULT 0, UNIQUE(provider,subject));
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), csrf TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS oauth(state TEXT PRIMARY KEY, provider TEXT NOT NULL, binding TEXT NOT NULL, verifier TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL NOT NULL, user_id INTEGER, action TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            from .migrations import bootstrap_v1, migrate_participations
            version=db.execute('PRAGMA user_version').fetchone()[0]
            if version<1:
                bootstrap_v1(db)
                db.execute('PRAGMA user_version=1')
                db.commit()
            if version<2:
                migrate_participations(db)
            if version<3:
                from .migrations import migrate_observations
                migrate_observations(db)
            if version<4:
                from .migrations import migrate_reference_clock
                migrate_reference_clock(db)
            if version<5:
                from .migrations import retire_loose_timing
                retire_loose_timing(db)
            db.execute('PRAGMA journal_mode=WAL')

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


def audit(db, user, action, payload):
    db.execute('INSERT INTO audit(at,user_id,action,payload) VALUES(?,?,?,?)', (time.time(), user['id'] if user else None, action, json.dumps(payload, ensure_ascii=False)))


def get(db, table, identity):
    assert table in {'series', 'boats', 'races', 'users', 'captures', 'rounds', 'round_courses', 'heats'}
    if table=='boats':
        row=db.execute('SELECT b.*,p.name,p.skipper,p.sail_number,p.boat_type,p.club,p.current_handicap_set_id,h.ratings,h.extra_handicap FROM boats b JOIN boat_profiles p ON p.id=b.profile_id JOIN handicap_sets h ON h.id=p.current_handicap_set_id WHERE b.id=?',(identity,)).fetchone()
    else:
        row = db.execute(f'SELECT * FROM {table} WHERE id=?', (identity,)).fetchone()
    if row is None:
        raise ValueError('Posten findes ikke.')
    return dict(row)
