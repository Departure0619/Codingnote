"""Versioned additive Alpha migration with verified SQLite backups and copy rehearsal.

CLI rollback restores to a new path. It never overwrites a learner database.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import uuid


SCHEMA_V2 = '''
CREATE TABLE task_profiles (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), user_id TEXT NOT NULL);
INSERT INTO task_profiles SELECT id,'local' FROM tasks;
CREATE TABLE task_sources (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), source_platform TEXT NOT NULL DEFAULT '',
 original_id TEXT NOT NULL DEFAULT '', source_kind TEXT NOT NULL DEFAULT 'manual',
 source_description TEXT NOT NULL DEFAULT '', license TEXT NOT NULL DEFAULT '{}',
 bank_id TEXT NOT NULL DEFAULT '', question_id TEXT NOT NULL DEFAULT '',
 content_version TEXT NOT NULL DEFAULT '', content_digest TEXT NOT NULL DEFAULT '',
 knowledge_primary TEXT NOT NULL DEFAULT '', knowledge_tags TEXT NOT NULL DEFAULT '[]',
 difficulty TEXT NOT NULL DEFAULT '', import_status TEXT NOT NULL DEFAULT 'manual');
CREATE TABLE error_records (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 mistake_id TEXT REFERENCES mistakes(id), version_id TEXT NOT NULL REFERENCES versions(id),
 evidence_id TEXT REFERENCES evidence(id), observed TEXT NOT NULL, category TEXT NOT NULL,
 reason TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('ai_candidate','user_confirmed','user_modified','uncertain')),
 revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE error_revisions (
 id TEXT PRIMARY KEY, error_id TEXT NOT NULL REFERENCES error_records(id), revision INTEGER NOT NULL,
 version_id TEXT NOT NULL REFERENCES versions(id), evidence_id TEXT REFERENCES evidence(id),
 observed TEXT NOT NULL, category TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(error_id,revision));
CREATE TABLE associations (
 id TEXT PRIMARY KEY, error_a_id TEXT NOT NULL REFERENCES error_records(id),
 error_b_id TEXT NOT NULL REFERENCES error_records(id), reason TEXT NOT NULL, origin TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','confirmed','rejected','ignored','revoked')),
 basis_digest TEXT NOT NULL, basis_snapshot TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, CHECK(error_a_id<>error_b_id));
CREATE TABLE association_history (
 id TEXT PRIMARY KEY, association_id TEXT NOT NULL REFERENCES associations(id),
 from_status TEXT, to_status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE learning_preferences (
 user_id TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '', value TEXT NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(user_id,task_id));
CREATE INDEX error_task_time ON error_records(task_id,created_at);
CREATE INDEX error_reason ON error_records(status,reason);
CREATE INDEX source_lookup ON task_sources(source_platform,original_id);
CREATE INDEX association_a ON associations(error_a_id);
CREATE INDEX association_b ON associations(error_b_id);
'''

SCHEMA_V3_SNAPSHOTS = '''
CREATE TABLE IF NOT EXISTS task_revisions (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), revision INTEGER NOT NULL,
 title TEXT NOT NULL, statement TEXT NOT NULL, source_url TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(task_id,revision));
CREATE TABLE IF NOT EXISTS source_revisions (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), revision INTEGER NOT NULL,
 source TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(task_id,revision));
'''


def inspect_database(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        integrity = [r[0] for r in db.execute('PRAGMA integrity_check')]
        foreign_keys = list(db.execute('PRAGMA foreign_key_check'))
        if integrity != ['ok'] or foreign_keys:
            raise ValueError('数据库完整性或外键检查未通过，未执行迁移。')
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {'version': db.execute('PRAGMA user_version').fetchone()[0], 'integrity': integrity,
                'foreign_keys': foreign_keys,
                'counts': {t: db.execute('SELECT COUNT(*) FROM "' + t.replace('"', '""') + '"').fetchone()[0] for t in tables}}


def require_complete_database(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        marker = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='export_metadata'").fetchone()
        if marker:
            values = dict(db.execute('SELECT key,value FROM export_metadata'))
            if values.get('kind') == 'learner_export' or values.get('complete_snapshot') == 'false':
                raise ValueError('这是脱敏学习记录导出，不是完整备份，不能作为正式学习数据库启动或恢复。请使用离线完整备份。')


def _apply(path):
    from .bank import BANK_SCHEMA
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        with db:
            db.execute('BEGIN IMMEDIATE')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            sql = (SCHEMA_V2 if version < 2 else '') + (BANK_SCHEMA + SCHEMA_V3_SNAPSHOTS if version < 3 else '')
            for statement in sql.split(';'):
                if statement.strip():
                    db.execute(statement)
            if version < 3:
                for task in db.execute('SELECT * FROM tasks').fetchall():
                    db.execute('INSERT OR IGNORE INTO task_revisions VALUES(?,?,?,?,?,?,?)',
                        (uuid.uuid4().hex, task['id'], task['revision'], task['title'], task['statement'], task['source_url'], task['updated_at']))
                for source in db.execute('SELECT * FROM task_sources').fetchall():
                    snapshot = dict(source)
                    for key in ('license', 'knowledge_tags'):
                        snapshot[key] = json.loads(snapshot[key])
                    db.execute('INSERT OR IGNORE INTO source_revisions VALUES(?,?,?,?,?)',
                        (uuid.uuid4().hex, source['task_id'], 1, json.dumps(snapshot, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
            db.execute('PRAGMA user_version=3')
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or list(db.execute('PRAGMA foreign_key_check')):
                raise ValueError('迁移后完整性检查失败，事务已回滚。')


def upgrade(path, backup_required=True):
    path = Path(path).resolve()
    require_complete_database(path)
    before = inspect_database(path)
    if before['version'] == 3:
        return {'version': before['version'], 'changed': False, 'backup': None}
    if before['version'] not in {1, 2}:
        raise ValueError('仅支持已识别的 Alpha schema v1/v2，未修改数据库。')
    backup = None
    if backup_required:
        folder = path.parent / 'migration_backups'
        folder.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = folder / ('before-v' + str(before['version']) + '-to-v3-' + stamp + '-' + uuid.uuid4().hex[:8] + '.sqlite3')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
        snapshot = inspect_database(backup)
        if snapshot != before:
            raise ValueError('迁移备份与源数据库结构/计数不一致，未迁移。')
        manifest = {'format': 'alpha-migration-backup-v1', 'source': str(path), 'target_version': 3,
                    'sha256': hashlib.sha256(backup.read_bytes()).hexdigest(), 'database': snapshot}
        backup.with_suffix('.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf8')
        # Exercise migration and rollback from the verified backup before changing the source.
        with tempfile.TemporaryDirectory(prefix='alpha-migration-rehearsal-') as temporary:
            trial = Path(temporary) / 'trial.sqlite3'
            restored = Path(temporary) / 'rollback.sqlite3'
            restore_backup(backup, trial)
            _apply(trial)
            after_trial = inspect_database(trial)
            if any(after_trial['counts'].get(t) != count for t, count in before['counts'].items()):
                raise ValueError('测试副本旧记录计数发生变化，未迁移。')
            restore_backup(backup, restored)
            if inspect_database(restored) != before:
                raise ValueError('回滚演练未通过，未迁移。')
    _apply(path)
    after = inspect_database(path)
    return {'version': after['version'], 'changed': True, 'backup': str(backup) if backup else None,
            'rehearsed': bool(backup), 'before': before, 'after': after}


def restore_backup(backup, destination):
    backup, destination = Path(backup).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError('恢复目标必须不存在；不会覆盖或合并已有数据。')
    manifest = json.loads(backup.with_suffix('.json').read_text('utf8'))
    if manifest.get('format') != 'alpha-migration-backup-v1' or manifest.get('sha256') != hashlib.sha256(backup.read_bytes()).hexdigest():
        raise ValueError('备份摘要无效，未恢复。')
    require_complete_database(backup)
    before = inspect_database(backup)
    if before != manifest['database']:
        raise ValueError('备份结构校验失败，未恢复。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(backup.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
    if inspect_database(destination) != before:
        raise ValueError('恢复校验失败，请保留备份与失败副本。')
    return {'destination': str(destination), 'version': before['version'], 'verified': True}


def backup_database(database, destination):
    """Complete administrative snapshot for offline CLI; no redaction or app startup."""
    database, destination = Path(database).resolve(), Path(destination).resolve()
    manifest_path = destination.with_suffix('.json')
    if destination.suffix.lower() == '.json':
        raise ValueError('完整备份请使用 .sqlite3 或其他非 .json 文件名，避免与摘要文件冲突。')
    if destination.exists() or manifest_path.exists():
        raise ValueError('完整备份目标及摘要文件必须不存在；不会覆盖已有文件。')
    require_complete_database(database)
    before = inspect_database(database)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
    snapshot = inspect_database(destination)
    if snapshot != before:
        raise ValueError('完整备份校验失败，请保留失败副本进行检查。')
    manifest = {'format': 'alpha-migration-backup-v1', 'source': str(database),
                'kind': 'complete_offline_snapshot', 'complete_snapshot': True,
                'target_version': snapshot['version'],
                'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(), 'database': snapshot}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf8')
    return {'backup': str(destination), 'manifest': str(manifest_path), 'sha256': manifest['sha256'],
            'version': snapshot['version'], 'complete_snapshot': True}


def main():
    parser = argparse.ArgumentParser(description='Alpha 增量迁移与安全恢复（先停止目标数据目录服务）')
    sub = parser.add_subparsers(dest='command', required=True)
    migrate = sub.add_parser('upgrade')
    migrate.add_argument('--database', required=True, type=Path)
    restore = sub.add_parser('rollback')
    restore.add_argument('--backup', required=True, type=Path)
    restore.add_argument('--restore-to', required=True, type=Path)
    backup = sub.add_parser('backup')
    backup.add_argument('--database', required=True, type=Path)
    backup.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    # Use the same directory lock as the official service, without importing app/providers.
    from .locking import lock_data
    lock_root = args.database.resolve().parent if args.command in {'upgrade', 'backup'} else args.restore_to.resolve().parent
    handle = lock_data(lock_root)
    try:
        if args.command == 'upgrade':
            result = upgrade(args.database)
        elif args.command == 'backup':
            result = backup_database(args.database, args.output)
        else:
            result = restore_backup(args.backup, args.restore_to)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        handle.close()


if __name__ == '__main__':
    main()
