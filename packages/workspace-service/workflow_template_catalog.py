"""Business-equivalent template identities and explicit, audited maintenance."""
import hashlib
import itertools
import json
import math
import unicodedata
import uuid

import workflow_layout
from workflow import now


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def normalized_name(name):
    return ' '.join(unicodedata.normalize('NFKC', str(name)).split())


def canonical(layout):
    """Return a canonical definition and node mapping, preserving edge semantics.

    Exact labeling is bounded for highly symmetric graphs. Beyond that bound we
    retain node IDs, conservatively missing a duplicate instead of merging two
    definitions whose equivalence we have not proved.
    """
    definition = workflow_layout.validate_definition(layout)
    nodes = {node['id']: node for node in definition['nodes']}
    edges = definition['edges']
    semantic = {}
    for nid, node in nodes.items():
        value = {key: item for key, item in node.items() if key != 'id'}
        if value.get('executor_role_id') == nid:
            value['executor_role_id'] = '$self'
        for key in ('tools', 'skills'):
            if isinstance(value.get(key), list):
                value[key] = sorted(value[key])
        semantic[nid] = value
    colors = {nid: encoded(value) for nid, value in semantic.items()}
    for _ in nodes:
        signatures = {nid: encoded([colors[nid],
            sorted(([colors[e['source']], {k: v for k, v in e.items() if k not in ('source', 'target')}]
                   for e in edges if e['target'] == nid), key=encoded),
            sorted(([colors[e['target']], {k: v for k, v in e.items() if k not in ('source', 'target')}]
                   for e in edges if e['source'] == nid), key=encoded)]) for nid in nodes}
        ranks = {value: str(index) for index, value in enumerate(sorted(set(signatures.values())))}
        refined = {nid: ranks[value] for nid, value in signatures.items()}
        if len(set(refined.values())) == len(set(colors.values())):
            colors = refined
            break
        colors = refined
    groups = [sorted(nid for nid in nodes if colors[nid] == color) for color in sorted(set(colors.values()))]
    bounded = math.prod(math.factorial(len(group)) for group in groups) <= 4096
    candidates = itertools.product(*(itertools.permutations(group) for group in groups)) if bounded else [groups]
    best, best_mapping = None, None
    for grouping in candidates:
        order = [nid for group in grouping for nid in group]
        mapping = {nid: 'n' + str(index) for index, nid in enumerate(order)}
        value = {'nodes': [{**semantic[nid], 'id': mapping[nid]} for nid in order],
                 'edges': sorted([{**edge, 'source': mapping[edge['source']], 'target': mapping[edge['target']]}
                                  for edge in edges], key=encoded)}
        if not bounded:
            value['unresolved_symmetry_ids'] = order
        text = encoded(value)
        if best is None or text < best:
            best, best_mapping = text, mapping
    return hashlib.sha256(best.encode()).hexdigest(), best_mapping


def identity(layout):
    return normalized_name(layout['name']), canonical(layout)[0]


def _tables(db):
    return {row[0] for row in db.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def deliberately_deleted(db, wid):
    return bool(db.db.execute("SELECT 1 FROM workflow_template_archive WHERE template_id=? AND operation='delete' LIMIT 1", (wid,)).fetchone())


def references(db, wid):
    tables = _tables(db)
    result = {}
    for table, column in [('project_workflow_layouts', 'layout_id'),
                          ('project_workflow_definitions', 'layout_id'),
                          ('project_workflow_roles', 'template_id'), ('schedule_workflows', 'layout_id')]:
        result[table] = [dict(row) for row in db.db.execute(f'SELECT * FROM {table} WHERE {column}=?', (wid,))] if table in tables else []
    nested = []
    for row in db.db.execute('SELECT id,definition FROM workflow_layouts'):
        for node in json.loads(row['definition']).get('nodes', []):
            if node.get('workflow_template_id') == wid:
                nested.append({'template_id': row['id'], 'node_id': node['id']})
    if 'project_workflow_definitions' in tables:
        for row in db.db.execute('SELECT project_id,layout_id,definition FROM project_workflow_definitions'):
            for node in json.loads(row['definition']).get('nodes', []):
                if node.get('workflow_template_id') == wid:
                    nested.append({'project_id': row['project_id'], 'template_id': row['layout_id'], 'node_id': node['id']})
    result['subworkflows'] = nested
    result['research_setups'] = [dict(row) for row in db.db.execute(
        "SELECT * FROM research_setups WHERE json_extract(result,'$.layout_id')=?", (wid,))] if 'research_setups' in tables else []
    result['binding_origins'] = [dict(row) for row in db.db.execute(
        "SELECT * FROM project_binding_origins WHERE source='workflow_node' AND substr(source_id,1,?)=?",
        (len(wid)+1, wid+':'))] if 'project_binding_origins' in tables else []
    return result


def archive(db, operation, layout, refs, canonical_id=''):
    db.db.execute('INSERT INTO workflow_template_archive VALUES (?,?,?,?,?,?)',
                  (uuid.uuid4().hex, operation, layout['id'], canonical_id,
                   encoded({'layout': layout, 'references': refs}), now()))


def _remap_definition(definition, mapping):
    value = json.loads(json.dumps(definition))
    for node in value['nodes']:
        old = node['id']
        node['id'] = mapping.get(old, old)
        if node.get('executor_role_id') == old:
            node['executor_role_id'] = node['id']
    for edge in value['edges']:
        for key in ('source', 'target'):
            edge[key] = mapping.get(edge[key], edge[key])
    value['order'] = [mapping.get(nid, nid) for nid in value['order']]
    return value


def _merge(db, old, keep):
    refs = references(db, old['id'])
    old_map, keep_map = canonical(old)[1], canonical(keep)[1]
    reverse = {value: key for key, value in keep_map.items()}
    mapping = {nid: reverse[value] for nid, value in old_map.items()}
    # Overrides can change/remove node identities. Preserve such groups for review.
    for row in refs['project_workflow_definitions']:
        definition = json.loads(row['definition'])
        if {n['id'] for n in definition['nodes']} != set(mapping):
            raise ValueError('项目覆盖包含不同节点，需人工核对')
        existing = db.db.execute('SELECT * FROM project_workflow_definitions WHERE project_id=? AND layout_id=?',
                                 (row['project_id'], keep['id'])).fetchone()
        if existing and encoded(json.loads(existing['definition'])) != encoded(_remap_definition(definition, mapping)):
            raise ValueError('项目覆盖配置冲突，已保留两份模板')
    roles = {}
    keep_nodes = {n['id']: n for n in workflow_layout.validate_definition(keep)['nodes']}
    for node in workflow_layout.validate_definition(old)['nodes']:
        roles[node['executor_role_id']] = keep_nodes[mapping[node['id']]]['executor_role_id']
    for row in refs['project_workflow_roles']:
        role = roles.get(row['role_id'], row['role_id'])
        existing = db.db.execute('SELECT * FROM project_workflow_roles WHERE project_id=? AND template_id=? AND role_id=?',
                                (row['project_id'], keep['id'], role)).fetchone()
        if existing and any(existing[key] != row[key] for key in ('binding_id', 'member_id')):
            raise ValueError('项目角色绑定冲突，已保留两份模板')
    archive(db, 'deduplicate', old, refs, keep['id'])
    for row in refs['project_workflow_definitions']:
        value = _remap_definition(json.loads(row['definition']), mapping)
        db.db.execute('INSERT OR IGNORE INTO project_workflow_definitions VALUES (?,?,?,?,?,?)',
                      (row['project_id'], keep['id'], row['revision'], encoded(value), row['base_fingerprint'], row['updated_at']))
    for row in refs['project_workflow_roles']:
        db.db.execute('INSERT OR IGNORE INTO project_workflow_roles VALUES (?,?,?,?,?,?,?,?)',
                      (row['project_id'], keep['id'], roles.get(row['role_id'], row['role_id']), row['binding_id'],
                       row['member_id'], row['revision'], row['source'], row['updated_at']))
    for row in refs['project_workflow_layouts']:
        db.db.execute('UPDATE project_workflow_layouts SET layout_id=?,selected_node=? WHERE task_id=?',
                      (keep['id'], mapping.get(row['selected_node'], ''), row['task_id']))
    if 'schedule_workflows' in _tables(db):
        db.db.execute('UPDATE schedule_workflows SET layout_id=? WHERE layout_id=?', (keep['id'], old['id']))
    for row in refs['research_setups']:
        result = json.loads(row['result']); result['layout_id'] = keep['id']
        db.db.execute('UPDATE research_setups SET result=? WHERE request_key=?', (encoded(result), row['request_key']))
    for row in refs['binding_origins']:
        nid = row['source_id'][len(old['id'])+1:]
        replacement = keep['id'] + ':' + mapping.get(nid, nid)
        db.db.execute('INSERT OR IGNORE INTO project_binding_origins VALUES (?,?,?,?)',
                      (row['binding_id'], row['source'], replacement, row['created_at']))
        db.db.execute('DELETE FROM project_binding_origins WHERE binding_id=? AND source=? AND source_id=?',
                      (row['binding_id'], row['source'], row['source_id']))
    for table, keys in [('workflow_layouts', ['id']), ('project_workflow_definitions', ['project_id', 'layout_id'])]:
        if table not in _tables(db):
            continue
        for row in db.db.execute(f'SELECT * FROM {table}').fetchall():
            definition = json.loads(row['definition'])
            changed = False
            for node in definition.get('nodes', []):
                if node.get('workflow_template_id') == old['id']:
                    node['workflow_template_id'] = keep['id']; changed = True
            if changed:
                archive(db, 'reference_redirect', {'id': row[keys[-1]], 'definition': json.loads(row['definition'])}, dict(row), keep['id'])
                db.db.execute(f"UPDATE {table} SET definition=? WHERE " + ' AND '.join(key+'=?' for key in keys),
                              (encoded(definition), *(row[key] for key in keys)))
    db.db.execute('DELETE FROM project_workflow_roles WHERE template_id=?', (old['id'],))
    db.db.execute('DELETE FROM workflow_layouts WHERE id=?', (old['id'],))


def deduplicate(db, *, apply=False):
    """Explicit maintenance only. Each group is atomic and conflicts are retained."""
    import project_tasks
    workflow_layout.ensure_schema(db)
    groups, invalid = {}, []
    for layout in workflow_layout.list_layouts(db)['layouts']:
        try:
            groups.setdefault(identity(layout), []).append(layout)
        except (ValueError, KeyError, TypeError) as exc:
            invalid.append({'id': layout['id'], 'reason': str(exc)})
    report = {'groups': [], 'invalid': invalid, 'removed': 0, 'applied': apply}
    for (name, fingerprint), layouts in groups.items():
        if len(layouts) < 2:
            continue
        layouts.sort(key=lambda item: (item['updated_at'], item['id']), reverse=True)
        keep, *duplicates = layouts
        item = {'name': name, 'fingerprint': fingerprint, 'canonical_id': keep['id'],
                'duplicates': [{'id': old['id'], 'references': references(db, old['id'])} for old in duplicates]}
        report['groups'].append(item)
        if apply:
            try:
                with project_tasks._atomic(db):
                    for old in duplicates:
                        current = workflow_layout._layout(db, old['id'])
                        canonical_layout = workflow_layout._layout(db, keep['id'])
                        if identity(current) != identity(canonical_layout) or current['updated_at'] != old['updated_at']:
                            raise ValueError('预览后模板发生变化，已跳过')
                        _merge(db, current, canonical_layout)
                report['removed'] += len(duplicates)
            except (ValueError, KeyError) as exc:
                item['skipped_reason'] = str(exc)
    return report
