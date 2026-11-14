// Run: npm test   (node's built-in test runner, no extra dependencies)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createReconnectingStream, backoffDelay, WS_STATUS } from '../src/lib/binanceWebSocket.js';

function harness({ throwOnCreate = 0 } = {}) {
    const sockets = [];
    const timers = [];
    let throws = throwOnCreate;
    class FakeWS {
        constructor(url) {
            if (throws > 0) { throws -= 1; throw new Error('boom'); }
            this.url = url;
            this.closed = false;
            sockets.push(this);
        }
        close() { this.closed = true; if (this.onclose) this.onclose(); }
    }
    const deps = {
        WebSocket: FakeWS,
        setTimeout: (fn, ms) => { const t = { fn, ms, cleared: false }; timers.push(t); return t; },
        clearTimeout: (t) => { t.cleared = true; },
    };
    const fire = () => { const t = timers.find((x) => !x.cleared && !x.fired); t.fired = true; t.fn(); return t; };
    return { sockets, timers, deps, fire };
}

const quiet = () => { const orig = { ...console }; console.log = console.warn = console.error = () => {}; return () => Object.assign(console, orig); };

test('connecting -> open -> reconnecting -> connecting -> open', () => {
    const restore = quiet();
    const h = harness();
    const statuses = [];
    const data = [];
    const cleanup = createReconnectingStream('btcusdt@aggTrade', (d) => data.push(d), (s) => statuses.push(s), h.deps);
    assert.equal(h.sockets[0].url, 'wss://stream.binance.com:9443/ws/btcusdt@aggTrade');
    h.sockets[0].onopen();
    h.sockets[0].onmessage({ data: '{"p":"1"}' });
    h.sockets[0].onclose();
    assert.equal(h.timers.length, 1);
    h.fire();
    h.sockets[1].onopen();
    assert.deepEqual(statuses, ['connecting', 'open', 'reconnecting', 'connecting', 'open']);
    assert.deepEqual(data, [{ p: '1' }]);
    cleanup();
    restore();
});

test('backoff grows and resets after a successful open', () => {
    const restore = quiet();
    const h = harness();
    createReconnectingStream('x', () => {}, null, h.deps);
    h.sockets[0].onclose(); const d1 = h.fire().ms;
    h.sockets[1].onclose(); const d2 = h.fire().ms;
    h.sockets[2].onclose(); const d3 = h.fire().ms;
    assert.ok(d1 < d2 && d2 < d3, `${d1} ${d2} ${d3}`);
    h.sockets[3].onopen();
    h.sockets[3].onclose(); const d4 = h.fire().ms;
    assert.ok(d4 <= 1200, `reset to base, got ${d4}`);
    restore();
});

test('backoff is capped at 30s', () => {
    assert.equal(backoffDelay(20, () => 0.5), 30000);
    assert.equal(backoffDelay(0, () => 0.5), 1000);
});

test('constructor throwing still schedules a reconnect (old code gave up)', () => {
    const restore = quiet();
    const h = harness({ throwOnCreate: 1 });
    const statuses = [];
    createReconnectingStream('x', () => {}, (s) => statuses.push(s), h.deps);
    assert.equal(h.sockets.length, 0);
    assert.equal(statuses.at(-1), WS_STATUS.RECONNECTING);
    h.fire();
    assert.equal(h.sockets.length, 1);
    restore();
});

test('cleanup is terminal: no reconnect, pending timer cleared', () => {
    const restore = quiet();
    const h = harness();
    const statuses = [];
    const cleanup = createReconnectingStream('x', () => {}, (s) => statuses.push(s), h.deps);
    h.sockets[0].onclose();          // reconnect pending
    cleanup();
    assert.equal(h.timers[0].cleared, true);
    assert.equal(statuses.at(-1), WS_STATUS.CLOSED);
    assert.equal(h.sockets.length, 1);
    restore();
});

test('cleanup while open closes the socket and does not reconnect', () => {
    const restore = quiet();
    const h = harness();
    const cleanup = createReconnectingStream('x', () => {}, null, h.deps);
    h.sockets[0].onopen();
    cleanup();
    assert.equal(h.sockets[0].closed, true);
    assert.equal(h.timers.length, 0);
    restore();
});

test('bad JSON is ignored, stream keeps going', () => {
    const restore = quiet();
    const h = harness();
    const data = [];
    createReconnectingStream('x', (d) => data.push(d), null, h.deps);
    h.sockets[0].onmessage({ data: 'not json' });
    h.sockets[0].onmessage({ data: '{"k":1}' });
    assert.deepEqual(data, [{ k: 1 }]);
    restore();
});

test('a stale socket closing after reconnect does not double-schedule', () => {
    const restore = quiet();
    const h = harness();
    createReconnectingStream('x', () => {}, null, h.deps);
    const first = h.sockets[0];
    first.onclose();
    h.fire();
    first.onclose(); // late duplicate event from the old socket
    assert.equal(h.timers.filter((t) => !t.fired && !t.cleared).length, 0);
    restore();
});
