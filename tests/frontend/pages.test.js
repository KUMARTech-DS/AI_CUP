/* Page-level checks: every page's inline JS parses, markup is sane, and the pure helpers
   embedded in the pages behave (tested in place, extracted from the inline scripts). */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { read, inlineScripts, pageHelpers, PAGES } = require('./harness');

const XSS = `<img src=x onerror="alert('1')">&`;
const XSS_SAFE = '&lt;img src=x onerror=&quot;alert(&#39;1&#39;)&quot;&gt;&amp;';

for (const page of PAGES) {
  test(`${page}: inline scripts parse`, () => {
    for (const { code, line } of inlineScripts(page)) {
      assert.doesNotThrow(() => new vm.Script(code, { filename: `${page}:${line}` }), `<script> at line ${line}`);
    }
  });

  test(`${page}: no duplicate ids or merge-conflict markers`, () => {
    const src = read(page);
    const seen = new Set();
    const dupes = [...src.matchAll(/\bid="([^"$]+)"/g)].map((m) => m[1]).filter((id) => seen.has(id) || !seen.add(id));
    assert.deepEqual(dupes, []);
    assert.doesNotMatch(src, /^(<<<<<<<|>>>>>>>) /m);
  });
}

test('pages load the shared session + config scripts before their own code', () => {
  for (const page of PAGES) {
    const src = read(page);
    const cfg = src.indexOf('src="./config.js"'), ses = src.indexOf('src="./session.js"');
    assert.ok(cfg > -1 && ses > cfg, `${page}: config.js then session.js`);
    const firstInline = src.search(/<script(?![^>]*\bsrc=)[^>]*>/);
    assert.ok(firstInline === -1 || firstInline > ses, `${page}: inline code runs after session.js`);
  }
});

test('every page escapes HTML the same way', () => {
  const helpers = [
    ['admin.html', 'esc'], ['campaigns.html', 'esc'], ['geo.html', 'esc'],
    ['ads_lab.html', 'esc'], ['style_assistant.html', 'escapeHtml'],
  ];
  for (const [page, name] of helpers) {
    const fn = pageHelpers(page, [name])[name];
    assert.equal(fn(XSS), XSS_SAFE, `${page} ${name}`);
    assert.equal(fn(null), '', `${page} ${name}(null)`);
    assert.equal(fn(0), '0', `${page} ${name}(0)`);
  }
});

test('shell clean-URL paths match the server routes', () => {
  const main = fs.readFileSync(path.join(__dirname, '..', '..', 'command_center', 'main.py'), 'utf8');
  const server = JSON.parse(main.match(/SHELL_PATHS = \(([^)]*)\)/)[1].replace(/'/g, '"').replace(/^/, '[').replace(/,?\s*$/, ']'));
  const location = { pathname: '/' };
  const shell = pageHelpers('index.html', ['TAB_PATHS', 'tabFromPath'], { location });
  assert.deepEqual([...Object.values(shell.TAB_PATHS)].sort(), [...server].sort());
  for (const [tab, seg] of Object.entries(shell.TAB_PATHS)) {
    for (const p of [`/${seg}`, `/${seg}/`, `/${seg.toUpperCase()}`]) {
      location.pathname = p;
      assert.equal(shell.tabFromPath(), tab, p);
    }
  }
  for (const p of ['/', '/index.html', '/unknown', '/geo/extra']) {
    location.pathname = p;
    assert.equal(shell.tabFromPath(), '', p);
  }
});

test('geo: haversineKm and norm01', () => {
  const { haversineKm, norm01 } = pageHelpers('geo.html', ['haversineKm', 'norm01']);
  assert.equal(haversineKm(12.97, 77.59, 12.97, 77.59), 0);
  assert.ok(Math.abs(haversineKm(12, 77, 13, 77) - 111.2) < 0.1, '1° latitude ≈ 111.2 km');
  // MG Road → Koramangala, Bengaluru: about 4.5 km, the size the 4 km guardrail works at
  const km = haversineKm(12.9756, 77.6050, 12.9352, 77.6245);
  assert.ok(km > 4.5 && km < 5.2, String(km));

  const n = norm01([10, 20, NaN, 30]);
  assert.deepEqual([n(10), n(20), n(30)], [0, 0.5, 1]);
  assert.equal(norm01([])(5), 0);
  assert.equal(norm01([7, 7])(7), 1, 'all-equal positive values rank as top');
  assert.equal(norm01([0, 0])(0), 0);
});

test('ads lab: audience value normalisation and formatting', () => {
  const h = pageHelpers('ads_lab.html', ['normal', 'normalizedGender', 'normalizedValue', 'fmtDuration']);
  assert.equal(h.normal('  Smart_Casual--Wear  '), 'smart casual wear');
  for (const g of ['F', 'female', 'Women']) assert.equal(h.normalizedGender(g), 'f');
  for (const g of ['m', 'MALE', 'men']) assert.equal(h.normalizedGender(g), 'm');
  assert.equal(h.normalizedGender('Unisex'), 'u');
  assert.equal(h.normalizedValue('pin_code', '560034.0'), '560034');
  assert.equal(h.normalizedValue('pin_code', 560034), '560034');
  assert.equal(h.normalizedValue('fabric', 'Pure_Cotton'), 'pure cotton');

  assert.equal(h.fmtDuration(-5), '0s');
  assert.equal(h.fmtDuration('abc'), '0s');
  assert.equal(h.fmtDuration(59.4), '59s');
  assert.equal(h.fmtDuration(60), '1 min');
  assert.equal(h.fmtDuration(3725), '1h 02m');
});

test('campaigns: links, slugs and audience labels', () => {
  const h = pageHelpers('campaigns.html', [
    'slug', 'stripUtm', 'splitList', 'displayGender', 'defaultName', 'statusLabel', 'localDay',
  ]);
  assert.equal(h.stripUtm('https://shop.example/p?utm_source=x&page=2&UTM_medium=y'), 'https://shop.example/p?page=2');
  assert.equal(h.stripUtm('https://shop.example/p?utm_source=x'), 'https://shop.example/p');
  assert.equal(h.stripUtm('  not a url '), 'not a url');
  assert.equal(h.stripUtm(null), '');

  assert.equal(h.slug('Diwali Sale — 50% Off!'), 'diwali-sale-50-off');
  assert.equal(h.slug('***'), 'campaign');
  assert.equal(h.slug('x'.repeat(80)).length, 48);

  assert.equal(h.displayGender('m, F'), 'Male, Female');
  assert.equal(h.displayGender(''), 'All genders');
  assert.equal(h.defaultName({ brand_name: 'Acme', pin_codes: ['560034'], rfm_segment: 'Champions' }), 'Acme · 560034 · Champions');
  assert.equal(h.defaultName({ pin_codes: ['1', '2', '3'] }), 'Brand · 3 PINs');
  assert.equal(h.defaultName({}), 'Brand · All PINs');

  assert.equal(h.statusLabel('launched'), 'Running');
  assert.equal(h.statusLabel('mystery'), 'mystery');
  assert.equal(h.localDay('garbage-value'), 'garbage-va');
  assert.match(h.localDay('2026-10-18T10:00:00'), /^2026-10-18$/);
});
