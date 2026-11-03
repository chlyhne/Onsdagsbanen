import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import test_app
from onsdagsbane.app import Application
from onsdagsbane.snapshot import export_snapshot, bootstrap_database, BUNDLED_SNAPSHOT
from onsdagsbane.store import audit


class SnapshotTests(unittest.TestCase):
    setUp=test_app.WebTests.setUp
    race=test_app.WebTests.race
    finish=test_app.WebTests.finish

    def test_export_preserves_races_and_removes_auth_even_from_deleted_pages(self):
        race=self.race();self.finish(race,self.boats[0])
        secret='DO_NOT_PUBLISH_AUTH_SENTINEL_987654321'
        with self.app.store.connect(write=True) as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(secret,1,secret,1e20))
            db.execute('INSERT INTO oauth VALUES(?,?,?,?,?)',(secret,'google',secret,secret,1e20))
            audit(db,self.user,'login',{'access_token':secret})
            audit(db,self.user,'race.note',{'race_id':race,'note':'Keep this note','nested':{'csrf':secret,'text':'Keep this text'}})
            expected=[tuple(r) for r in db.execute('SELECT * FROM finishes ORDER BY 1,2')]
        target=Path(self.temp.name)/'shared.sqlite3'
        export_snapshot(self.app.store.path,target)
        self.assertNotIn(secret.encode(),target.read_bytes())
        with closing(sqlite3.connect(target)) as db:
            for table in ('users','sessions','oauth'):self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
            self.assertEqual(expected,db.execute('SELECT * FROM finishes ORDER BY 1,2').fetchall())
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='login'").fetchone()[0],0)
            identity,payload=db.execute("SELECT user_id,payload FROM audit WHERE action='race.note'").fetchone()
            self.assertIsNone(identity)
            self.assertEqual(json.loads(payload),{'race_id':race,'note':'Keep this note','nested':{'text':'Keep this text'}})
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        with self.app.store.connect() as db:self.assertEqual(db.execute('SELECT token FROM sessions').fetchone()[0],secret)

    def test_first_server_start_copies_snapshot_and_restart_keeps_runtime_changes(self):
        self.race()
        snapshot=Path(self.temp.name)/'seed.sqlite3'
        export_snapshot(self.app.store.path,snapshot)
        digest=hashlib.sha256(snapshot.read_bytes()).digest()
        runtime=Path(self.temp.name)/'data/server.sqlite3'
        with patch('onsdagsbane.snapshot.BUNDLED_SNAPSHOT',snapshot):
            app=Application(db_path=runtime)
            with app.store.connect(write=True) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM boats').fetchone()[0],3)
                db.execute("UPDATE series SET name='Changed on server' WHERE id=?",(self.series,))
            self.assertFalse(bootstrap_database(runtime,snapshot))
            restarted=Application(db_path=runtime)
            with restarted.store.connect() as db:self.assertEqual(db.execute('SELECT name FROM series WHERE id=?',(self.series,)).fetchone()[0],'Changed on server')
        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).digest(),digest)
        self.assertFalse(Path(str(snapshot)+'-wal').exists())

    def test_existing_database_is_never_replaced_and_seed_cannot_be_runtime(self):
        target=Path(self.temp.name)/'existing.sqlite3'
        target.write_bytes(b'existing file')
        self.assertFalse(bootstrap_database(target,BUNDLED_SNAPSHOT))
        self.assertEqual(target.read_bytes(),b'existing file')
        with self.assertRaises(ValueError):bootstrap_database(BUNDLED_SNAPSHOT,BUNDLED_SNAPSHOT)

    def test_bundled_snapshot_is_complete_and_contains_no_access_records(self):
        with closing(sqlite3.connect(BUNDLED_SNAPSHOT)) as db:
            for table in ('users','sessions','oauth'):self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            missing=db.execute("SELECT count(*) FROM entries e JOIN handicap_sets h ON h.id=e.handicap_set_id WHERE json_extract(h.ratings,'$.TACIL') IS NULL OR json_extract(h.ratings,'$.TACIM') IS NULL OR json_extract(h.ratings,'$.TACIH') IS NULL").fetchone()[0]
            self.assertEqual(missing,0)
