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
            db.executescript(SCHEMA)
            for table in ('boats','entries'):
                columns = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                if 'penalty' in columns and 'extra_handicap' not in columns:
                    db.execute(f'ALTER TABLE {table} RENAME COLUMN penalty TO extra_handicap')
            if 'penalty' not in {r['name'] for r in db.execute('PRAGMA table_info(finishes)')}:
                db.execute('ALTER TABLE finishes ADD COLUMN penalty REAL NOT NULL DEFAULT 0')
            # Additive migrations also support locally created early databases.
            for table,column,definition in [('series','reference','REAL NOT NULL DEFAULT 1000'),('entries','wind_category',"TEXT NOT NULL DEFAULT 'M'"),('finishes','source_points','REAL'),('finishes','source_discarded','INTEGER'),('races','round_course_id','INTEGER REFERENCES round_courses(id)'),('rounds','handicap_system',"TEXT NOT NULL DEFAULT 'circle'"),('rounds','completed','INTEGER NOT NULL DEFAULT 0'),('rounds','completed_at','TEXT'),('finishes','auto_dnc','INTEGER NOT NULL DEFAULT 0'),('series','default_start_stor',"TEXT NOT NULL DEFAULT '18:30:00'"),('series','default_start_lille',"TEXT NOT NULL DEFAULT '18:35:00'"),('round_courses','start_time','TEXT'),('rounds','race_date','TEXT'),('boats','profile_id','INTEGER REFERENCES boat_profiles(id)'),('entries','handicap_set_id','INTEGER REFERENCES handicap_sets(id)'),('boat_profiles','current_handicap_set_id','INTEGER REFERENCES handicap_sets(id)')]:
                if column not in {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            if 'uid' not in {r['name'] for r in db.execute('PRAGMA table_info(rounds)')}:
                db.execute('ALTER TABLE rounds ADD COLUMN uid TEXT')
            for row in list(db.execute("SELECT id FROM rounds WHERE uid IS NULL OR uid=''")):
                db.execute('UPDATE rounds SET uid=? WHERE id=?',(str(uuid4()),row['id']))
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS round_uid ON rounds(uid)')
            entry_columns={r['name'] for r in db.execute('PRAGMA table_info(entries)')}
            if 'rating' in entry_columns:
                if 'ratings_snapshot' not in entry_columns:db.execute('ALTER TABLE entries ADD COLUMN ratings_snapshot TEXT')
                if 'extra_handicap' not in entry_columns:db.execute('ALTER TABLE entries ADD COLUMN extra_handicap REAL NOT NULL DEFAULT 0')
                db.execute("UPDATE entries SET ratings_snapshot=json_object((SELECT rating_key FROM races WHERE id=entries.race_id),rating) WHERE ratings_snapshot IS NULL")
            boat_columns={r['name'] for r in db.execute('PRAGMA table_info(boats)')}
            if 'ratings' in boat_columns and 'extra_handicap' not in boat_columns:db.execute('ALTER TABLE boats ADD COLUMN extra_handicap REAL NOT NULL DEFAULT 0')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS race_round_course ON races(round_course_id)')
            from .hierarchy import migrate_legacy
            migrate_legacy(db)
            from . import planning, fleet
            planning.migrate(db);fleet.migrate(db)
            from . import handicaps
            handicaps.migrate(db)
            for table,retired in [('boats',('name','skipper','sail_number','boat_type','club','ratings','extra_handicap')),('entries',('rating','extra_handicap','ratings_snapshot')),('boat_profiles',('ratings','extra_handicap'))]:
                columns={r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                for column in retired:
                    if column in columns:db.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
            db.executescript('''CREATE TRIGGER IF NOT EXISTS handicap_sets_immutable BEFORE UPDATE ON handicap_sets BEGIN SELECT RAISE(ABORT, "Opret et nyt handicapsæt i stedet for at ændre et brugt sæt."); END;
CREATE TRIGGER IF NOT EXISTS entry_handicap_owner_insert BEFORE INSERT ON entries WHEN NEW.handicap_set_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM handicap_sets h JOIN boats b ON b.profile_id=h.profile_id WHERE h.id=NEW.handicap_set_id AND b.id=NEW.boat_id) BEGIN SELECT RAISE(ABORT, "Handicapsættet skal høre til båden."); END;
CREATE TRIGGER IF NOT EXISTS entry_handicap_owner_update BEFORE UPDATE OF handicap_set_id,boat_id ON entries WHEN NEW.handicap_set_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM handicap_sets h JOIN boats b ON b.profile_id=h.profile_id WHERE h.id=NEW.handicap_set_id AND b.id=NEW.boat_id) BEGIN SELECT RAISE(ABORT, "Handicapsættet skal høre til båden."); END;''')
            db.executescript('''CREATE TRIGGER IF NOT EXISTS profile_handicap_owner BEFORE UPDATE OF current_handicap_set_id ON boat_profiles WHEN NEW.current_handicap_set_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM handicap_sets h WHERE h.id=NEW.current_handicap_set_id AND h.profile_id=NEW.id) BEGIN SELECT RAISE(ABORT, "Handicapsættet skal høre til båden."); END;
CREATE TRIGGER IF NOT EXISTS boat_handicap_identity BEFORE UPDATE OF profile_id ON boats WHEN EXISTS(SELECT 1 FROM entries e JOIN handicap_sets h ON h.id=e.handicap_set_id WHERE e.boat_id=OLD.id AND h.profile_id<>NEW.profile_id) BEGIN SELECT RAISE(ABORT, "Bådens handicapsæt må ikke miste sin ejer."); END;''')
            db.executescript('''CREATE TRIGGER IF NOT EXISTS entry_handicap_required BEFORE INSERT ON entries WHEN NEW.handicap_set_id IS NULL BEGIN SELECT RAISE(ABORT, "Deltagelsen skal referere et handicapsæt."); END;
CREATE TRIGGER IF NOT EXISTS entry_handicap_required_update BEFORE UPDATE OF handicap_set_id ON entries WHEN NEW.handicap_set_id IS NULL BEGIN SELECT RAISE(ABORT, "Deltagelsen skal referere et handicapsæt."); END;''')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS boat_series_profile ON boats(series_id,profile_id)')
            from .timing import migrate
            migrate(db)
            db.executescript('''CREATE TRIGGER IF NOT EXISTS round_uid_required BEFORE INSERT ON rounds WHEN NEW.uid IS NULL OR NEW.uid='' BEGIN SELECT RAISE(ABORT, "Ræset skal have et UID."); END;
CREATE TRIGGER IF NOT EXISTS round_uid_immutable BEFORE UPDATE OF uid ON rounds WHEN NEW.uid IS NOT OLD.uid BEGIN SELECT RAISE(ABORT, "Ræsets UID kan ikke ændres."); END;
CREATE TRIGGER IF NOT EXISTS heat_boat_one_round_insert BEFORE INSERT ON heat_boats WHEN EXISTS(SELECT 1 FROM heat_boats m JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id WHERE m.boat_id=NEW.boat_id AND m.heat_id<>NEW.heat_id AND c.round_id=(SELECT c2.round_id FROM heats h2 JOIN round_courses c2 ON c2.id=h2.round_course_id WHERE h2.id=NEW.heat_id)) BEGIN SELECT RAISE(ABORT, "En båd kan kun være i ét løb i samme ræs i serien."); END;
CREATE TRIGGER IF NOT EXISTS heat_boat_one_round_update BEFORE UPDATE OF heat_id,boat_id ON heat_boats WHEN EXISTS(SELECT 1 FROM heat_boats m JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id WHERE m.boat_id=NEW.boat_id AND NOT(m.boat_id=OLD.boat_id AND m.heat_id=OLD.heat_id) AND c.round_id=(SELECT c2.round_id FROM heats h2 JOIN round_courses c2 ON c2.id=h2.round_course_id WHERE h2.id=NEW.heat_id)) BEGIN SELECT RAISE(ABORT, "En båd kan kun være i ét løb i samme ræs i serien."); END;''')
            db.commit()
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
