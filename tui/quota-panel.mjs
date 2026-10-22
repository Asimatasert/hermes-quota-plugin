// Standalone Hermes Ink widget. No credentials or Hermes core imports.
import { readFile, stat } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { execFile } from 'node:child_process';

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const clean = value => String(value ?? '').replace(/[\x00-\x1f\x7f-\x9f]/g, '').slice(0, 120);
const list = value => Array.isArray(value) ? value : [];

// Keep these public display names aligned with Desktop PROVIDER_META.
export const PROVIDER_NAMES = {
  anthropic: 'Anthropic', 'openai-codex': 'OpenAI Codex', nous: 'Nous Portal',
  openrouter: 'OpenRouter', deepseek: 'DeepSeek', experientiallabs: 'Experiential Labs',
  gemini: 'Google Gemini', kimi: 'Kimi / Moonshot', grok: 'xAI Grok',
  'opencode-go': 'OpenCode Go', copilot: 'Copilot', zai: 'Z.ai',
  commandcode: 'CommandCode', cursor: 'Cursor', antigravity: 'Antigravity',
  minimax: 'MiniMax', ollama: 'Ollama',
};

function friendlyName(id, record, account) {
  const [base, ...suffix] = id.split(':');
  const name = Object.hasOwn(PROVIDER_NAMES, base) ? PROVIDER_NAMES[base] : clean(record.label || base);
  const label = account || record.account_label || suffix.join(':');
  return label ? `${name} · ${clean(label)}` : name;
}

function resetDate(value, now) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  const pad = n => String(n).padStart(2, '0');
  const year = date.getFullYear() !== new Date(now).getFullYear() ? `/${date.getFullYear()}` : '';
  return `${pad(date.getDate())}/${pad(date.getMonth() + 1)}${year} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function resetLabel(value, now) {
  const time = typeof value === 'string' ? Date.parse(value) : NaN;
  if (!Number.isFinite(time)) return '';
  const minutes = Math.ceil((time - now) / 60000);
  if (minutes <= 0) return 'reset due';
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const relative = days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes % 60}m` : `${minutes}m`;
  return `reset ${relative} (${resetDate(value, now)})`;
}

export function panelModel(cache, selected = [], now = Date.now()) {
  const timestamp = typeof cache?.fetched_at === 'string' ? Date.parse(cache.fetched_at) : NaN;
  const age = Number.isFinite(timestamp) ? Math.max(0, (now - timestamp) / 1000) : null;
  const providers = object(cache?.providers) ? cache.providers : {};
  const rows = [];
  for (const [id, record] of Object.entries(providers)) {
    if (!object(record)) continue;
    const accounts = id === 'openai-codex' && list(record.accounts).length ? record.accounts : [record];
    accounts.forEach((account, index) => {
      if (!object(account)) return;
      const key = accounts === record.accounts ? `${id}:${account.id || index + 1}` : id;
      if (selected.length && !selected.some(s => s === key || s === id || key.startsWith(`${s}:`))) return;
      const label = friendlyName(id, record, accounts === record.accounts ? account.label || `Account ${index + 1}` : null);
      const windows = list(account.windows).filter(object);
      const meters = windows.map(window => {
        const used = window.used_percent;
        const percent = typeof used === 'number' && Number.isFinite(used) && used >= 0 && used <= 100
          ? `${Math.round(100 - used)}% left` : '? left';
        const reset = resetLabel(window.reset_at, now);
        return `${clean(window.label || 'Window')} ${percent}${reset ? ` (${reset})` : ''}`;
      });
      const balances = list(account.account_balances).filter(object).filter(b =>
        typeof b.total_balance === 'string' && /^-?\d+(\.\d+)?$/.test(b.total_balance)
      ).map(b => `${clean(b.currency)} ${clean(b.total_balance)}`);
      const unavailable = Boolean(account.unavailable_reason);
      rows.push({ id: key, label, windows, balances, reason: account.unavailable_reason,
        text: `${label}: ${unavailable ? 'unavailable' : [...meters, ...balances].join(' · ') || 'no data'}`, unavailable, hasData: !unavailable && Boolean(meters.length || balances.length) });
    });
  }
  for (const selector of selected) {
    if (!rows.some(row => row.id === selector || row.id.startsWith(`${selector}:`))) {
      rows.push({ id: selector, text: `${clean(selector)}: no data`, unavailable: true, hasData: false });
    }
  }
  return { rows, stale: age === null || age > 600, age };
}

/** Cached availability is evidence, not a new credential scan or login check. */
export function pickerOptions(cache) {
  return panelModel(cache).rows.filter(row => !/^(no-credentials|disabled|not-enabled|not-opted-in|opt-in-disabled)(:|$)/i.test(String(row.reason || '')));
}

export function meterModel(cache, selector, now = Date.now()) {
  const model = panelModel(cache, [selector], now);
  const row = model.rows.find(item => item.id === selector) || model.rows[0];
  const meters = row?.unavailable ? [] : list(row?.windows).map(window => {
    const used = window.used_percent;
    return { label: clean(window.label || 'Window'), reset: resetLabel(window.reset_at, now),
      resetDate: typeof window.reset_at === 'string' ? resetDate(window.reset_at, now) : '',
      remaining: typeof used === 'number' && Number.isFinite(used) && used >= 0 && used <= 100 ? Math.round(100 - used) : null };
  });
  return { ...model, label: row?.label || clean(selector || 'Choose a provider'),
    unavailable: Boolean(row?.unavailable), balances: row?.balances || [], meters };
}

const shorten = (text, limit) => [...text].length <= limit ? text : [...text].slice(0, Math.max(0, limit - 1)).join('') + '…';
const length = text => [...text].length;

/** Exactly one line; tightest known window first, with explicit overflow. */
export function lineModel(cache, selector, cols, now = Date.now(), status = {}) {
  const model = meterModel(cache, selector, now);
  const meters = [...model.meters].sort((a, b) => (a.remaining ?? Infinity) - (b.remaining ?? Infinity));
  const prefix = `Quota · ${model.stale ? 'STALE · ' : ''}`;
  const error = status.error ? 'refresh error · ' : '';
  const width = Math.max(1, Math.floor(cols));
  const meter = meters[0];
  const remaining = meter?.remaining ?? null;
  const overflow = meters.length > 1 ? ` · +${meters.length - 1}` : '';
  let barWidth = 6, nameWidth = 24, windowWidth = 12;
  const segment = window => {
    const left = window.remaining === null ? '?' : String(window.remaining);
    const filled = window.remaining === null ? 0 : Math.round(window.remaining / 100 * barWidth);
    const bar = '█'.repeat(filled) + '░'.repeat(barWidth - filled);
    const reset = width < 60 ? '' : window.resetDate && window.reset !== 'reset due' ? `reset ${window.resetDate}` : window.reset;
    return `${width > 48 ? `${shorten(window.label, windowWidth)} ` : ''}${bar} ${left}%${width > 48 ? ' left' : ''}${reset ? ` · ${reset}` : ''}`;
  };
  const build = () => `${prefix}${error}${shorten(model.label, nameWidth)} · ${meter ? segment(meter) : model.unavailable ? 'unavailable' : model.balances.join(' · ') || '/quota-select'}${overflow}`;
  if (length(build()) > width) barWidth = 4;

  if (length(build()) > width) nameWidth = Math.max(8, Math.min(24, Math.floor(width / 4)));
  if (length(build()) > width) windowWidth = 6;
  let text = build();

  if (overflow && length(text) > width && width > length(overflow)) {
    text = shorten(text.slice(0, -overflow.length), width - length(overflow)) + overflow;
  }
  return { text: shorten(text, width), stale: model.stale, remaining, unavailable: model.unavailable, error: Boolean(status.error) };
}

/** Role-colored chip, with no status-bar-like background or full-width rule. */
export function renderLine(sdk, model, t, available) {
  const { h, Box, Text } = sdk;
  const color = model.error || model.unavailable ? t.color.error : model.stale || model.remaining === null ? t.color.muted
    : model.remaining <= 10 ? t.color.error : model.remaining <= 30 ? t.color.warn : t.color.ok;
  const chunks = model.text.split(/(Quota|[█░]+|(?:\d+|\?)%(?: left)?|STALE|refresh error)/g).filter(Boolean);
  return h(Box, { width: available, height: 1, justifyContent: 'flex-end' },
    h(Text, { wrap: 'truncate-end' }, ...chunks.map((chunk, index) => h(Text, {
      key: index, color: chunk === 'Quota' ? t.color.primary : chunk === 'refresh error' ? t.color.error
        : /[█░%]/.test(chunk) ? color : t.color.muted,
      bold: chunk === 'Quota',
    }, chunk))));
}

/** Poll only while mounted. Cache reads never wait for the network refresh. */
export function createPoller({ read, refresh, onUpdate, now = Date.now }) {
  let stopped = false, reading = false, refreshing = false, lastAttempt = -Infinity;
  let cache = {}, error = '', refreshingError = '';
  const controller = new AbortController();
  const publish = () => {
    if (!stopped) onUpdate({ cache, error: error || refreshingError, refreshing });
  };
  async function load() {
    try { cache = await read(); error = ''; }
    catch { cache = {}; error = 'Cache unavailable'; }
  }
  return {
    async tick() {
      if (stopped || reading) return;
      reading = true;
      try { await load(); } finally { reading = false; }
      if (stopped) return;
      publish();
      if (panelModel(cache, [], now()).stale && !refreshing && now() - lastAttempt >= 60000) {
        refreshing = true;
        lastAttempt = now();
        refreshingError = '';
        publish();
        void (async () => {
          try {
            await refresh(controller.signal);
            if (!stopped) await load();
          } catch { refreshingError = 'Refresh failed; retry in 60s'; }
          finally { refreshing = false; publish(); }
        })();
      }
    },
    stop() { stopped = true; controller.abort(); },
  };
}

const hermesHome = () => process.env.HERMES_HOME?.trim() || join(homedir(), '.hermes');
export async function readCache() {
  const path = join(hermesHome(), 'quota_cache.json');
  if ((await stat(path)).size > 1024 * 1024) throw new Error('Cache too large');
  const cache = JSON.parse(await readFile(path, 'utf8'));
  if (!object(cache) || !object(cache.providers)) throw new Error('Invalid cache');
  return cache;
}

function refreshCache(signal) {
  return new Promise((resolve, reject) => {
    // Fixed argv, no shell or selectors in the command. Existing plugin owns all I/O.
    execFile('hermes', ['quota', 'refresh'], {
      signal, timeout: 45000, killSignal: 'SIGKILL', maxBuffer: 65536, windowsHide: true,
    }, error => error ? reject(error) : resolve());
  });
}

export default function register(sdk) {
  const { h, Box, Text, React, defineWidgetApp, Overlay, Dialog } = sdk;
  let chosen = null;
  let panel, picker;
  function Panel({ selected, t, cols }) {
    const [snapshot, setSnapshot] = React.useState({ cache: {}, error: '', refreshing: false });
    const [now, setNow] = React.useState(Date.now);
    React.useEffect(() => {
      if (!selected.length) {
        sdk.openWidget(picker, picker.init(''));
        return;
      }
      const poller = createPoller({ read: readCache, refresh: refreshCache, onUpdate: setSnapshot });
      void poller.tick();
      const timer = setInterval(() => { setNow(Date.now()); void poller.tick(); }, 15000);
      return () => { clearInterval(timer); poller.stop(); };
    }, []);
    // Host dock reserves two trailing columns, plus outer chrome padding.
    const available = Math.max(1, cols - 4);
    const model = lineModel(snapshot.cache, selected[0], available, now, snapshot);
    return renderLine(sdk, model, t, available);
  }

  function Picker({ state, t, cols, rows }) {
    React.useEffect(() => {
      let mounted = true;
      readCache().then(cache => {
        if (mounted) sdk.updateWidget(picker, current => ({ ...current, phase: 'ready', options: pickerOptions(cache), stale: panelModel(cache).stale }));
      }, () => {
        if (mounted) sdk.updateWidget(picker, current => ({ ...current, phase: 'error' }));
      });
      return () => { mounted = false; };
    }, []);
    const count = Math.max(1, Math.min(7, rows - 14));
    const start = Math.max(0, Math.min(state.index - Math.floor(count / 2), state.options.length - count));
    const visible = state.options.slice(start, start + count);
    return h(Overlay, { zone: 'center', backdrop: true },
      h(Dialog, { width: Math.max(12, Math.min(76, cols)), title: 'Quota · choose provider / account', hint: '↑↓ choose · Enter confirm · Esc cancel' },
        h(Box, { flexDirection: 'column' },
          h(Text, { color: t.color.muted, wrap: 'truncate-end' }, 'From quota cache · no key scan · display only'),
          ...(state.phase === 'loading' ? [h(Text, { key: 'loading' }, 'Reading cache…')]
            : state.phase === 'error' ? [h(Text, { key: 'error', color: t.color.error }, 'Cache unavailable. Run /quota refresh first.')]
              : !visible.length ? [h(Text, { key: 'empty' }, 'No configured providers in cache. Run /quota refresh.')]
                : visible.map((option, index) => h(Text, { key: option.id, wrap: 'truncate-end',
                  color: start + index === state.index ? t.color.primary : t.color.text,
                  bold: start + index === state.index }, `${start + index === state.index ? '›' : ' '} ${option.label} · ${option.unavailable ? 'unavailable' : 'cached'}`))),
          h(Text, { color: t.color.muted }, state.phase === 'ready' ? `${state.options.length ? state.index + 1 : 0}/${state.options.length}${state.stale ? ' · STALE snapshot' : ''}` : ' '),
        )),
    );
  }
  panel = defineWidgetApp({
    id: 'quota-panel', help: 'Toggle one-account quota bar above the composer',
    mode: 'ambient', zone: 'dock-top', width: 100,
    usage: '/quota-panel [provider:account] · /quota-select to choose interactively',
    init(arg) {
      const selected = [...new Set(arg.trim().split(/[\s,]+/).filter(Boolean))];
      if (selected.length > 1 || selected.some(id => !/^[a-z0-9][a-z0-9:_#.-]{0,119}$/i.test(id))) return null;
      if (!selected.length && chosen) selected.push(chosen);
      return { selected };
    },
    reduce: state => state,
    render: ({ state, t, cols }) => h(Panel, { key: state.selected.join(','), selected: state.selected, t, cols }),
  });
  picker = defineWidgetApp({
    id: 'quota-select', help: 'Choose a cached configured provider/account for the quota bar', mode: 'modal',
    init: () => ({ phase: 'loading', options: [], index: 0 }),
    reduce(state, { ch, key }) {
      if (key.escape || (key.ctrl && ch === 'c') || ch === 'q') return null;
      if (key.return && state.options[state.index]) {
        chosen = state.options[state.index].id;
        sdk.openWidget(panel, { selected: [chosen] });
        return null;
      }
      if (key.downArrow && state.options.length) return { ...state, index: (state.index + 1) % state.options.length };
      if (key.upArrow && state.options.length) return { ...state, index: (state.index + state.options.length - 1) % state.options.length };
      return state;
    },
    render: ({ state, t, cols, rows }) => h(Picker, { state, t, cols, rows }),
  });
}
