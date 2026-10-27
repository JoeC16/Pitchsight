// Headless browser smoke test of the UI against a running server.
//
//   node e2e/ui_smoke.js <video.mp4> [baseUrl] [screenshotDir]
//
// Walks the whole coach flow: create match + upload, wait for processing, filter chips,
// timeline, reel playback, edit an event, manual tag, clip download. Exits non-zero on
// the first failed check. Needs `playwright` (pre-installed in the dev container).
'use strict';
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const [video, base = 'http://localhost:8000', shots = 'e2e/screenshots'] = process.argv.slice(2);
if (!video) { console.error('usage: node e2e/ui_smoke.js <video.mp4> [baseUrl] [screenshotDir]'); process.exit(2); }
fs.mkdirSync(shots, { recursive: true });

let step = 0;
function check(cond, msg) {
  if (!cond) throw new Error('FAILED: ' + msg);
  console.log('  ok  ' + msg);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, acceptDownloads: true });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  const snap = async (name) => page.screenshot({ path: path.join(shots, `${String(++step).padStart(2, '0')}-${name}.png`) });

  try {
    await page.goto(base);
    await page.waitForSelector('.empty h2, .match-item');
    await snap('home');

    // --- create match + upload
    await page.click('#new-match');
    const dlg = page.locator('dialog[open]');
    await dlg.locator('select').first().selectOption('gaa_football');
    const inputs = dlg.locator('input[type=text]');
    await inputs.nth(0).fill('St Mary GAC');
    await inputs.nth(1).fill('Ballyboden');
    await inputs.nth(2).fill('UI smoke test');
    await dlg.locator('input[type=file]').setInputFiles(video);
    await snap('upload-dialog');
    await dlg.locator('button.primary').click();
    await page.waitForSelector('dialog[open]', { state: 'detached', timeout: 120000 });
    check(true, 'upload dialog submitted and closed');

    // --- processing -> ready (UI polls every 2s)
    await page.waitForSelector('.ev', { timeout: 60000 });
    const banner = await page.locator('#banner').innerText();
    check(/Demo data/.test(banner), 'demo-data banner shown for stub events');
    const total = await page.locator('.ev').count();
    check(total > 50, `event list populated (${total} events)`);
    check(await page.locator('#timeline .mk').count() === total, 'timeline has one marker per event');
    check(/\d+-\d+ \(\d+\)/.test(await page.locator('.scoreline').innerText()), 'GAA scoreline rendered');
    check(await page.locator('.match-item').count() >= 1, 'match listed in sidebar');
    check(!/\bnull\b|undefined/.test(await page.locator('body').innerText()), 'no stray "null"/"undefined" text rendered');
    await snap('ready');

    // --- video can play in this browser
    const canPlay = await page.evaluate(() => {
      const v = document.querySelector('#video');
      return { mp4: v.canPlayType('video/mp4; codecs="avc1.42E01E, mp4a.40.2"'), ready: v.readyState };
    });
    console.log('  info canPlayType(h264/aac) =', JSON.stringify(canPlay.mp4));

    // --- filters
    await page.locator('#filters .chip', { hasText: 'Shot' }).click();
    await page.waitForFunction((n) => document.querySelectorAll('.ev').length < n, total);
    const shotRows = await page.locator('.ev .badge').allInnerTexts();
    check(shotRows.length > 0 && shotRows.every((t) => t === 'Shot'), `type filter: ${shotRows.length} shots only`);
    check(await page.locator('#filters .ftitle', { hasText: 'Outcome' }).count() === 1, 'attribute chips appear for selected type');
    await page.locator('#filters .chip', { hasText: /^point$/ }).click();
    await page.waitForFunction((n) => document.querySelectorAll('.ev').length < n, shotRows.length);
    const pointAttrs = await page.locator('.ev .attrs').allInnerTexts();
    check(pointAttrs.every((t) => t.split(' · ').includes('point')), `attribute filter: ${pointAttrs.length} points`);
    const dimmed = await page.locator('#timeline .mk.dim').count();
    check(dimmed === total - pointAttrs.length, 'timeline dims markers outside the filter');
    await page.locator('#filters .chip', { hasText: 'St Mary GAC' }).click();
    await page.waitForTimeout(300);
    const teams = await page.locator('.ev .line1 > span:nth-child(2)').allInnerTexts();
    check(teams.every((t) => t === 'St Mary GAC'), `team filter: ${teams.length} home points`);
    await snap('filtered');
    await page.locator('#filters button', { hasText: 'Reset filters' }).click();
    await page.waitForFunction((n) => document.querySelectorAll('.ev').length === n, total);
    check(true, 'reset filters restores all events');

    // --- timeline click seeks the video
    const tl = page.locator('#timeline');
    const box = await tl.boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    const t = await page.evaluate(() => document.querySelector('#video').currentTime);
    const dur = await page.evaluate(() => document.querySelector('#video').duration);
    check(Math.abs(t - dur / 2) < dur * 0.05, `timeline click seeks to middle (t=${t.toFixed(1)}s of ${dur.toFixed(0)}s)`);

    // --- reel playback (filter to kickouts, play, verify it advances between items)
    await page.locator('#filters .chip', { hasText: 'Kickout' }).click();
    await page.waitForFunction((n) => document.querySelectorAll('.ev').length < n, total);
    await page.locator('#listbar button', { hasText: 'Play reel' }).click();
    await page.waitForSelector('.ev.playing');
    const first = await page.locator('.ev.playing').getAttribute('id');
    const playing = await page.evaluate(() => !document.querySelector('#video').paused);
    check(playing, 'reel starts playback');
    await snap('reel');
    // Fast-forward to just before the end of the first item's window to trigger the advance.
    await page.evaluate(() => { const v = document.querySelector('#video'); v.playbackRate = 4; });
    await page.waitForFunction((id) => { const p = document.querySelector('.ev.playing'); return p && p.id !== id; }, first, { timeout: 30000 });
    const second = await page.locator('.ev.playing').getAttribute('id');
    check(second !== first, `reel advanced ${first} -> ${second}`);
    await page.locator('#listbar button', { hasText: 'Stop reel' }).click();
    check(await page.locator('.ev.playing').count() === 0, 'stop reel');
    await page.locator('#filters button', { hasText: 'Reset filters' }).click();
    await page.waitForFunction((n) => document.querySelectorAll('.ev').length === n, total);

    // --- edit an event
    const row = page.locator('.ev').first();
    const rowId = await row.getAttribute('id');
    await row.locator('button[title=Edit]').click();
    const ed = page.locator('dialog[open]');
    await ed.locator('select').nth(1).selectOption('away');
    await snap('edit-dialog');
    await ed.locator('button', { hasText: 'Save' }).click();
    await page.waitForSelector('dialog[open]', { state: 'detached' });
    const edited = page.locator('#' + rowId);
    check(/reviewed/.test(await edited.innerText()) && /Ballyboden/.test(await edited.innerText()), 'edit saved: team changed, marked reviewed');

    // --- manual tag
    await page.evaluate(() => { document.querySelector('#video').currentTime = 42; });
    await page.locator('#listbar button', { hasText: '+ Tag' }).click();
    const tag = page.locator('dialog[open]');
    await tag.locator('select').nth(0).selectOption('free');
    await tag.locator('select').nth(3).selectOption('over_carry');
    await tag.locator('button', { hasText: 'Save' }).click();
    await page.waitForSelector('dialog[open]', { state: 'detached' });
    check(await page.locator('.ev').count() === total + 1, 'manual tag added to list');
    const freeRows = await page.locator('.ev', { hasText: 'over carry' }).allInnerTexts();
    check(freeRows.some((r) => r.startsWith('0:42') && /reviewed/.test(r)), 'manual tag at 0:42, reviewed');
    await snap('tagged');

    // --- invalid edit is rejected with a visible error (t_end < t_start)
    await page.locator('.ev').first().locator('button[title=Edit]').click();
    const bad = page.locator('dialog[open]');
    await bad.locator('input[type=number]').nth(1).fill('0');
    await bad.locator('input[type=number]').nth(0).fill('50');
    await bad.locator('button', { hasText: 'Save' }).click();
    await page.waitForTimeout(300);
    check((await bad.locator('.err').innerText()).length > 0, 'invalid edit shows an error and keeps the dialog open');
    await bad.locator('button', { hasText: 'Cancel' }).click();
    // That 422 is expected; the browser logs it as a console error, so drop it.
    const i = errors.findIndex((e) => /status of 422/.test(e));
    if (i >= 0) errors.splice(i, 1);

    // --- clip download
    const [dl] = await Promise.all([page.waitForEvent('download'), page.locator('.ev a[title="Download clip"]').first().click()]);
    const out = path.join(shots, 'clip.mp4');
    await dl.saveAs(out);
    const size = fs.statSync(out).size;
    check(size > 10000 && dl.suggestedFilename().endsWith('.mp4'), `clip downloaded: ${dl.suggestedFilename()} (${size} bytes)`);

    check(errors.length === 0, 'no JS errors in console: ' + JSON.stringify(errors));
    console.log('\nUI SMOKE TEST PASSED');
  } catch (e) {
    await snap('failure').catch(() => {});
    console.error(e.message);
    if (errors.length) console.error('page errors:', errors);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
