"""Versioned, one-time database migrations. Never run business migrations per startup."""
from uuid import uuid4
from pathlib import Path
import sqlite3


def migrate_observations(db):
    """Add durable observations independent of course setup, atomically."""
    db.commit()
    try:
        db.execute('BEGIN IMMEDIATE')
        script=(Path(__file__).parent/'schema/003_unconfigured_timing.sql').read_text(encoding='utf-8')
        statement=''
        for line in script.splitlines():
            statement+=line+'\n'
            if sqlite3.complete_statement(statement):
                db.execute(statement);statement=''
        db.execute('PRAGMA user_version=3')
        db.commit()
    except BaseException:
        db.rollback();raise


def migrate_reference_clock(db):
    db.commit()
    try:
        db.execute('BEGIN IMMEDIATE')
        statement=''
        for line in (Path(__file__).parent/'schema/004_reference_clock.sql').read_text(encoding='utf-8').splitlines():
            statement+=line+'\n'
            if sqlite3.complete_statement(statement):
                db.execute(statement);statement=''
        db.execute('PRAGMA user_version=4')
        db.commit()
    except BaseException:
        db.rollback();raise


def retire_loose_timing(db):
    """Archive the retired unassigned queue without deleting its timestamps."""
    from .store import audit
    db.commit()
    try:
        db.execute('BEGIN IMMEDIATE')
        retired={table:[dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE used=0 ORDER BY id')] for table in ('captures','round_captures')}
        for table in retired:db.execute(f'UPDATE {table} SET used=1 WHERE used=0')
        if any(retired.values()):audit(db,None,'timing.loose.retired',retired)
        db.execute('PRAGMA user_version=5')
        db.commit()
    except BaseException:
        db.rollback();raise


def migrate_participations(db):
    """Atomic v1 -> v2 conversion; preserve IDs and every measured value."""
    db.commit()
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        db.execute('BEGIN IMMEDIATE')
        for row in list(db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")):
            name=row[0].replace('"','""')
            db.execute(f'DROP TRIGGER "{name}"')
        for name in ('entries','heat_boats','finishes'):
            db.execute(f'ALTER TABLE {name} RENAME TO v1_{name}')
        script=(Path(__file__).parent/'schema/002_participations.sql').read_text(encoding='utf-8')
        statement=''
        for line in script.splitlines():
            statement+=line+'\n'
            if sqlite3.complete_statement(statement):
                db.execute(statement);statement=''
        violations=db.execute('PRAGMA foreign_key_check').fetchall()
        if violations:raise ValueError(f'Datamigreringen brød en relation: {tuple(violations[0])}')
        db.execute('PRAGMA user_version=2')
        db.commit()
    except BaseException:
        db.rollback();raise
    finally:
        db.execute('PRAGMA foreign_keys=ON')


def bootstrap_v1(db):
    from .store import SCHEMA
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
