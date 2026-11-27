// node --test coming-soon/test/*.test.mjs   (Node 22+; .vercelignore keeps this folder off the site)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { handle } from '../api/interest.js';

const env = { SENDER_API_TOKEN: 'test-token', SENDER_WAITLIST_GROUP_ID: 'grp1' };

function req(body, { origin = 'https://katha.life', method = 'POST', json = true } = {}) {
  const headers = { origin };
  let payload;
  if (method === 'POST') {
    if (json) {
      headers['content-type'] = 'application/json';
      payload = JSON.stringify(body);
    } else {
      const form = new FormData();
      for (const [k, v] of Object.entries(body)) form.set(k, v);
      payload = form;
    }
  }
  return new Request('https://katha.life/api/interest', { method, headers, body: payload });
}

function fakeSender(...statuses) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init, body: JSON.parse(init.body) });
    return new Response('{}', { status: statuses[calls.length - 1] ?? 500 });
  };
  return { calls, fetchImpl };
}

test('new email is created in the waitlist group with the automation on', async () => {
  const s = fakeSender(201);
  const res = await handle(req({ email: ' Asha@Example.com ' }), env, s.fetchImpl);
  assert.equal(res.status, 204);
  assert.equal(s.calls.length, 1);
  assert.equal(s.calls[0].url, 'https://api.sender.net/v2/subscribers');
  assert.deepEqual(s.calls[0].body, { email: 'asha@example.com', groups: ['grp1'], trigger_automation: true });
  assert.equal(s.calls[0].init.headers.Authorization, 'Bearer test-token');
});

test('an email Sender refuses to create is added to the group instead', async () => {
  const s = fakeSender(422, 200);
  const res = await handle(req({ email: 'asha@example.com' }), env, s.fetchImpl);
  assert.equal(res.status, 204);
  assert.equal(s.calls[1].url, 'https://api.sender.net/v2/subscribers/groups/grp1');
  assert.deepEqual(s.calls[1].body, { subscribers: ['asha@example.com'], trigger_automation: true });
});

test('a bad token or Sender outage is not retried through the group endpoint', async () => {
  for (const status of [401, 429, 503]) {
    const s = fakeSender(status);
    const res = await handle(req({ email: 'asha@example.com' }), env, s.fetchImpl);
    assert.equal(res.status, 502);
    assert.equal(s.calls.length, 1);
  }
});

test('form-encoded bodies work too', async () => {
  const s = fakeSender(201);
  const res = await handle(req({ email: 'asha@example.com' }, { json: false }), env, s.fetchImpl);
  assert.equal(res.status, 204);
});

test('filled honeypot is accepted silently and never reaches Sender', async () => {
  const s = fakeSender(201);
  const res = await handle(req({ email: 'asha@example.com', _gotcha: 'x' }), env, s.fetchImpl);
  assert.equal(res.status, 204);
  assert.equal(s.calls.length, 0);
});

test('requests from other sites are refused', async () => {
  const s = fakeSender(201);
  for (const origin of ['https://evil.example', '']) {
    const res = await handle(req({ email: 'asha@example.com' }, { origin }), env, s.fetchImpl);
    assert.equal(res.status, 403);
  }
  assert.equal(s.calls.length, 0);
});

test('invalid emails, wrong method and missing config', async () => {
  const s = fakeSender(201);
  for (const email of ['', 'not-an-email', 'a@b', `${'a'.repeat(250)}@example.com`, 42]) {
    assert.equal((await handle(req({ email }), env, s.fetchImpl)).status, 400);
  }
  assert.equal((await handle(req(null, { method: 'GET' }), env, s.fetchImpl)).status, 405);
  assert.equal((await handle(req({ email: 'asha@example.com' }), {}, s.fetchImpl)).status, 503);
  assert.equal(s.calls.length, 0);
});

test('a network failure returns 502', async () => {
  const res = await handle(req({ email: 'asha@example.com' }), env, async () => { throw new TypeError('fetch failed'); });
  assert.equal(res.status, 502);
});
