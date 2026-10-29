// Browser test of pitch calibration against a synthetic pitch video with known landmark pixels.
//
//   backend/.venv/bin/python e2e/make_pitch_video.py /tmp/pv
//   node e2e/pitch_smoke.js /tmp/pv [baseUrl] [screenshotDir]
//
// Registers a club, uploads pitch.mp4, opens Pitch setup, clicks 6 landmarks where they truly
// are (with ~2px of "hand" error), saves, and checks the fit error and that the overlay is drawn.
'use strict';
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const [dir, base = 'http://localhost:8000', shots = 'e2e/screenshots'] = process.argv.slice(2);
if (!dir) { console.error('usage: node e2e/pitch_smoke.js <dir from make_pitch_video.py> [baseUrl] [screenshotDir]'); process.exit(2); }
fs.mkdirSync(shots, { recursive: true });
const truth = JSON.parse(fs.readFileSync(path.join(dir, 'landmarks.json')));
const check = (cond, msg) => { if (!cond) throw new Error('FAILED: ' + msg); console.log('  ok  ' + msg); };

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  try {
    await page.goto(base);
    await page.waitForSelector('form.auth');
    await page.locator('form.auth a', { hasText: 'Create an account' }).click();
    await page.fill('form.auth input[name=club]', 'Pitch Test GAA');
    await page.fill('form.auth input[name=email]', `pitch${Date.now()}@example.com`);
    await page.fill('form.auth input[name=password]', 'pitch-test-pw');
    await page.locator('form.auth button[type=submit]').click();
    await page.waitForSelector('.empty h2');

    await page.click('#new-match');
    const d = page.locator('dialog[open]');
    const t = d.locator('input[type=text]');
    await t.nth(0).fill('Home'); await t.nth(1).fill('Away'); await t.nth(2).fill('Calibration test');
    await d.locator('input[type=file]').setInputFiles(path.join(dir, 'pitch.mp4'));
    await d.locator('button.primary').click();
    await page.waitForSelector('#pitch-setup', { timeout: 60000 });

    await page.click('#pitch-setup');
    const dlg = page.locator('dialog[open]');
    await dlg.locator('.framebox img').evaluate((img) => img.complete && img.naturalWidth ? null : new Promise((r) => img.addEventListener('load', r, { once: true })));
    const canvas = dlg.locator('canvas.overlay');
    const box = await canvas.boundingBox();
    const ids = ['left.corner.near', 'right.corner.near', 'left.45m.far', 'right.45m.far', 'halfway.near', 'right.large rectangle.near.front'];
    const jitter = [[1.5, -2], [-2, 1], [2, 2], [-1, -1.5], [1, 2], [-2, -1]];
    for (const [i, id] of ids.entries()) {
      await dlg.locator('.lm', { hasText: await page.evaluate(async (lid) => {
        const m = location.hash.slice(1);
        const info = await (await fetch(`/api/matches/${m}/pitch`)).json();
        return info.landmarks.find((l) => l.id === lid).label;
      }, id) }).first().click();
      const [u, v] = truth.landmarks[id];
      await page.mouse.click(box.x + ((u + jitter[i][0]) / truth.width) * box.width, box.y + ((v + jitter[i][1]) / truth.height) * box.height);
    }
    check((await dlg.locator('.lm.set').count()) === 6, '6 landmarks placed by clicking the frame');
    await dlg.locator('#pitch-save').click();
    await page.waitForFunction(() => /Saved\. Fit error/.test(document.querySelector('dialog[open] .fitstatus').innerText));
    const status = await dlg.locator('.fitstatus').innerText();
    const rms = Number(status.match(/Fit error ([\d.]+) m/)[1]);
    check(rms < 1.0, `fit error ${rms} m RMS from 6 hand-placed points`);
    // Overlay drawn: some canvas pixels are the green line colour.
    const drawn = await canvas.evaluate((c) => {
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 0 && d[i + 1] > 150 && d[i] < 120) n++;
      return n;
    });
    check(drawn > 5000, `fitted pitch lines drawn over the frame (${drawn} px)`);
    await page.screenshot({ path: path.join(shots, 'pitch-setup.png') });

    // A point that wasn't clicked (far-side 13m line) maps to where it should, via the saved H.
    const mapped = await page.evaluate(async ([u, v]) => {
      const m = await (await fetch('/api/matches/' + location.hash.slice(1))).json();
      const H = m.pitch_setup.H, w = H[2][0] * u + H[2][1] * v + H[2][2];
      return [(H[0][0] * u + H[0][1] * v + H[0][2]) / w, (H[1][0] * u + H[1][1] * v + H[1][2]) / w];
    }, truth.landmarks['left.13m.far']);
    const off = Math.hypot(mapped[0] - 13, mapped[1] - 85);
    check(off < 1.5, `unclicked landmark (left 13m x far sideline) lands ${off.toFixed(2)} m from truth`);
    await dlg.locator('button', { hasText: 'Close' }).click();
    check(/Pitch set up ✓/.test(await page.locator('#pitch-setup').innerText()), 'header shows pitch is set up');
    check(errors.length === 0, 'no JS errors: ' + JSON.stringify(errors));
    console.log('\nPITCH SMOKE TEST PASSED');
  } catch (e) {
    await page.screenshot({ path: path.join(shots, 'pitch-failure.png') }).catch(() => {});
    console.error(e.message);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
