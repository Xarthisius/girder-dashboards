/*
 * Browser verification for girder-dashboards.
 *
 * Drives headless Chrome against a running Girder and asserts the four
 * requirements from TASK.md end to end: the sidebar entry, the card gallery, the
 * admin config page, and the layout-free dashboard runner. Also fails on any
 * console error, page error or failed request, in either an anonymous or an
 * admin session.
 *
 * Covers girder-dashboards itself. Dashboards shipped by other plugins bring
 * their own harness (see girder-dashboards-precipitate); this one only requires
 * that the built-in Data Overview dashboard is enabled, and adapts to whatever
 * else happens to be installed alongside it.
 *
 * Prerequisites: a Girder with this plugin loaded and its web_client built, an
 * admin account, and the Data Overview dashboard enabled. See CLAUDE.md.
 *
 *   node test/browser/verify.cjs
 *
 * Environment:
 *   GIRDER_URL       default http://127.0.0.1:8989
 *   GIRDER_ADMIN     default admin
 *   GIRDER_PASSWORD  default adminpassword
 *   SHOTS            screenshot output directory (default test/browser/screenshots)
 *   PLAYWRIGHT_PATH  where to resolve playwright from, if not installed here
 */
const fs = require('fs');
const path = require('path');

// This repo has no node dependency of its own, so fall back to the playwright in
// the sibling girder checkout, which ships it for the core client's own tests.
const PW_CANDIDATES = [
    process.env.PLAYWRIGHT_PATH,
    'playwright',
    path.resolve(__dirname, '../../../girder/girder/web/node_modules/playwright')
].filter(Boolean);

let chromium;
for (const candidate of PW_CANDIDATES) {
    try {
        ({ chromium } = require(candidate));
        break;
    } catch (e) { /* try the next candidate */ }
}
if (!chromium) {
    console.error(`Could not resolve playwright. Tried:\n  ${PW_CANDIDATES.join('\n  ')}`);
    console.error('Set PLAYWRIGHT_PATH, or `npm i -D playwright`.');
    process.exit(2);
}

const BASE = (process.env.GIRDER_URL || 'http://127.0.0.1:8989').replace(/\/$/, '');
const ADMIN = process.env.GIRDER_ADMIN || 'admin';
const PASSWORD = process.env.GIRDER_PASSWORD || 'adminpassword';
const SHOTS = process.env.SHOTS || path.resolve(__dirname, 'screenshots');
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
let failures = 0;

function check(name, ok, detail) {
    results.push({ name, ok: !!ok, detail });
    if (!ok) failures++;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  [${detail}]` : ''}`);
}

async function newPage(browser, token) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    if (token) {
        await context.addInitScript((t) => {
            window.localStorage.setItem('girderToken', t);
        }, token);
    }
    const page = await context.newPage();
    const problems = [];
    page.on('console', (msg) => {
        if (msg.type() === 'error') problems.push(`console: ${msg.text()}`);
    });
    page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`));
    page.on('requestfailed', (req) => problems.push(`requestfailed: ${req.url()}`));
    return { context, page, problems };
}

// Girder's Backbone router does not re-run a route when only the hash changes on
// an already-loaded page in some cases, so each step does a full goto.
async function go(page, hash, waitFor) {
    await page.goto(`${BASE}/${hash}`, { waitUntil: 'domcontentloaded' });
    if (waitFor) await page.waitForSelector(waitFor, { timeout: 15000 });
}

/** The gallery card for a named dashboard, whatever order the cards are in. */
function cardFor(page, title) {
    return page.locator('.g-dashboard-card', {
        has: page.locator('.g-dashboard-card-title', { hasText: title })
    });
}

/** The config-page row for a dashboard key. */
function configRowFor(page, key) {
    return page.locator('.g-dashboards-config-table tbody tr', {
        has: page.locator('code', { hasText: key })
    });
}

async function publicKeys() {
    return (await (await fetch(`${BASE}/api/v1/dashboard`)).json()).map((d) => d.key);
}

(async () => {
    // Log in via the API and hand the token to the browser through localStorage,
    // which is exactly where girder's auth.js keeps it.
    const loginResp = await fetch(`${BASE}/api/v1/user/authentication`, {
        headers: {
            Authorization: `Basic ${Buffer.from(`${ADMIN}:${PASSWORD}`).toString('base64')}`
        }
    });
    if (!loginResp.ok) {
        throw new Error(`admin login as "${ADMIN}" failed: ${loginResp.status}`);
    }
    const adminToken = (await loginResp.json()).authToken.token;

    const dashboards = await (await fetch(`${BASE}/api/v1/dashboard`)).json();
    if (!dashboards.length) {
        throw new Error('no enabled dashboard to test against — enable one first');
    }
    // Look dashboards up by key rather than by position: other plugins install
    // dashboards of their own, and the listing is sorted by name.
    const byKey = Object.fromEntries(dashboards.map((d) => [d.key, d]));
    if (!byKey['data-overview']) {
        throw new Error('dashboard "data-overview" is not enabled — run seed.py first');
    }
    const dashId = byKey['data-overview']._id;
    // Whatever else is installed and enabled. Used to prove the config page's
    // toggles act on one dashboard at a time; with nothing else there is no
    // isolation to demonstrate, and the check says so rather than passing quietly.
    const others = dashboards.filter((d) => d.key !== 'data-overview');
    const allIds = dashboards.map((d) => d._id);

    // Prefer the system Chrome: the browser build cached for playwright may not
    // match the installed playwright version. Either way this is a throwaway
    // profile, never the user's own Chrome session.
    let browser;
    try {
        browser = await chromium.launch({ channel: 'chrome' });
        console.log('browser: system chrome');
    } catch (e) {
        browser = await chromium.launch();
        console.log(`browser: bundled chromium (system chrome unavailable: ${e.message.split('\n')[0]})`);
    }
    console.log(`target:  ${BASE}\n`);

    // ---------------------------------------------------------------- anonymous
    {
        const { context, page, problems } = await newPage(browser);

        // REQ 1: sidebar entry
        await go(page, '', '.g-global-nav-li');
        const navLink = page.locator('.g-global-nav a[g-name="Dashboards"]');
        check('REQ1 sidebar has a Dashboards entry', await navLink.count() === 1);
        check('REQ1 sidebar entry targets #dashboards',
            (await navLink.getAttribute('href')) === '#dashboards',
            await navLink.getAttribute('href'));
        check('REQ1 sidebar entry has an icon',
            await page.locator('.g-global-nav a[g-name="Dashboards"] i.icon-gauge').count() === 1);

        // Navigate by clicking the sidebar, as a user would.
        await navLink.click();
        await page.waitForSelector('.g-dashboard-card', { timeout: 15000 });
        check('REQ1 clicking the sidebar entry routes to the list',
            page.url().endsWith('#dashboards'), page.url());
        check('REQ1 sidebar entry is highlighted as active',
            await page.locator('.g-global-nav-li.g-active a[g-name="Dashboards"]').count() === 1);

        check('REQ2 gallery shows a card per enabled dashboard',
            await page.locator('.g-dashboard-card').count() === dashboards.length,
            `${await page.locator('.g-dashboard-card').count()} cards`);

        // REQ 2: card contents
        const card = cardFor(page, 'Data Overview');
        check('REQ2a card shows an image',
            await card.locator('.g-dashboard-card-media img').count() === 1);
        const imgOk = await card.locator('.g-dashboard-card-media img').evaluate(
            (el) => el.complete && el.naturalWidth > 0);
        check('REQ2a card image actually loaded', imgOk);
        check('REQ2b card shows the name',
            (await card.locator('.g-dashboard-card-title').innerText()).trim() === 'Data Overview');
        const desc = (await card.locator('.g-dashboard-card-description').innerText()).trim();
        check('REQ2c card shows the description', desc.startsWith('At-a-glance counts'), desc.slice(0, 40));
        // The byline: a credit is only a credit if it is actually on screen, so
        // check the rendered text and that it occupies a box.
        const byline = card.locator('.g-dashboard-card-authors');
        check('card shows the authors', (await byline.innerText()).trim() === 'JHU/NCSA Data Team',
            (await byline.innerText()).trim());
        const bylineBox = await byline.boundingBox();
        check('author byline is visible on the card',
            await byline.isVisible() && bylineBox && bylineBox.height > 8,
            bylineBox && `${Math.round(bylineBox.width)}x${Math.round(bylineBox.height)}`);
        check('REQ2d card shows a run/open action',
            await card.locator('a.g-dashboard-run').count() === 1);
        check('REQ2d run action links to the dashboard',
            (await card.locator('a.g-dashboard-run').getAttribute('href')) === `#dashboard/${dashId}`);
        check('REQ2e no settings gear for a non-admin',
            await card.locator('a.g-dashboard-configure').count() === 0);
        check('config link hidden from non-admins',
            await page.locator('.g-dashboards-configure-link').count() === 0);

        await page.screenshot({ path: `${SHOTS}/01-gallery-anon.png`, fullPage: true });

        // Card geometry: catches a grid that collapsed or a zero-height image.
        const box = await card.boundingBox();
        check('REQ2 card has sane dimensions',
            box && box.width > 240 && box.height > 240,
            box && `${Math.round(box.width)}x${Math.round(box.height)}`);

        // REQ 4: running dashboard drops the standard layout
        await card.locator('a.g-dashboard-run').click();
        await page.waitForSelector('.g-dashboard-topbar', { timeout: 15000 });
        check('REQ4 opening routes to #dashboard/:id',
            page.url().endsWith(`#dashboard/${dashId}`), page.url());

        const chrome = await page.evaluate(() => {
            const vis = (sel) => {
                const el = document.querySelector(sel);
                if (!el) return 'missing';
                return getComputedStyle(el).display === 'none' ? 'hidden' : 'visible';
            };
            return {
                header: vis('#g-app-header-container'),
                nav: vis('#g-global-nav-container'),
                footer: vis('#g-app-footer-container'),
                bodyClass: document.querySelector('#g-app-body-container').className
            };
        });
        check('REQ4 girder header is hidden', chrome.header === 'hidden', chrome.header);
        check('REQ4 global nav is hidden', chrome.nav === 'hidden', chrome.nav);
        check('REQ4 footer is hidden', chrome.footer === 'hidden', chrome.footer);
        check('REQ4 body uses the empty layout',
            chrome.bodyClass.includes('g-empty-layout') && !chrome.bodyClass.includes('g-default-layout'),
            chrome.bodyClass);

        const back = page.locator('a.g-dashboard-back');
        check('REQ4 top navbar has a go-back link', await back.count() === 1);
        check('REQ4 go-back link points at the list',
            (await back.getAttribute('href')) === '#dashboards');
        const title = (await page.locator('.g-dashboard-topbar-title').innerText()).trim();
        check('REQ4 top navbar names the dashboard', title === 'Data Overview', title);
        check('REQ4 no settings gear in navbar for a non-admin',
            await page.locator('.g-dashboard-topbar a.g-dashboard-configure').count() === 0);

        // The dashboard view itself rendered, not just its chrome.
        await page.waitForSelector('.g-data-overview .g-overview-stat', { timeout: 15000 });
        const stats = await page.locator('.g-overview-stat').evaluateAll(
            (els) => els.map((el) => [
                el.querySelector('.g-overview-stat-label').textContent.trim(),
                el.querySelector('.g-overview-stat-value').textContent.trim()
            ]));
        // Anonymously, only the publicly-readable counts are offered: `GET /user`
        // is @access.user, so that tile is omitted rather than fetched and dashed.
        check('demo dashboard rendered its public stat tiles',
            stats.length === 2, JSON.stringify(stats));
        check('demo dashboard omits the sign-in-only Users tile',
            !stats.some(([label]) => label === 'Users'), JSON.stringify(stats));
        check('demo dashboard stat values are all real numbers',
            stats.every(([, v]) => /^[\d,]+$/.test(v)), JSON.stringify(stats));
        const rows = await page.locator('.g-overview-table tbody tr').count();
        check('demo dashboard listed collections', rows > 0, `${rows} rows`);

        await page.screenshot({ path: `${SHOTS}/02-runner-anon.png`, fullPage: true });

        // Back out: the standard layout must come back.
        await back.click();
        await page.waitForSelector('.g-dashboard-card', { timeout: 15000 });
        const restored = await page.evaluate(() => ({
            header: getComputedStyle(document.querySelector('#g-app-header-container')).display,
            nav: getComputedStyle(document.querySelector('#g-global-nav-container')).display,
            bodyClass: document.querySelector('#g-app-body-container').className
        }));
        check('REQ4 going back restores the header', restored.header !== 'none', restored.header);
        check('REQ4 going back restores the global nav', restored.nav !== 'none', restored.nav);
        check('REQ4 going back restores the default layout',
            restored.bodyClass.includes('g-default-layout'), restored.bodyClass);

        check('no console/page errors in the anonymous flow',
            problems.length === 0, problems.join(' | ').slice(0, 300));
        await context.close();
    }

    // -------------------------------------------------------------------- admin
    {
        const { context, page, problems } = await newPage(browser, adminToken);

        // A logged-in admin does get the Users tile, and it must be a real count.
        await go(page, `#dashboard/${dashId}`, '.g-data-overview .g-overview-stat');
        const adminStats = await page.locator('.g-overview-stat').evaluateAll(
            (els) => els.map((el) => [
                el.querySelector('.g-overview-stat-label').textContent.trim(),
                el.querySelector('.g-overview-stat-value').textContent.trim()
            ]));
        check('demo dashboard shows all three tiles to a signed-in user',
            adminStats.length === 3, JSON.stringify(adminStats));
        check('demo dashboard Users count resolves for a signed-in user',
            adminStats.some(([label, v]) => label === 'Users' && /^[\d,]+$/.test(v)),
            JSON.stringify(adminStats));
        check('REQ2e settings gear shown in the runner navbar for an admin',
            await page.locator('.g-dashboard-topbar a.g-dashboard-configure').count() === 1);
        await page.screenshot({ path: `${SHOTS}/08-runner-admin.png`, fullPage: true });

        await go(page, '#dashboards', '.g-dashboard-card');
        check('admin session is logged in',
            await page.locator('.g-current-user-text, a.g-user-text').count() > 0 ||
            await page.locator('.g-global-nav a[g-name="Admin console"]').count() === 1);

        // REQ 2e: gear for admins
        const card = cardFor(page, 'Data Overview');
        check('REQ2e settings gear shown to an admin',
            await card.locator('a.g-dashboard-configure').count() === 1);
        check('REQ2e gallery offers a link to the config page',
            await page.locator('.g-dashboards-configure-link').count() === 1);
        await page.screenshot({ path: `${SHOTS}/03-gallery-admin.png`, fullPage: true });

        // The gear opens the settings dialog.
        await card.locator('a.g-dashboard-configure').click();
        await page.waitForSelector('#g-dashboard-edit-form', { timeout: 15000 });
        check('REQ2e gear opens the settings dialog',
            await page.locator('#g-dashboard-edit-form').isVisible());
        check('dialog prefills the name',
            (await page.inputValue('#g-dashboard-name')) === 'Data Overview');
        const settingsJson = await page.inputValue('#g-dashboard-settings');
        check('dialog prefills the settings JSON',
            JSON.parse(settingsJson).collectionLimit === 10, settingsJson.replace(/\s+/g, ' '));
        check('dialog reflects the enabled state',
            await page.locator('#g-dashboard-enabled').isChecked());
        check('dialog prefills the authors, one per line',
            (await page.inputValue('#g-dashboard-authors')) === 'JHU/NCSA Data Team',
            await page.inputValue('#g-dashboard-authors'));
        // The dialog has enough fields to outgrow a short viewport; its title and
        // Save button must stay on screen regardless.
        await page.waitForTimeout(500);
        const modalFits = await page.evaluate(() => {
            const r = (sel) => document.querySelector(sel).getBoundingClientRect();
            const title = r('#g-dashboard-edit-form .modal-title');
            const save = r('#g-dashboard-edit-form button.g-save-dashboard');
            return {
                titleTop: Math.round(title.top),
                saveBottom: Math.round(save.bottom),
                vh: window.innerHeight
            };
        });
        check('dialog title stays within the viewport',
            modalFits.titleTop >= 0, JSON.stringify(modalFits));
        check('dialog Save button stays within the viewport',
            modalFits.saveBottom <= modalFits.vh, JSON.stringify(modalFits));
        await page.screenshot({ path: `${SHOTS}/04-settings-dialog.png` });

        // An admin owns the byline too: edit it, and check the *reloaded* gallery
        // shows the change (so this proves it persisted, not just re-rendered).
        await page.fill('#g-dashboard-authors', '  Ada Lovelace  \n\nGrace Hopper\n');
        await page.locator('#g-dashboard-edit-form button.g-save-dashboard').click();
        await page.waitForSelector('#g-dashboard-edit-form', { state: 'hidden', timeout: 15000 });
        await go(page, '#dashboards', '.g-dashboard-card');
        const edited = (await cardFor(page, 'Data Overview')
            .locator('.g-dashboard-card-authors').innerText()).trim();
        check('an admin can edit the authors', edited === 'Ada Lovelace, Grace Hopper', edited);

        // And "Reset to defaults" gives the declared authors back, which is also
        // what keeps this harness repeatable.
        await cardFor(page, 'Data Overview').locator('a.g-dashboard-configure').click();
        await page.waitForSelector('#g-dashboard-edit-form', { timeout: 15000 });
        await page.locator('#g-dashboard-edit-form a.g-dashboard-reset').click();
        await page.waitForSelector('#g-dashboard-edit-form', { state: 'hidden', timeout: 15000 });
        await go(page, '#dashboards', '.g-dashboard-card');
        const restored = (await cardFor(page, 'Data Overview')
            .locator('.g-dashboard-card-authors').innerText()).trim();
        check('resetting to defaults restores the declared authors',
            restored === 'JHU/NCSA Data Team', restored);

        // REQ 3: config page
        await go(page, '#plugins/dashboards/config', '.g-dashboards-config-table');
        const row = configRowFor(page, 'data-overview');
        check('REQ3 config page lists every registered dashboard',
            await page.locator('.g-dashboards-config-table tbody tr').count() === dashboards.length,
            `${await page.locator('.g-dashboards-config-table tbody tr').count()} rows`);
        check('REQ3 config row shows the key',
            (await row.locator('code').innerText()).trim() === 'data-overview');
        check('config row shows the authors',
            (await row.locator('.g-dashboard-config-authors').innerText()).trim() ===
                'JHU/NCSA Data Team',
            (await row.locator('.g-dashboard-config-authors').innerText()).trim());
        check('REQ3 config row has an enable toggle',
            await row.locator('input.g-dashboard-enabled-toggle').count() === 1);
        check('REQ3 toggle reflects the enabled state',
            await row.locator('input.g-dashboard-enabled-toggle').isChecked());
        check('REQ3 config row has edit and access actions',
            await row.locator('a.g-dashboard-edit').count() === 1 &&
            await row.locator('a.g-dashboard-access').count() === 1);
        check('REQ3 breadcrumb rendered',
            await page.locator('.g-config-breadcrumb-container').innerText() !== '');
        await page.screenshot({ path: `${SHOTS}/05-config-page.png`, fullPage: true });

        // REQ3 behaviour: the toggles are per dashboard, and disabling one really
        // takes that one — and only that one — out of the gallery.
        await row.locator('input.g-dashboard-enabled-toggle').uncheck();
        await page.waitForFunction(async () => {
            const r = await fetch('/api/v1/dashboard');
            return !(await r.json()).some((d) => d.key === 'data-overview');
        }, null, { timeout: 15000 });
        const stillPublic = await publicKeys();
        check('REQ3 disabling one removes it from the public listing',
            !stillPublic.includes('data-overview'), stillPublic.join(','));
        if (others.length) {
            check('REQ3 disabling one leaves the others enabled',
                others.every((d) => stillPublic.includes(d.key)), stillPublic.join(','));
        } else {
            console.log('SKIP  REQ3 disabling one leaves the others enabled  ' +
                '[only one dashboard installed — install another plugin to cover this]');
        }

        // With everything off, the gallery has to say so rather than render blank.
        for (const other of others) {
            await configRowFor(page, other.key)
                .locator('input.g-dashboard-enabled-toggle').uncheck();
        }
        await page.waitForFunction(async () => {
            const r = await fetch('/api/v1/dashboard');
            return (await r.json()).length === 0;
        }, null, { timeout: 15000 });

        await go(page, '#dashboards', '.g-dashboards-empty');
        check('REQ3 gallery shows the empty state when nothing is enabled',
            (await page.locator('.g-dashboards-empty').innerText()).includes('No dashboards are enabled'));
        await page.screenshot({ path: `${SHOTS}/06-gallery-empty.png`, fullPage: true });

        // The access dialog (ACL editor) opens.
        await go(page, '#plugins/dashboards/config', '.g-dashboards-config-table');
        await page.locator('.g-dashboards-config-table tbody tr').first()
            .locator('a.g-dashboard-access').click();
        await page.waitForSelector('.g-public-container, .modal-title', { timeout: 15000 });
        const aclTitle = await page.locator('.modal-title').innerText();
        check('REQ2e/3 access dialog opens', /access/i.test(aclTitle), aclTitle.trim());
        await page.screenshot({ path: `${SHOTS}/07-access-dialog.png` });

        // Restore the enabled state so the environment is left as found — this
        // harness has to be repeatable, and other plugins' harnesses run after it.
        await page.evaluate(async (ids) => {
            for (const id of ids) {
                await fetch(`/api/v1/dashboard/${id}`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Girder-Token': window.localStorage.getItem('girderToken')
                    },
                    body: 'enabled=true'
                });
            }
        }, allIds);
        const finalState = await publicKeys();
        check('re-enabled the dashboards for a clean finish',
            finalState.length === dashboards.length, finalState.join(','));

        check('no console/page errors in the admin flow',
            problems.length === 0, problems.join(' | ').slice(0, 300));
        await context.close();
    }

    await browser.close();

    console.log(`\n${results.length - failures}/${results.length} checks passed`);
    if (failures) {
        console.log('\nFAILURES:');
        results.filter((r) => !r.ok).forEach((r) => console.log(`  - ${r.name} [${r.detail}]`));
    }
    process.exit(failures ? 1 : 0);
})().catch((err) => {
    console.error('HARNESS ERROR:', err);
    process.exit(2);
});
