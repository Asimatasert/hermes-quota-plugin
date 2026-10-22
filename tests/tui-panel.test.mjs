import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';

const source = new URL('../tui/quota-panel.mjs', import.meta.url);
test('the TUI panel is shipped as a standalone widget', () => {
  assert.ok(existsSync(source), 'missing standalone quota widget');
});

test('renders remaining quota, preserves nested accounts and filters by provider', async () => {
  const { panelModel } = await import(source);
  const now = Date.parse('2026-10-22T12:00:00Z');
  const cache = { fetched_at: '2026-10-22T11:59:00Z', providers: {
    'openai-codex': { accounts: [{ label: 'Personal', windows: [
      { label: 'Session', used_percent: 25, reset_at: '2026-10-22T13:00:00Z' }
    ] }] },
    'anthropic:work': { label: 'Work', windows: [{ label: 'Weekly', used_percent: 90 }] }
  } };
  const model = panelModel(cache, ['openai-codex'], now);
  assert.equal(model.rows.length, 1);
  assert.match(model.rows[0].text, /Personal/);
  assert.match(model.rows[0].text, /75% left/);
  assert.match(model.rows[0].text, /1h/);
  assert.equal(model.stale, false);
});

test('unknown values, errors, stale data and balances are never fabricated percentages', async () => {
  const { panelModel } = await import(source);
  const model = panelModel({ providers: {
    broken: { unavailable_reason: 'expired token', windows: [{ used_percent: 0 }] },
    unknown: { windows: [{ label: 'Monthly', used_percent: null }] },
    balance: { account_balances: [{ currency: 'USD', total_balance: '3.50' }] },
    bad: null
  } });
  assert.equal(model.stale, true);
  assert.equal(model.rows.length, 3);
  assert.match(model.rows[0].text, /unavailable/);
  assert.doesNotMatch(model.rows[0].text, /100%/);
  assert.match(model.rows[1].text, /\? left/);
  assert.match(model.rows[2].text, /USD 3.50/);
  const missing = panelModel({}, ['absent']);
  assert.match(missing.rows[0].text, /absent.*no data/);
});

test('registers an opt-in ambient dock with a distinct command and validated selectors', async () => {
  const { default: register } = await import(source);
  const apps = [];
  register({ defineWidgetApp: value => { apps.push(value); return value; } });
  const app = apps.find(value => value.id === 'quota-panel');
  assert.equal(app.id, 'quota-panel');
  assert.equal(app.mode, 'ambient');
  assert.equal(app.zone, 'dock-top');
  assert.deepEqual(app.init('anthropic').selected, ['anthropic']);
  assert.equal(app.init('anthropic,openai-codex'), null);
  assert.equal(app.init('$(touch hacked)'), null);
});

test('picker excludes missing credentials and disabled rows without reading keys', async () => {
  const { pickerOptions } = await import(source);
  const rows = pickerOptions({ providers: {
    absent: { unavailable_reason: 'no-credentials' },
    off: { unavailable_reason: 'disabled' },
    optedOut: { unavailable_reason: 'opt-in-disabled' },
    expired: { label: 'Reconnect me', unavailable_reason: 'auth-failed' },
    ready: { label: 'Ready', windows: [{ used_percent: 20 }] },
    'openai-codex': { accounts: [{ id: 'work', label: 'Work', windows: [{ used_percent: 10 }] }] },
  } });
  assert.deepEqual(rows.map(row => row.id), ['expired', 'ready', 'openai-codex:work']);
  assert.equal(rows[0].unavailable, true);
});

test('picker navigates, selects one account, and cancel never opens a panel', async () => {
  const { default: register } = await import(source);
  const apps = [], opened = [];
  const sdk = { defineWidgetApp: app => { apps.push(app); return app; },
    openWidget: (app, state) => opened.push([app.id, state]) };
  register(sdk);
  const picker = apps.find(app => app.id === 'quota-select');
  assert.equal(picker.mode, 'modal');
  let state = { phase: 'ready', options: [{ id: 'first' }, { id: 'second' }], index: 0 };
  state = picker.reduce(state, { ch: '', key: { downArrow: true } });
  assert.equal(state.index, 1);
  assert.equal(picker.reduce(state, { ch: '', key: { return: true } }), null);
  assert.deepEqual(opened, [['quota-panel', { selected: ['second'] }]]);
  assert.deepEqual(apps.find(app => app.id === 'quota-panel').init('').selected, ['second']);
  assert.equal(picker.reduce(state, { ch: '', key: { escape: true } }), null);
  assert.equal(opened.length, 1);
});

test('bar meters use remaining capacity and never fill unknown or unavailable windows', async () => {
  const { meterModel } = await import(source);
  const cache = { providers: {
    demo: { label: 'Demo', windows: [{ label: 'Weekly', used_percent: 75 }] },
    missing: { windows: [{ used_percent: null }] },
    expired: { unavailable_reason: 'auth-failed', windows: [{ used_percent: 0 }] },
  } };
  assert.equal(meterModel(cache, 'demo').meters[0].remaining, 25);
  assert.equal(meterModel(cache, 'missing').meters[0].remaining, null);
  assert.equal(meterModel(cache, 'expired').meters.length, 0);
  assert.equal(meterModel(cache, 'demo').label, 'Demo');
});

test('friendly provider names match Desktop and preserve account identity', async () => {
  const { panelModel, pickerOptions } = await import(source);
  const cache = { providers: {
    'openai-codex': { accounts: [{ id: 'personal', label: 'Personal', windows: [] }] },
    'anthropic:work': { account_label: 'Work', windows: [] },
    'opencode-go': { windows: [] },
  } };
  assert.deepEqual(panelModel(cache).rows.map(row => row.label), [
    'OpenAI Codex · Personal', 'Anthropic · Work', 'OpenCode Go',
  ]);
  assert.deepEqual(pickerOptions(cache).map(row => row.label), panelModel(cache).rows.map(row => row.label));
});

test('all public provider names stay aligned with the Desktop registry', async () => {
  const { readFile } = await import('node:fs/promises');
  const { PROVIDER_NAMES } = await import(source);
  const desktop = await readFile(new URL('../desktop/plugin.js', import.meta.url), 'utf8');
  const block = desktop.split('const PROVIDER_META = {')[1].split('\n};')[0];
  const names = Object.fromEntries([...block.matchAll(/\s*(?:"([^"]+)"|([\w]+)):\s*\{ name: "([^"]+)"/g)].map(match => [match[1] || match[2], match[3]]));
  assert.deepEqual(PROVIDER_NAMES, names);
});

test('single-line layout keeps the tightest window, reset date and overflow count', async () => {
  const { lineModel } = await import(source);
  const now = Date.parse('2026-10-22T12:00:00Z');
  const cache = { fetched_at: new Date(now).toISOString(), providers: {
    'openai-codex': { windows: [
      { label: 'Session', used_percent: 10, reset_at: '2026-10-22T13:00:00Z' },
      { label: 'Weekly', used_percent: 80, reset_at: '2026-10-27T14:30:00Z' },
    ] },
  } };
  const line = lineModel(cache, 'openai-codex', 100, now);
  assert.match(line.text, /OpenAI Codex/);
  assert.match(line.text, /Weekly/);
  assert.match(line.text, /20% left/);
  assert.match(line.text, /27\/10/);
  assert.doesNotMatch(line.text, /5d/);
  assert.doesNotMatch(line.text, /\n/);
  const small = lineModel(cache, 'openai-codex', 60, now);
  assert.ok([...small.text].length <= 60);
  assert.match(small.text, /\+1/);
  assert.match(lineModel(cache, 'openai-codex', 40, now).text, /\+1$/);
});

test('minimal quota line is explicit and omits routine cache noise', async () => {
  const { lineModel } = await import(source);
  const now = Date.parse('2026-10-22T12:00:00Z');
  const model = lineModel({ fetched_at: new Date(now).toISOString(), providers: {
    demo: { label: 'Demo', windows: [{ label: 'Weekly', used_percent: 60, reset_at: '2026-10-27T14:30:00Z' }] },
  } }, 'demo', 100, now);
  assert.match(model.text, /^Quota · Demo/);
  assert.doesNotMatch(model.text, /cache|5d/);
  assert.match(model.text, /27\/10/);
});

test('minimal quota renderer right-aligns and uses theme tokens by role', async () => {
  const { renderLine } = await import(source);
  const sdk = { Box: 'Box', Text: 'Text', h: (type, props, ...children) => ({ type, props, children }) };
  const t = { color: { primary: 'THEME_PRIMARY', muted: 'THEME_MUTED', ok: 'THEME_OK', warn: 'THEME_WARN', error: 'THEME_ERROR' } };
  const tree = renderLine(sdk, { text: 'Quota · Demo · Weekly ██░░ 40% left · reset 27/10 14:30', remaining: 40 }, t, 100);
  assert.equal(tree.props.justifyContent, 'flex-end');
  assert.equal(tree.props.height, 1);
  const nodes = tree.children[0].children;
  assert.ok(nodes.some(node => node.props?.color === 'THEME_PRIMARY' && node.children[0] === 'Quota'));
  assert.ok(nodes.some(node => node.props?.color === 'THEME_OK' && node.children[0].includes('██')));
  assert.ok(nodes.some(node => node.props?.color === 'THEME_MUTED'));
  const stale = renderLine(sdk, { text: 'Quota · STALE · Demo ██░░ 40% left', remaining: 40, stale: true }, t, 100);
  assert.ok(!stale.children[0].children.some(node => node.props?.color === 'THEME_OK'));
});

test('poller reads cache before refresh, is single-flight and stops on close', async () => {
  const { createPoller } = await import(source);
  let reads = 0, refreshes = 0, updates = 0, finish;
  const poller = createPoller({
    read: async () => { reads++; return {}; },
    refresh: async () => { refreshes++; await new Promise(resolve => { finish = resolve; }); },
    onUpdate: () => { updates++; },
    now: () => 100000,
  });
  await poller.tick();
  assert.equal(reads, 1);
  assert.equal(refreshes, 1);
  assert.equal(updates, 2);
  await poller.tick();
  assert.equal(refreshes, 1);
  poller.stop();
  const before = updates;
  finish();
  await new Promise(resolve => setImmediate(resolve));
  await poller.tick();
  assert.equal(updates, before);
  assert.equal(reads, 2);
});

test('refresh failure is visible and retries are throttled; fresh caches never refresh', async () => {
  const { createPoller } = await import(source);
  let now = Date.parse('2026-10-22T12:00:00Z'), attempts = 0, latest;
  const poller = createPoller({ read: async () => ({}),
    refresh: async () => { attempts++; throw Error('network down'); },
    now: () => now, onUpdate: state => { latest = state; } });
  await poller.tick();
  await new Promise(resolve => setImmediate(resolve));
  assert.match(latest.error, /Refresh failed/);
  await poller.tick();
  assert.equal(attempts, 1);
  now += 60000;
  await poller.tick();
  assert.equal(attempts, 2);
  poller.stop();
  const fresh = createPoller({ read: async () => ({ fetched_at: new Date(now).toISOString(), providers: {} }),
    refresh: async () => { throw Error('must not refresh'); }, now: () => now, onUpdate: () => {} });
  await fresh.tick();
  fresh.stop();
});

test('real cache reader respects HERMES_HOME and rejects malformed cache', async () => {
  const { mkdtemp, writeFile, rm } = await import('node:fs/promises');
  const { join } = await import('node:path');
  const { tmpdir } = await import('node:os');
  const { readCache } = await import(source);
  const dir = await mkdtemp(join(tmpdir(), 'quota-widget-'));
  const previous = process.env.HERMES_HOME;
  process.env.HERMES_HOME = dir;
  try {
    await assert.rejects(readCache());
    await writeFile(join(dir, 'quota_cache.json'), '{broken');
    await assert.rejects(readCache());
    await writeFile(join(dir, 'quota_cache.json'), JSON.stringify({ providers: {} }));
    assert.deepEqual(await readCache(), { providers: {} });
  } finally {
    if (previous === undefined) delete process.env.HERMES_HOME;
    else process.env.HERMES_HOME = previous;
    await rm(dir, { recursive: true, force: true });
  }
});
