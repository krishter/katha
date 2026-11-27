// POST /api/interest: adds an email from the "Keep me updated" form to the Sender waitlist group.
// Joining the group starts Sender's thank-you automation. Formspree still receives every submission
// first (main.js); this call is extra, so a failure here never shows the person an error.
//
// Env (Vercel project "katha"):
//   SENDER_API_TOKEN          Sender → Settings → API access tokens. Full account access: keep it here only.
//   SENDER_WAITLIST_GROUP_ID  id of the waitlist group (Sender shows it in the group's URL).
//
// The email is never logged.

const SENDER = 'https://api.sender.net/v2';
const ALLOWED_ORIGINS = new Set(['https://katha.life', 'https://www.katha.life']);
const EMAIL = /^[^\s@]{1,64}@[^\s@]+\.[^\s@]{2,}$/;

export async function readEmail(request) {
  const type = request.headers.get('content-type') || '';
  let email = '';
  let honeypot = '';
  try {
    if (type.includes('application/json')) {
      const body = await request.json();
      email = typeof body?.email === 'string' ? body.email : '';
      honeypot = typeof body?._gotcha === 'string' ? body._gotcha : '';
    } else {
      const form = await request.formData();
      email = String(form.get('email') || '');
      honeypot = String(form.get('_gotcha') || '');
    }
  } catch {
    return { email: '', honeypot: '' };
  }
  return { email: email.trim().toLowerCase(), honeypot: honeypot.trim() };
}

async function sender(path, token, body, fetchImpl) {
  return fetchImpl(`${SENDER}${path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(body),
  });
}

// Creates the subscriber in the group. If Sender refuses the create (already a subscriber, say),
// adds the existing subscriber to the group instead. Either way the automation is triggered.
export async function addToWaitlist(email, { token, groupId, fetchImpl = fetch }) {
  const created = await sender('/subscribers', token, { email, groups: [groupId], trigger_automation: true }, fetchImpl);
  if (created.ok) return { ok: true, via: 'create', status: created.status };
  if (created.status === 401 || created.status === 429 || created.status >= 500) {
    return { ok: false, via: 'create', status: created.status };
  }
  const added = await sender(`/subscribers/groups/${encodeURIComponent(groupId)}`, token,
    { subscribers: [email], trigger_automation: true }, fetchImpl);
  return { ok: added.ok, via: 'group', status: added.status };
}

export async function handle(request, env = process.env, fetchImpl = fetch) {
  if (request.method !== 'POST') return new Response(null, { status: 405, headers: { Allow: 'POST' } });
  if (!ALLOWED_ORIGINS.has(request.headers.get('origin') || '')) return new Response(null, { status: 403 });

  const { email, honeypot } = await readEmail(request);
  if (honeypot) return new Response(null, { status: 204 }); // a bot filled the hidden field
  if (email.length > 254 || !EMAIL.test(email)) return Response.json({ error: 'invalid_email' }, { status: 400 });

  const token = env.SENDER_API_TOKEN;
  const groupId = env.SENDER_WAITLIST_GROUP_ID;
  if (!token || !groupId) {
    console.error('interest: Sender is not configured');
    return new Response(null, { status: 503 });
  }

  try {
    const result = await addToWaitlist(email, { token, groupId, fetchImpl });
    if (result.ok) return new Response(null, { status: 204 });
    console.error(`interest: Sender ${result.via} returned ${result.status}`);
  } catch (err) {
    console.error(`interest: Sender call failed: ${err?.name || 'error'}`);
  }
  return new Response(null, { status: 502 });
}

export default { fetch: (request) => handle(request) };
