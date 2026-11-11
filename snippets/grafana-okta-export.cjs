// Guided SSO login -> read-only export of one Grafana org -> logout.
// GET requests only. The session cookie is never written to disk; the browser profile is ephemeral.
//
// Usage:
//   npm i playwright
//   GRAFANA_BASE_URL=https://grafana.example.com GRAFANA_ORG=1 node grafana-okta-export.cjs
//
// Optional: OUT_DIR (default /tmp/grafana-export), GRAFANA_LOGIN_TIMEOUT_MS.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.GRAFANA_BASE_URL;
if (!BASE) { console.error('Set GRAFANA_BASE_URL to the Grafana origin.'); process.exit(2); }

const ORG = process.env.GRAFANA_ORG || '1';
const OUT = process.env.OUT_DIR || '/tmp/grafana-export';
const LOGIN_TIMEOUT_MS = Number(process.env.GRAFANA_LOGIN_TIMEOUT_MS || 8 * 60 * 1000);

const ENDPOINTS = [
  ['health.json', '/api/health'],
  ['user.json', '/api/user'],
  ['user-orgs.json', '/api/user/orgs'],
  ['org.json', '/api/org'],
  ['folders.json', '/api/folders?limit=1000'],
  ['datasources.json', '/api/datasources'],
  ['org-rules.yaml', '/api/v1/provisioning/alert-rules/export?format=yaml'],
  ['org-rules-ruler-export.yaml', '/api/ruler/grafana/api/v1/export/rules?format=yaml'],
  ['org-rules.json', '/api/v1/provisioning/alert-rules'],
  ['org-ruler-config.json', '/api/ruler/grafana/api/v1/rules'],
  ['org-rule-state.json', '/api/prometheus/grafana/api/v1/rules'],
  ['org-contact-points.yaml', '/api/v1/provisioning/contact-points/export?format=yaml'],
  ['org-contact-points.json', '/api/v1/provisioning/contact-points'],
  ['org-policies.yaml', '/api/v1/provisioning/policies/export?format=yaml'],
  ['org-policies.json', '/api/v1/provisioning/policies'],
  ['org-mute-timings.json', '/api/v1/provisioning/mute-timings'],
  ['org-templates.json', '/api/v1/provisioning/templates'],
  ['org-silences.json', '/api/alertmanager/grafana/api/v2/silences'],
  ['org-am-config.json', '/api/alertmanager/grafana/config/api/v1/alerts'],
];

(async () => {
  fs.mkdirSync(OUT, { recursive: true, mode: 0o700 });
  fs.chmodSync(OUT, 0o700);

  const browser = await chromium.launch({ channel: 'chrome', headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();
  console.log('Opened Chrome. Complete the SSO sign-in in that window...');
  await page.goto(BASE + '/login');

  // Wait until the session is authenticated.
  const deadline = Date.now() + LOGIN_TIMEOUT_MS;
  let me = null;
  while (Date.now() < deadline) {
    try {
      const r = await context.request.get(BASE + '/api/user', { failOnStatusCode: false });
      if (r.status() === 200) { me = await r.json(); break; }
    } catch (_) {}
    await new Promise(r => setTimeout(r, 2000));
  }
  if (!me) { console.error('Timed out waiting for login.'); await browser.close(); process.exit(2); }
  console.log(`Signed in as ${me.login} (${me.email}).`);

  const headers = { 'X-Grafana-Org-Id': ORG, Accept: 'application/json, application/yaml, */*' };
  const summary = [];
  for (const [file, ep] of ENDPOINTS) {
    const r = await context.request.get(BASE + ep, { headers, failOnStatusCode: false });
    const body = await r.text();
    fs.writeFileSync(path.join(OUT, file), body, { mode: 0o600 });
    summary.push(`${r.status()}  ${ep}  -> ${file} (${body.length} bytes)`);
  }
  console.log(summary.join('\n'));

  // Revoke the session server-side, then close the ephemeral browser.
  try { await page.goto(BASE + '/logout', { timeout: 15000 }); } catch (_) {}
  await browser.close();
  console.log('Logged out and closed browser. Exports in ' + OUT);
})().catch(async e => { console.error('ERROR', e.message); process.exit(1); });
