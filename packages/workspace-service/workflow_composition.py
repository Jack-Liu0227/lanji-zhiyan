"""Freeze reusable subworkflows into one run with namespaced node identities."""
import project_workflows
import workflow_layout


def expand(db, project_id, layout, ancestors=()):
    if layout['id'] in ancestors or len(ancestors) >= 5:
        raise ValueError('子工作流存在递归引用或嵌套超过 5 层')
    definition = workflow_layout.validate_definition(layout)
    if not any(node.get('node_kind') == 'workflow' for node in definition['nodes']):
        return {**layout, **definition}
    from workflow_tasks import _executor
    nodes, edges, order, endpoints = [], [], [], {}
    by_id = {node['id']: node for node in definition['nodes']}
    for nid in definition['order']:
        node = by_id[nid]
        if node.get('node_kind') != 'workflow':
            nodes.append(node); order.append(nid); endpoints[nid] = ([nid], [nid])
            continue
        child = expand(db, project_id, project_workflows.current(db, project_id, node['workflow_template_id']), (*ancestors, layout['id']))
        names = {item['id']: nid + '.' + item['id'] for item in child['nodes']}
        entries = [item for item in child['order'] if not any(edge['target'] == item for edge in child['edges'])]
        exits = [item for item in child['order'] if not any(edge['source'] == item for edge in child['edges'])]
        for item in child['nodes']:
            executor, _ = _executor(db, project_id, child, item)
            allowed = item.get('tools')
            if node.get('tools') is not None:
                allowed = [name for name in node['tools'] if allowed is None or name in allowed]
            nodes.append({**item, 'id': names[item['id']], 'label': (node['label'] + ' / ' + item['label'])[:120],
                          'executor_binding_id': executor['binding_id'],
                          'member_id': executor.get('requested_member_id', ''),
                          'tools': allowed,
                          'review_required': item['review_required'] or (node['review_required'] and item['id'] in exits)})
        edges.extend({'source': names[edge['source']], 'target': names[edge['target']]} for edge in child['edges'])
        order.extend(names[item] for item in child['order'])
        endpoints[nid] = ([names[item] for item in entries], [names[item] for item in exits])
    for edge in definition['edges']:
        edges.extend({'source': source, 'target': target}
                     for source in endpoints[edge['source']][1] for target in endpoints[edge['target']][0])
    return {**layout, **workflow_layout.validate_definition({'nodes': nodes, 'edges': edges, 'order': order}),
            'composition': {'template_id': layout['id'], 'revision': layout.get('revision', 0)}}
