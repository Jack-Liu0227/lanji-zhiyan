import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../apps/desktop/web/views/notes-and-settings.js', import.meta.url), 'utf8');
const importFunction = source.match(/async function mdImport\(files\)\{[\s\S]*?\n\}/)?.[0];
assert.ok(importFunction, 'mdImport function is present');

function fixture({project = false} = {}) {
  const calls = [];
  const notifications = [];
  const saves = [];
  const state = {textContent: ''};
  const context = vm.createContext({
    mdDashReadOnly: false,
    uiRouteIs: () => false,
    route: () => 'notes',
    mdProj: project ? {folder: 'project-notes'} : null,
    spTab: project ? 'notes' : '',
    mdDashSelectedFolder: 'selected-folder',
    mdDashProject: 'project-1',
    mdDashLoad: async () => {},
    $: id => id === 'mdSaveState' ? state : null,
    api: async (url, body) => { calls.push({url, body}); return {}; },
    mdNew: async (...args) => { saves.push(args); },
    uiNotify: message => notifications.push(message),
    window: {}
  });
  vm.runInContext(importFunction, context);
  return {importMarkdown: context.mdImport, calls, notifications, saves, state};
}

const largeMarkdown = '# Large note\n\n' + 'x'.repeat(5 * 1024 * 1024 + 32) + '\nTAIL_MARKER_COMPLETE';
const file = (name, text) => ({name, size: 5 * 1024 * 1024 + 1, text: async () => text});

test('project-note import sends the complete Markdown body above 5 MiB', async () => {
  const {importMarkdown, calls} = fixture({project: true});
  await importMarkdown([file('project.md', largeMarkdown)]);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/project/note/save');
  assert.equal(calls[0].body.content.length, largeMarkdown.length);
  assert.ok(calls[0].body.content.endsWith('TAIL_MARKER_COMPLETE'));
});

test('global-note import sends the complete Markdown body above 5 MiB', async () => {
  const {importMarkdown, saves} = fixture();
  await importMarkdown([file('global.md', largeMarkdown)]);
  assert.equal(saves.length, 1);
  assert.equal(saves[0][0], 'global');
  assert.equal(saves[0][1].length, largeMarkdown.length);
  assert.ok(saves[0][1].endsWith('TAIL_MARKER_COMPLETE'));
});

test('project-note import reports file read errors', async () => {
  const {importMarkdown, notifications, calls} = fixture({project: true});
  await importMarkdown([{name: 'broken.md', size: 1, text: async () => { throw new Error('disk read failed'); }}]);
  assert.equal(calls.length, 0);
  assert.match(notifications.join('\n'), /broken\.md.*disk read failed/);
});

test('global-note import reports file read errors', async () => {
  const {importMarkdown, notifications, saves} = fixture();
  await importMarkdown([{name: 'broken.md', size: 1, text: async () => { throw new Error('disk read failed'); }}]);
  assert.equal(saves.length, 0);
  assert.match(notifications.join('\n'), /broken\.md.*disk read failed/);
});

for (const project of [true, false]) {
  test(`${project ? 'project' : 'global'}-note import remains limited to the first 50 Markdown files`, async () => {
    const {importMarkdown, calls, saves} = fixture({project});
    const files = Array.from({length: 51}, (_, index) => file(`note-${index}.md`, `# Note ${index}`));
    await importMarkdown(files);
    const titles = project ? calls.map(call => call.body.title) : saves.map(args => args[0]);
    assert.equal(titles.length, 50);
    assert.equal(titles[0], 'note-0');
    assert.equal(titles.at(-1), 'note-49');
    assert.ok(!titles.includes('note-50'));
  });
}
