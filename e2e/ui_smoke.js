// Headless browser smoke test of the UI against a running server.
//
//   node e2e/ui_smoke.js <video.mp4> [baseUrl] [screenshotDir] [unfriendly.mkv]
//
// With the optional 4th argument (a file browsers can't play well, e.g. mpeg4 in .mkv) it also
// checks the playback-proxy flow.
// Walks the whole coach flow: create match + upload, wait for processing, filter chips,
// timeline, reel playback, edit an event, manual tag, clip download. Exits non-zero on
// the first failed check. Needs `playwright` (pre-installed in the dev container).
'use strict';
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const [video, base = 'http://localhost:8000', shots = 'e2e/screenshots', proxyVideo] = process.argv.slice(2);
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
    // --- register a new club (each run gets its own, so runs don't see each other's matches)
    const email = `coach${Date.now()}@example.com`, password = 'smoke-test-pw';
    await page.goto(base);
    await page.waitForSelector('form.auth');
    check(await page.locator('.sidebar').isHidden(), 'signed out: login form shown, sidebar hidden');
    await snap('login');
    await page.locator('form.auth a', { hasText: 'Create an account' }).click();
    await page.fill('form.auth input[name=club]', 'St Mary GAC');
    await page.fill('form.auth input[name=email]', email);
    await page.fill('form.auth input[name=password]', password);
    await page.locator('form.auth button[type=submit]').click();
    await page.waitForSelector('.empty h2');
    check(/St Mary GAC/.test(await page.locator('#user-box').innerText()), 'registered and signed in; club shown in top bar');
    await snap('home');
    errors.length = 0; // the signed-out probe of /api/auth/me logs an expected 401

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
    await page.waitForFunction((id) => /reviewed/.test(document.getElementById(id).innerText), rowId);
    check(/reviewed/.test(await edited.innerText()) && /Ballyboden/.test(await edited.innerText()), 'edit saved: team changed, marked reviewed');

    // --- manual tag
    await page.evaluate(() => { document.querySelector('#video').currentTime = 42; });
    await page.locator('#listbar button', { hasText: '+ Tag' }).click();
    const tag = page.locator('dialog[open]');
    await tag.locator('select').nth(0).selectOption('free');
    await tag.locator('select').nth(3).selectOption('over_carry');
    await tag.locator('button', { hasText: 'Save' }).click();
    await page.waitForSelector('dialog[open]', { state: 'detached' });
    await page.waitForFunction((n) => document.querySelectorAll('.ev').length === n, total + 1);
    check(true, 'manual tag added to list');
    const freeRows = await page.locator('.ev', { hasText: 'over carry' }).allInnerTexts();
    check(freeRows.some((r) => r.startsWith('0:42') && /reviewed/.test(r)), 'manual tag at 0:42, reviewed');
    await snap('tagged');

    // --- reel export: filter to home kickouts, export, wait, download
    await page.locator('#filters .chip', { hasText: 'Kickout' }).click();
    await page.locator('#filters .chip', { hasText: 'St Mary GAC' }).click();
    await page.waitForTimeout(300);
    const nKick = await page.locator('.ev').count();
    await page.locator('#listbar button', { hasText: 'Export' }).click();
    await page.waitForSelector('.exportbox');
    check(new RegExp(`(Building reel of |Reel ready: )${nKick} events`).test(await page.locator('.exportbox').innerText()), `export started for the ${nKick} filtered events`);
    await page.waitForSelector('.exportbox.ready', { timeout: 120000 });
    await snap('export-ready');
    const [reelDl] = await Promise.all([page.waitForEvent('download'), page.locator('.exportbox a', { hasText: 'Download' }).click()]);
    const reelOut = path.join(shots, 'reel.mp4');
    await reelDl.saveAs(reelOut);
    check(fs.statSync(reelOut).size > 100000, `reel downloaded: ${reelDl.suggestedFilename()} (${fs.statSync(reelOut).size} bytes)`);
    await page.locator('.exportbox button[title=Dismiss]').click();
    await page.locator('#filters button', { hasText: 'Reset filters' }).click();
    await page.waitForFunction((n) => document.querySelectorAll('.ev').length === n, total + 1);

    // --- review queue, keyboard only
    const reviewedCount = async () => Number((await page.locator('#mhead .sub').innerText()).match(/(\d+) reviewed/)[1]);
    const r0 = await reviewedCount();
    const btn = page.locator('#listbar button', { hasText: /^Review \(/ });
    const queueLen = Number((await btn.innerText()).match(/\((\d+)\)/)[1]);
    await btn.click();
    await page.waitForSelector('#review');
    const pos = async () => (await page.locator('#listbar').innerText()).match(/Reviewing (\d+) of (\d+)/).slice(1).map(Number);
    check(JSON.stringify(await pos()) === JSON.stringify([1, queueLen]), `review queue opened: 1 of ${queueLen}`);
    const confOf = async () => Number((await page.locator('#review .tag').first().innerText()).match(/(\d+)%/)[1]);
    const c1 = await confOf();
    const mid = page.url().match(/#(\d+)/)[1];
    const minConf = await page.evaluate(async (id) => {
      const evs = await (await fetch(`/api/matches/${id}/events?reviewed=false`)).json();
      return Math.round(Math.min(...evs.map((e) => e.confidence)) * 100);
    }, mid);
    check(c1 === minConf, `least confident event first (${c1}%)`);
    const tStart = await page.evaluate(() => document.querySelector('#video').currentTime);
    check(await page.evaluate(() => !document.querySelector('#video').paused), `review plays the event (from ${tStart.toFixed(1)}s)`);
    const card = await page.locator('#review').innerText();
    check(!/\[object|undefined|\bnull\b/.test(card) && /Outcome|Kind|Reason/.test(card), 'review card shows attribute labels and values');
    await snap('review');

    await page.keyboard.press('ArrowRight');
    check((await pos())[0] === 2, 'ArrowRight -> next');
    await page.keyboard.press('ArrowLeft');
    check((await pos())[0] === 1, 'ArrowLeft -> prev');

    await page.keyboard.press('Enter');
    await page.waitForFunction((n) => /of (\d+)/.exec(document.querySelector('#listbar').innerText)[1] == n, queueLen - 1);
    check(await reviewedCount() === r0 + 1, 'Enter confirms: queue -1, reviewed +1');
    check(await confOf() >= c1, 'queue stays sorted by confidence after confirming');

    await page.keyboard.press('d');
    check(/Press D again/.test(await page.locator('#review').innerText()), 'D once arms delete (nothing deleted yet)');
    check((await pos())[1] === queueLen - 1, 'queue unchanged after one D');
    await page.keyboard.press('d');
    await page.waitForFunction((n) => /of (\d+)/.exec(document.querySelector('#listbar').innerText)[1] == n, queueLen - 2);
    check(/(\d+) events/.exec(await page.locator('#mhead .sub').innerText())[1] == total, 'D twice deletes the event');

    await page.keyboard.press('e');
    const red = page.locator('dialog[open]');
    await red.waitFor();
    check(/Edit event/.test(await red.innerText()), 'E opens the editor');
    await page.keyboard.press('Enter'); // keys go to the dialog, not the queue
    check((await pos())[1] === queueLen - 2, 'review shortcuts are ignored while the editor is open');
    await red.locator('select').nth(1).selectOption('');
    await red.locator('button', { hasText: 'Save' }).click();
    await page.waitForFunction((n) => /of (\d+)/.exec(document.querySelector('#listbar').innerText)[1] == n, queueLen - 3);
    check(await reviewedCount() === r0 + 2, 'saving an edit marks it reviewed and advances');

    await page.keyboard.press('Escape');
    await page.waitForSelector('#review', { state: 'detached' });
    check(await page.locator('.ev').count() === total, 'Esc exits review back to the list');

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
    check(size > 10000 && dl.suggestedFilename().endsWith('_DEMO-simulated-event.mp4'), `clip downloaded: ${dl.suggestedFilename()} (${size} bytes)`);

    // --- club dialog: admin adds a coach
    await page.locator('#user-box button', { hasText: 'St Mary GAC' }).click();
    const club = page.locator('dialog[open]');
    await club.locator('input[type=email]').fill(`assistant${Date.now()}@example.com`);
    await club.locator('input[type=text]').fill('temp-password');
    await club.locator('button[type=submit]').click();
    await page.waitForFunction(() => document.querySelectorAll('dialog[open] .member').length === 2);
    check(true, 'admin added a coach to the club');
    await club.locator('button', { hasText: 'Close' }).click();

    // --- sign out, then back in: the match is still there
    await page.locator('#user-box button', { hasText: 'Sign out' }).click();
    await page.waitForSelector('form.auth');
    const blocked = await page.evaluate(async () => (await fetch('/api/matches')).status);
    check(blocked === 401, 'signed out: API refuses requests (401)');
    errors.splice(errors.findIndex((e) => /status of 401/.test(e)), 1);
    await page.fill('form.auth input[name=email]', email);
    await page.fill('form.auth input[name=password]', password);
    await page.locator('form.auth button[type=submit]').click();
    await page.waitForSelector('.match-item');
    check(await page.locator('.match-item', { hasText: 'UI smoke test' }).count() === 1, 'signed back in: match still listed');

    if (proxyVideo) {
      // --- playback proxy: upload a browser-unfriendly file
      await page.click('#new-match');
      const pd = page.locator('dialog[open]');
      const pin = pd.locator('input[type=text]');
      await pin.nth(0).fill('Home'); await pin.nth(1).fill('Away'); await pin.nth(2).fill('Proxy test');
      await pd.locator('input[type=file]').setInputFiles(proxyVideo);
      await pd.locator('button.primary').click();
      await page.waitForSelector('dialog[open]', { state: 'detached', timeout: 120000 });
      await page.waitForFunction(() => /Preparing smooth playback/.test(document.querySelector('#banner').innerText), null, { timeout: 60000 });
      check(true, 'proxy banner shown while the browser-friendly copy is built');
      await snap('proxy-pending');
      await page.waitForFunction(() => !/Preparing smooth playback/.test(document.querySelector('#banner').innerText), null, { timeout: 300000 });
      const mid2 = page.url().match(/#(\d+)/)[1];
      const pm = await page.evaluate(async (id) => (await fetch('/api/matches/' + id)).json(), mid2);
      check(pm.proxy_status === 'ready' && pm.video_codec === 'mpeg4', `proxy ready for ${pm.video_codec} upload (${pm.proxy_reason})`);
      await page.evaluate(() => { const v = document.querySelector('#video'); v.muted = true; return v.play(); });
      await page.waitForFunction(() => document.querySelector('#video').currentTime > 0.5, null, { timeout: 20000 });
      const vw = await page.evaluate(() => document.querySelector('#video').videoWidth);
      check(vw === 1280, `proxy plays in the browser (${vw}px wide)`);
    }

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
