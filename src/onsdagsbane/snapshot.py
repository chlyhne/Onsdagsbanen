"""Export shareable race data and bootstrap an independent server database."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile

BUNDLED_SNAPSHOT=Path(__file__).parent/'seed/onsdagsbane.sqlite3'
AUTH_TABLES=('sessions','oauth','users')


def _connect_readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)


def _temporary(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    os.close(descriptor)
    return Path(name)


def _public_payload(value):
    if isinstance(value,list):return [_public_payload(v) for v in value]
    if not isinstance(value,dict):return value
    private={'token','csrf','password','secret','verifier','binding','authorization','database','backup'}
    return {key:_public_payload(v) for key,v in value.items() if key.casefold() not in private and not key.casefold().endswith(('_token','_secret'))}


def export_snapshot(source,target=BUNDLED_SNAPSHOT):
    source,target=Path(source),Path(target)
    if source.resolve()==target.resolve():raise ValueError('Eksporten skal have en anden fil end driftsdatabasen.')
    temporary=_temporary(target)
    try:
        original=_connect_readonly(source)
        copy=sqlite3.connect(temporary)
        try:
            original.backup(copy)
            copy.execute('PRAGMA foreign_keys=ON')
            copy.execute('PRAGMA secure_delete=ON')
            for table in AUTH_TABLES:copy.execute(f'DELETE FROM {table}')
            for identity,action,payload in list(copy.execute('SELECT id,action,payload FROM audit')):
                if action in ('login','logout','users') or action.startswith(('auth.','oauth.','session.','user.','users.','preview.')):
                    copy.execute('DELETE FROM audit WHERE id=?',(identity,))
                else:
                    copy.execute('UPDATE audit SET user_id=NULL,payload=? WHERE id=?',(json.dumps(_public_payload(json.loads(payload)),ensure_ascii=False),identity))
            counts={t:copy.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('boat_profiles','boats','series','races','finishes')}
            info={'exported_at':datetime.now(timezone.utc).isoformat(),'counts':counts,'authentication':'excluded'}
            copy.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('database_snapshot',?)",(json.dumps(info),))
            if counts['series']:
                copy.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('seed',?)",(json.dumps({'source':'Database snapshot'}),))
                copy.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('seed_utf8','1')")
            copy.commit()
            copy.execute('PRAGMA journal_mode=DELETE')
            copy.execute('VACUUM')
            if copy.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Databasen har ugyldige relationer.')
            if copy.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Databasen kunne ikke verificeres.')
            for table in AUTH_TABLES:assert copy.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
        finally:
            copy.close();original.close()
        os.replace(temporary,target)
        return info
    finally:
        temporary.unlink(missing_ok=True)


def bootstrap_database(target,source=None):
    """Publish the initial copy atomically; an existing runtime DB always wins."""
    target=Path(target)
    source=Path(source) if source is not None else BUNDLED_SNAPSHOT
    if target.resolve()==source.resolve():raise ValueError('Startdatabasen i Git må ikke bruges som skrivbar driftsdatabase.')
    if target.exists() or not source.is_file():return False
    temporary=_temporary(target)
    try:
        original=_connect_readonly(source)
        copy=sqlite3.connect(temporary)
        try:
            original.backup(copy)
            copy.execute('PRAGMA journal_mode=DELETE')
        finally:
            copy.close();original.close()
        try:os.link(temporary,target)
        except FileExistsError:return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description='Eksportér båd- og løbsdata uden loginoplysninger.')
    parser.add_argument('--source',required=True,help='Den eksisterende SQLite-driftsdatabase')
    parser.add_argument('--output',default=str(BUNDLED_SNAPSHOT),help='Kopi til versionsstyring')
    args=parser.parse_args()
    print(json.dumps(export_snapshot(args.source,args.output),ensure_ascii=False))


if __name__=='__main__':main()
