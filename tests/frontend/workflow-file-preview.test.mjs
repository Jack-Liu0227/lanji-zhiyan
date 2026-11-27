import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../apps/desktop/web/views/workflow-file-preview.js', import.meta.url), 'utf8');

// A DOM boundary double: rendering stays observable as HTML; listeners run as in the host.
class Element {
  constructor() {
    this.innerHTML = '';
    this.textContent = '';
    this.isConnected = true;
    this.parentNode = null;
    this.attributes = new Map();
    this.parts = new Map();
    this.listeners = new Map();
    this.classes = new Set();
    this.classList = {add: value => this.classes.add(value), remove: value => this.classes.delete(value)};
  }
  setAttribute(key, value) { this.attributes.set(key, value); }
  getAttribute(key) { return this.attributes.get(key); }
  replaceChildren(child) {
    if (this.child) this.child.parentNode = null;
    this.child = child;
    child.parentNode = this;
  }
  querySelector(selector) {
    const key = selector.match(/^\[data-wfp-([a-z]+)\]$/)?.[1];
    assert.ok(key && this.innerHTML.includes(`data-wfp-${key}`), `Missing preview element ${selector}`);
    if (!this.parts.has(key)) {
      const child = new Element();
      child.parentNode = this;
      this.parts.set(key, child);
    }
    return this.parts.get(key);
  }
  contains(child) {
    for (let parent = child; parent; parent = parent.parentNode) if (parent === this) return true;
    return false;
  }
  addEventListener(type, handler) { this.listeners.set(type, handler); }
  removeEventListener(type, handler) { if (this.listeners.get(type) === handler) this.listeners.delete(type); }
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function fixture() {
  const pending = [];
  const markdown = [];
  const context = vm.createContext({
    window: {}, URLSearchParams, AbortController,
    document: {createElement: () => new Element()},
    api: (url, options) => {
      const response = deferred();
      pending.push({url, options, ...response});
      return response.promise;
    },
    renderMarkdown: text => { markdown.push(text); return '<p>Rendered Markdown</p>'; }
  });
  vm.runInContext(source, context);
  const host = new Element();
  const part = name => host.child.querySelector(`[data-wfp-${name}]`);
  return {module: context.window.WorkflowFilePreview, host, part, pending, markdown};
}

const mdPreview = (text = '# Read only', source = {kind: 'project', relative: 'notes/chapter/report.md'}) => ({
  preview: {kind: 'markdown', name: 'report.md', text, offset: 0, limit: 100, truncated: false, total_rows: 0, source}
});
const json = value => JSON.parse(JSON.stringify(value));

test('reference returns file objects and unwraps the reserved Markdown href', () => {
  const {module} = fixture();
  for (const [href, expected] of [
    ['notes/report.md', {relative: 'notes/report.md'}],
    ['../数据%20集.CSV#row-2', {relative: '../数据 集.CSV'}],
    ['/资料/来源.md', {relative: '/资料/来源.md'}],
    ['C:\\Project\\研究.csv', {path: 'C:\\Project\\研究.csv'}],
    ['D:/Project/notes.md', {path: 'D:/Project/notes.md'}],
    ['./report.markdown', {relative: './report.markdown'}],
    ['/__workflow-file?path=' + encodeURIComponent('../结果/中文.csv'), {relative: '../结果/中文.csv'}],
    ['/__workflow-file?path=' + encodeURIComponent('D:\\项目\\研究.md'), {path: 'D:\\项目\\研究.md'}]
  ]) assert.deepEqual(json(module.reference(href)), expected, href);
});

test('external links, protocols, malformed wrappers and non-preview files stay external', () => {
  const {module} = fixture();
  for (const href of [
    'http://example.org/report.md', 'https://example.org/data.csv', '//example.org/file.md',
    'mailto:report.md', 'javascript:alert("x.csv")', 'data:text/csv,one.csv', 'file:///C:/report.md',
    '#report.md', 'image.png', '/%00file.md', '%68%74%74%70%73%3A%2F%2Fexample.org/data.csv',
    '%20https%3A%2F%2Fexample.org/data.csv',
    '/__workflow-file?path=' + encodeURIComponent('https://example.org/data.csv'),
    '/__workflow-file?path=one.md&path=two.csv', '/__workflow-file?missing=report.md',
    '/__workflow-file?path=%2F%2Fexample.org%2Fdata.csv', '%invalid.csv', null
  ]) assert.equal(module.reference(href), null, String(href));
});

test('mount exposes host disposal immediately, sends file.path and revision zero, and reuses Markdown', async () => {
  const {module, host, part, pending, markdown} = fixture();
  const file = {task_id: 'task-1', task_run_id: 'run-1', output_revision: 0, path: 'D:/项目/report.md', name: 'report.md'};
  const mounting = module.mount(host, {projectId: 'p', chatId: 'c', file});
  assert.equal(typeof host.__previewDispose, 'function');
  assert.match(part('status').textContent, /正在读取/);
  assert.equal(host.child.getAttribute('aria-busy'), 'true');
  const query = new URL(pending[0].url, 'http://localhost').searchParams;
  assert.equal(query.get('path'), file.path);
  assert.equal(query.get('task_id'), 'task-1');
  assert.equal(query.get('task_run_id'), 'run-1');
  assert.equal(query.get('output_revision'), '0');
  assert.equal(query.get('chat_id'), 'c');
  assert.equal(pending[0].options.method, 'GET');
  file.path = 'mutated.md';
  pending[0].resolve(mdPreview('# Pinned Markdown', {kind: 'node', task_id: 'task-1', task_run_id: 'run-1', output_revision: 0, artifact_id: 'file:1'}));
  const view = await mounting;
  assert.deepEqual(markdown, ['# Pinned Markdown']);
  assert.match(part('body').innerHTML, /Rendered Markdown/);
  assert.match(part('source').textContent, /修订 v0.*file:1/);
  assert.equal(host.child.getAttribute('aria-busy'), 'false');
  assert.equal(part('pager').hidden, true);
  view.destroy();
  assert.equal(host.__previewDispose, undefined);
  assert.equal(pending[0].options.signal.aborted, true);
});

test('table shows every column, escapes cells, and requests successive pages', async () => {
  const {module, host, part, pending} = fixture();
  const columns = Array.from({length: 14}, (_, i) => `col${i}`);
  const mounting = module.mount(host, {projectId: 'p', chatId: 'c', file: {relative: 'table.csv'}});
  pending[0].resolve({preview: {kind: 'table', name: 'table.csv', columns,
    rows: [['<script>unsafe</script>', ...columns.slice(1)]], offset: 0, limit: 100, total_rows: 101,
    truncated: true, source: {kind: 'project', relative: 'table.csv'}}});
  await mounting;
  assert.equal((part('body').innerHTML.match(/scope="col"/g) || []).length, 14);
  assert.match(part('body').innerHTML, /col13/);
  assert.match(part('body').innerHTML, /&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.equal(part('pager').hidden, false);
  assert.equal(part('prev').disabled, true);
  assert.equal(part('next').disabled, false);
  const next = part('next').onclick();
  assert.equal(new URL(pending[1].url, 'http://localhost').searchParams.get('offset'), '100');
  pending[1].resolve({preview: {kind: 'table', name: 'table.csv', columns, rows: [columns],
    offset: 100, limit: 100, total_rows: 101, source: {kind: 'project'}}});
  await next;
  assert.equal(part('page').textContent, '101–101 / 101 行');
  assert.equal(part('prev').disabled, false);
  assert.equal(part('next').disabled, true);
  const previous = part('prev').onclick();
  assert.equal(new URL(pending[2].url, 'http://localhost').searchParams.get('offset'), '0');
  host.__previewDispose();
  pending[2].resolve(mdPreview('disposed'));
  await previous;
});

test('older responses, disposal and external live changes cannot replace current content', async () => {
  const {module, host, part, pending, markdown} = fixture();
  const first = module.mount(host, {projectId: 'p', chatId: 'c', file: {relative: 'old.md'}});
  const firstDispose = host.__previewDispose;
  const second = module.mount(host, {projectId: 'p', chatId: 'c', file: {relative: 'new.md'}});
  assert.equal(pending[0].options.signal.aborted, true);
  pending[1].resolve(mdPreview('new content'));
  await second;
  pending[0].resolve(mdPreview('stale content'));
  await first;
  firstDispose();
  assert.equal(typeof host.__previewDispose, 'function');
  assert.deepEqual(markdown, ['new content']);
  let active = true;
  const third = module.mount(host, {projectId: 'p', chatId: 'c', file: {relative: 'away.md'}, live: () => active});
  active = false;
  pending[2].resolve(mdPreview('no longer active'));
  await third;
  assert.deepEqual(markdown, ['new content']);
  assert.equal(part('body').innerHTML, '');
  host.__previewDispose();
});

test('pagination responses respect their request order even when abort is ignored', async () => {
  const {module, host, part, pending, markdown} = fixture();
  const mounting = module.mount(host, {projectId: 'p', chatId: 'c', file: {relative: 'report.md'}});
  pending[0].resolve(mdPreview('initial'));
  const view = await mounting;
  const older = view.reload();
  const newer = view.reload();
  pending[2].resolve(mdPreview('newest'));
  await newer;
  pending[1].resolve(mdPreview('older'));
  await older;
  assert.deepEqual(markdown, ['initial', 'newest']);
  assert.equal(part('status').textContent, '');
  host.__previewDispose();
});

test('preview errors are visible, reload clears the error, and detached hosts ignore failures', async () => {
  const {module, host, part, pending} = fixture();
  const mounting = module.mount(host, {projectId: 'p', chatId: 'c', file: {relative: 'missing.md'}});
  pending[0].reject(new Error('预览文件不存在'));
  const view = await mounting;
  assert.match(part('status').textContent, /预览失败.*不存在/);
  assert.equal(part('status').getAttribute('role'), 'alert');
  const retry = view.reload();
  pending[1].resolve(mdPreview('recovered'));
  await retry;
  assert.equal(part('status').textContent, '');
  assert.equal(part('status').classes.has('wfp-error'), false);
  const detached = view.reload();
  host.isConnected = false;
  pending[2].reject(new Error('late error'));
  await detached;
  assert.doesNotMatch(part('status').textContent, /late error/);
  host.__previewDispose();
});

test('Markdown links resolve from the source directory and stop root delegation', async () => {
  const {module, host, part, pending} = fixture();
  const opened = [];
  const mounting = module.mount(host, {projectId: 'p', chatId: 'c', file: {relative: '.tasks/revisions/report.md'},
    openFile: file => opened.push(json(file))});
  pending[0].resolve(mdPreview('links', {kind: 'node', relative: '.tasks/revisions/report.md', base_relative: 'notes/chapter/report.md'}));
  await mounting;
  for (const [href, expected] of [
    ['../数据.csv', {relative: 'notes/数据.csv'}],
    ['/资料/ref.md', {relative: '/资料/ref.md'}],
    ['/__workflow-file?path=' + encodeURIComponent('./supplement.csv'), {relative: 'notes/chapter/supplement.csv'}],
    ['C:\\Project\\other.md', {path: 'C:\\Project\\other.md'}]
  ]) {
    const anchor = new Element();
    anchor.parentNode = part('body');
    anchor.setAttribute('href', href);
    anchor.closest = () => anchor;
    let prevented = false, stopped = false;
    await host.child.listeners.get('click')({target: anchor, preventDefault: () => { prevented = true; }, stopPropagation: () => { stopped = true; }});
    assert.equal(prevented, true);
    assert.equal(stopped, true);
    assert.deepEqual(opened.at(-1), expected);
  }
  const anchor = new Element();
  anchor.parentNode = part('body');
  anchor.closest = () => anchor;
  anchor.setAttribute('href', 'https://example.org/outside.md');
  await host.child.listeners.get('click')({target: anchor, preventDefault: () => assert.fail('External navigation was intercepted')});
  assert.equal(opened.length, 4);
  host.__previewDispose();
});
