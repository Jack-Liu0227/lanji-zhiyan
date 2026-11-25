#!/usr/bin/env python3
"""Preview template duplicates; --apply merges proven equivalents with an audit archive."""
import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for name in ('database', 'workspace-core', 'workspace-service', 'connectors', 'ai-runtime'):
    sys.path.insert(0, str(ROOT / 'packages' / name))

import paths
import workflow
import workflow_template_catalog as catalog


def immutable_state(connection):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result = {}
    for table in ('workflow_executions', 'workflow_runs', 'workflow_nodes', 'project_task_runs',
                  'project_task_deliveries', 'project_task_output_revisions', 'artifacts', 'papers'):
        if table in tables:
            rows = sorted(json.dumps(list(row), ensure_ascii=False, default=str) for row in connection.execute('SELECT * FROM '+table))
            result[table] = {'count': len(rows), 'sha256': hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=paths.DB_PATH)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    database = args.database.resolve(strict=True)
    if args.report.resolve() == database:
        raise ValueError('The report path must not overwrite the database')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {'database': str(database), 'applied': args.apply}
    # Use a full SQLite backup before opening the app adapter or applying migrations.
    if args.apply:
        backup = args.report.with_suffix('.before.sqlite3')
        if backup.resolve() == database:
            raise ValueError('The backup path must differ from the database')
        if backup.exists():
            raise ValueError('Backup already exists; use a fresh report filename')
        with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        report['backup'] = str(backup)
    with ExitStack() as stack:
        if args.apply:
            db = stack.enter_context(workflow.WorkflowDB(database))
        else:
            connection = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)
            stack.callback(connection.close)
            connection.row_factory = sqlite3.Row
            db = SimpleNamespace(db=connection, _workflow_layout_initialized=True)
        report['before'] = immutable_state(db.db)
        report['preview'] = catalog.deduplicate(db)
        if args.apply:
            report['result'] = catalog.deduplicate(db, apply=True)
        report['after'] = immutable_state(db.db)
        report['history_unchanged'] = report['before'] == report['after']
        report['remaining_templates'] = db.db.execute('SELECT COUNT(*) FROM workflow_layouts').fetchone()[0]
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'removed': report.get('result', {}).get('removed', 0),
                      'groups': len(report['preview']['groups']), 'history_unchanged': report['history_unchanged'],
                      'remaining_templates': report['remaining_templates'], 'report': str(args.report)}))
    if not report['history_unchanged']:
        raise RuntimeError('Run history changed during maintenance; inspect the report before further work')


if __name__ == '__main__':
    main()
