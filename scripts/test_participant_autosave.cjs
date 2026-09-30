// Run with: node scripts/test_participant_autosave.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const template = fs.readFileSync('templates/admin_dash/profiles_participants_track_sheet.html', 'utf8');
const source = template.slice(template.lastIndexOf('<script>') + 8, template.lastIndexOf('</script>'))
  .replace(/{{ combined_sheet[^}]+}}/g, 'false')
  .replace(/{{ linked_google_sheet[^}]+}}/g, 'false')
  .replace(/{{ group.number }}/g, '1')
  .replace(/{{ track_slug[^}]*}}/g, 'mentoras');
function browser(response) {
  let matrix = [['Status'], ['Activa']];
  let tick;
  let requests = 0;
  const listeners = {};
  const storage = new Map();
  const revision = {value: 'original'};
  const status = {textContent: ''};
  const elements = {
    'ce-sheet-save-form': {querySelector: () => revision},
    'ce-sheet-data': {value: ''},
    'ce-sheet-autosave-status': status,
  };
  const context = {
    document: {getElementById: id => elements[id] || null, querySelectorAll: () => [], addEventListener: () => {}},
    window: {
      location: {pathname: '/group/1/mentoras/', href: 'https://example.test/group/1/mentoras/'},
      __ceSheetRegistry: {'participants-track-sheet-xsheet': {getMatrix: () => matrix}},
      setInterval: cb => {tick = cb;}, setTimeout: () => {},
      addEventListener: (name, cb) => {listeners[name] = cb;},
    },
    localStorage: {setItem: (key, value) => storage.set(key, value), getItem: key => storage.get(key)},
    FormData: class {},
    fetch: async () => { requests++; return response; },
    Blob, URL, Date, JSON,
  };
  vm.runInNewContext(source, context);
  return {status, revision, storage, listeners, edit: () => {matrix = [['Status'], ['Graduada']];},
    tick: async () => {tick(); await new Promise(resolve => setImmediate(resolve));}, requests: () => requests};
}
function reply({ok = true, redirected = false, status = 200, type = 'application/json', body = {ok: true, revision: 'new'}} = {}) {
  return {ok, redirected, status, headers: {get: () => type}, json: async () => body};
}
(async () => {
  for (const response of [reply({redirected: true, type: 'text/html'}), reply({body: {ok: false}}), reply({body: {ok: true}})]) {
    const b = browser(response); b.edit(); await b.tick();
    assert.ok(!b.status.textContent.startsWith('Autosaved'));
    assert.equal(b.revision.value, 'original');
    assert.match([...b.storage.values()][0], /Graduada/);
  }
  const conflict = browser(reply({ok: false, status: 409, body: {ok: false, error: 'Outdated tab'}}));
  conflict.edit(); await conflict.tick(); await conflict.tick();
  assert.equal(conflict.requests(), 1);
  assert.equal(conflict.status.textContent, 'Outdated tab');
  let warned = false;
  conflict.listeners.beforeunload({preventDefault: () => {warned = true;}});
  assert.ok(warned);
  const saved = browser(reply()); saved.edit(); await saved.tick(); await saved.tick();
  assert.equal(saved.requests(), 1);
  assert.equal(saved.revision.value, 'new');
  assert.match(saved.status.textContent, /^Autosaved/);
  warned = false;
  saved.listeners.beforeunload({preventDefault: () => {warned = true;}});
  assert.equal(warned, false);
  console.log('Autosave tests passed: confirmation, login redirects, conflicts, backups and unload warning.');
})().catch(error => {console.error(error); process.exitCode = 1;});
