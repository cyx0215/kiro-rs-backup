// Isolated audit harness. No dependencies, credentials, HTTP requests or app startup.
// Run: node aiclient2api-concurrency-repro.cjs <AIClient2API checkout>
// Executes unchanged methods extracted from the pinned source, with fake providers/I/O.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const { createHash } = require('node:crypto');

const repo = path.resolve(process.argv[2] || '.');
const commit = '4c4c774930c85f3760e9c89a1ef481f74eac4d00';
assert.equal(execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repo, encoding: 'utf8' }).trim(), commit);
const pool = fs.readFileSync(path.join(repo, 'src/providers/provider-pool-manager.js'), 'utf8');
const kiro = fs.readFileSync(path.join(repo, 'src/providers/claude/claude-kiro.js'), 'utf8');
function extract(text, start, end) {
  const a = text.indexOf(start);
  const b = text.indexOf(end, a + start.length);
  assert(a >= 0 && b > a, `Missing source markers: ${start}`);
  return text.slice(a, b).trimEnd();
}
const acquire = extract(pool, '    async acquireSlot(', '\n    /**');
const release = extract(pool, '    releaseSlot(', '\n    /**');
const select = extract(pool, '    async selectProvider(', '\n    /**');
const throttle = extract(kiro, 'function getKiroRequestMinIntervalMs(', '\nfunction normalizeKiroToolInput(');
const stream = extract(kiro, '    async * streamApiReal(', '\n    // 保留旧的非流式方法');
const wrapper = extract(kiro, '    async * generateContentStream(', '\n    /**');
const sourceHashes = Object.fromEntries(Object.entries({ acquire, release, select, throttle, stream, wrapper })
  .map(([name, text]) => [name, createHash('sha256').update(text).digest('hex')]));
const timers = { setTimeout, clearTimeout, setImmediate, Buffer };
const immediate = () => new Promise(resolve => setImmediate(resolve));

async function poolHandoffRace() {
  const Pool = vm.runInNewContext(`(class { ${acquire} ${release} ${select} })`, timers);
  const manager = new Pool();
  const provider = {
    config: { uuid: 'fake-account', concurrencyLimit: 1, queueLimit: 2 },
    state: { activeCount: 0, waitingCount: 0, queue: [] }
  };
  manager._isSelecting = {};
  manager._findProvider = () => provider;
  manager._doSelectProvider = async () => provider.config;
  manager._log = () => {};
  manager._getDisplayName = () => 'fake-account';
  await manager.acquireSlot('test'); // A active
  const queuedB = manager.acquireSlot('test', null, { queueTimeout: 2000 });
  await immediate();
  assert.equal(provider.state.waitingCount, 1);
  manager.releaseSlot('test', provider.config.uuid); // schedules B, without reserving a slot
  await manager.acquireSlot('test'); // C arrives before setImmediate(B)
  await queuedB;
  const active = provider.state.activeCount;
  assert.equal(active, 2, 'Expected reproduction of two active requests with limit one');
  manager.releaseSlot('test', provider.config.uuid);
  manager.releaseSlot('test', provider.config.uuid);
  return { concurrencyLimit: 1, observedActiveCount: active, reproduced: true };
}

async function streamRetry(minIntervalMs) {
  const noop = () => {};
  const Harness = vm.runInNewContext(`
    let kiroThrottleQueue = Promise.resolve();
    let kiroLastRequestStartedAt = 0;
    ${throttle}
    (class { ${stream} });
  `, { ...timers, uuidv4: () => 'fake-invocation',
       logger: { info: noop, warn: noop, error: noop },
       isRetryableNetworkError: error => error.code === 'ECONNRESET' });
  const instance = new Harness();
  instance.isInitialized = true;
  instance.config = { KIRO_REQUEST_MIN_INTERVAL_MS: minIntervalMs, REQUEST_MAX_RETRIES: 1, REQUEST_BASE_DELAY: 1 };
  instance.accessToken = 'fake-local-token';
  instance.baseUrl = 'https://invalid.local'; // Never passed to any real HTTP client
  instance._applySidecar = noop;
  let requests = 0, builds = 0;
  instance.buildCodewhispererRequest = async () => { builds++; return {}; };
  instance._requestWithBuilderEndpointRouting = async () => {
    requests++;
    if (requests === 1) throw Object.assign(new Error('Synthetic connection reset'), { code: 'ECONNRESET' });
    return { data: (async function* () { yield Buffer.from('fake-frame'); })() };
  };
  instance.parseAwsEventStreamBuffer = () => ({ events: [{ type: 'content', data: 'ok' }], remaining: Buffer.alloc(0) });
  const iterator = instance.streamApiReal('POST', 'test-model', { messages: [{ role: 'user', content: 'test' }] });
  const outcome = await Promise.race([
    iterator.next().then(value => ({ completed: true, content: value.value?.content })),
    new Promise(resolve => setTimeout(() => resolve({ completed: false }), 100))
  ]);
  if (outcome.completed) await iterator.return();
  return { minIntervalMs, requests, builds, ...outcome };
}

async function wrapperCancellation() {
  const Harness = vm.runInNewContext(`(class { ${wrapper} })`);
  const instance = new Harness();
  let cleanedUp = false;
  const raw = (async function* () {
    try {
      yield { type: 'message_start' };
      yield { type: 'content_block_delta' };
      yield { type: 'message_stop' };
    } finally { cleanedUp = true; }
  })();
  instance._generateContentStreamRaw = () => raw;
  const outer = instance.generateContentStream('test-model', {});
  await outer.next();
  await outer.return();
  const cleanedAfterClientCancellation = cleanedUp;
  assert.equal(cleanedAfterClientCancellation, false);
  await raw.return();
  assert.equal(cleanedUp, true);
  return { cleanedAfterClientCancellation, cleanedAfterExplicitInnerReturn: cleanedUp, reproduced: true };
}

(async () => {
  const poolResult = await poolHandoffRace();
  const throttleDisabled = await streamRetry(0);
  assert.equal(throttleDisabled.completed, true);
  assert.equal(throttleDisabled.requests, 2);
  const throttleEnabled = await streamRetry(1);
  assert.equal(throttleEnabled.completed, false);
  assert.equal(throttleEnabled.requests, 1);
  assert.equal(throttleEnabled.builds, 2);
  const cancellation = await wrapperCancellation();
  console.log(JSON.stringify({
    checkedAt: new Date().toISOString(), commit, node: process.version, sourceHashes,
    scope: 'Unchanged source methods in VM with fake provider selection and fake network; not a full app or upstream integration test',
    poolHandoffRace: poolResult,
    networkRetry: { throttleDisabled, throttleEnabled, deadlockReproduced: true },
    wrapperCancellation: cancellation,
    assertionsPassed: true
  }, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
