/**
 * Red flag alert backend - Google Apps Script web app.
 *
 * "Did you just nuke SREday London 2026 Q3?" (Marek 2026-09-21). Every talks.csv change is a GitHub web upload;
 * when the wrong file lands in the wrong event folder a single push swaps a whole lineup. The build step
 * "Red flag check" (_build/redflag.py, push builds only, all four repos) detects that and posts the FACTS here:
 *   { token, brand, kind: 'swap'|'removal'|'twin'|'path', event_name, folder, suspect_name, identical,
 *     overlap_pct, gone, before_n, added, after_n, gone_names[], added_names[], sha, when, author, path,
 *     commit_url, file_url, event_url, status_url, test }
 * This script owns the wording (composeAlert below) and emails Mark from the brand alias, then files the thread
 * in the Inbox as unread + important under the "Red flags" label.
 *
 * Abuse guards (the /exec URL is public): token checked against the Script Property REDFLAG_TOKEN; 3 wrong
 * attempts -> locked 15 min, 10 -> locked 24 h (a correct token resets the counter; clear a lock by deleting
 * the REDFLAG_LOCK property); 20 alerts a day; one alert per commit (re-running a workflow never sends twice);
 * recipient, sender and template are pinned here and every field is length-limited, links must point at
 * github.com or the brand's own site, so a leaked token can only send THIS email to Mark.
 *
 * Deploy (one-time, from the Google account that sends as mark@sreday.com / mark@llmday.com / mark@platformday.com):
 *   1. https://script.google.com -> New project -> paste this file -> save as "Red flag alert".
 *   2. Project Settings (gear) -> Script properties -> add REDFLAG_TOKEN = <a long random string>.
 *   3. Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone.
 *   4. Authorise the Gmail scope when prompted, copy the .../exec URL.
 *   5. GitHub -> each of sreday, llmday, platformday, 2026.promptengineering.rocks -> Settings -> Secrets and
 *      variables -> Actions -> add REDFLAG_URL (the /exec URL) and REDFLAG_TOKEN (the same string as step 2).
 *   6. Check: open the /exec URL (token_set must be true), then run `python _build/redflag.py --test` with the
 *      two values in the environment, or run testAlert() here in the editor.
 *   Re-deploy after editing: Deploy -> Manage deployments -> edit -> new version (the URL stays the same).
 */

var BRANDS = {
  sreday:      { to: 'mark@sreday.com',      from: 'mark@sreday.com',      site: 'sreday.com' },
  llmday:      { to: 'mark@llmday.com',      from: 'mark@llmday.com',      site: 'llmday.com' },
  platformday: { to: 'mark@platformday.com', from: 'mark@platformday.com', site: 'platformday.com' },
  // PEC: no mark@promptengineering.rocks alias exists, so it uses the LLMday alias
  pec:         { to: 'mark@llmday.com',      from: 'mark@llmday.com',      site: 'promptengineering.rocks' }
};
var SENDER_NAME = 'SREday red flag';
var ALERT_LABEL = 'Red flags';
var DAILY_MAX = 20;
var SEEN_DAYS = 30;                                  // how long a commit id is remembered for the duplicate check
var LOCK_STEPS = { 3: 15 * 60, 10: 24 * 60 * 60 };   // failed attempts -> lock seconds
var KINDS = { swap: 1, removal: 1, twin: 1, path: 1 };

function doGet() {
  // Health check. Never reveals the token, only whether one is configured and whether the endpoint is locked.
  var lock = readJson('REDFLAG_LOCK');
  return respond({ ok: true, service: 'red-flag-alert', token_set: !!expectedToken(),
                   failed_attempts: lock.count || 0, locked_for: currentLock(),
                   sent_today: (readJson('REDFLAG_DAILY').sends || 0), version: 1 });
}

function doPost(e) {
  var data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (err) {
    return respond({ ok: false, error: 'bad json' });
  }

  var lock = currentLock();
  if (lock > 0) return respond({ ok: false, error: 'locked', retry_in: lock });
  if (!authorized(data.token)) {
    var retry = registerFailure();
    return retry > 0 ? respond({ ok: false, error: 'locked', retry_in: retry }) : respond({ ok: false, error: 'unauthorized' });
  }
  resetFailures();                                             // a correct token clears the ladder

  var brand = BRANDS[String(data.brand || '').toLowerCase()];
  if (!brand) return respond({ ok: false, error: 'bad brand' });
  var flag = normalizeFlag(data, brand);
  if (!flag.event_name || !KINDS[flag.kind]) return respond({ ok: false, error: 'bad flag' });

  var key = flag.brand + ':' + flag.kind + ':' + flag.folder + ':' + flag.sha;
  if (alreadySent(key)) return respond({ ok: true, duplicate: true });
  if (!dailyBudget()) return respond({ ok: false, error: 'too many today' });

  var mail = composeAlert(flag);
  var from = GmailApp.getAliases().indexOf(brand.from) !== -1 ? brand.from : Session.getEffectiveUser().getEmail();
  var options = { name: SENDER_NAME };
  if (from === brand.from) options.from = brand.from;
  var message = GmailApp.createDraft(brand.to, mail.subject, mail.text, options).send();
  fileThread(message);
  remember(key);
  Logger.log('Red flag sent: %s [%s] %s', flag.event_name, flag.kind, flag.sha);
  return respond({ ok: true, sent: true, subject: mail.subject });
}

// Pull the sent message's thread into the Inbox (unread, important, labelled) - a mail sent from this very
// account would otherwise sit read in "Sent" only.
function fileThread(message) {
  try {
    var thread = message.getThread();
    thread.moveToInbox();
    thread.markUnread();
    thread.markImportant();
    var label = GmailApp.getUserLabelByName(ALERT_LABEL) || GmailApp.createLabel(ALERT_LABEL);
    thread.addLabel(label);
  } catch (err) {
    Logger.log('Sent, but could not file the thread: ' + err);
  }
}

// ---- the one email ----------------------------------------------------------------
// Plain text, Mark's voice. Subject is Marek's wording (2026-09-21) for every kind of flag.

function composeAlert(f) {
  var subject = (f.test ? 'TEST - ' : '') + 'Did you just nuke ' + f.event_name + '?';
  var lines = ['Hey Mark,', ''];
  if (f.test) lines.push('This is a test of the red flag alert, nothing happened to the website.', '');

  if (f.kind === 'path') {
    lines.push('A talks.csv for ' + f.event_name + ' was uploaded outside the _db folder, so the website ignores it:',
               f.path, '',
               'The lineup on the website did not change. Please upload the file again into ' + f.folder + '/_db/ and delete the stray copy.');
  } else if (f.kind === 'twin') {
    lines.push('It looks like ' + f.event_name + ' has the lineup of ' + f.suspect_name + ', please check.', '',
               (f.identical ? 'The two talks.csv files are identical.' : f.overlap_pct + '% of the ' + f.after_n + ' speakers are the same in both events.'));
  } else {
    if (f.suspect_name) {
      lines.push('It looks like ' + f.event_name + ' has the lineup of ' + f.suspect_name + ', please check.');
    } else if (f.kind === 'removal') {
      lines.push('It looks like ' + f.event_name + ' just lost most of its lineup, please check.');
    } else {
      lines.push('It looks like the whole lineup of ' + f.event_name + ' was replaced in one upload, please check.');
    }
    lines.push('');
    var what = f.gone + ' of ' + f.before_n + ' speakers disappeared';
    if (f.added) what += ' and ' + f.added + ' new ones came in';
    lines.push(what + ' in a single upload' + (f.when ? ' on ' + f.when : '') + (f.author ? ' by ' + f.author : '') + '.');
    if (f.suspect_name) {
      lines.push(f.identical ? 'The file is an exact copy of the ' + f.suspect_name + ' talks.csv.'
                             : f.overlap_pct + '% of the new lineup matches ' + f.suspect_name + '.');
    }
    if (f.gone_names.length) lines.push('', 'Gone: ' + f.gone_names.join(', ') + (f.gone > f.gone_names.length ? ', ...' : ''));
    if (f.added_names.length) lines.push('New: ' + f.added_names.join(', ') + (f.added > f.added_names.length ? ', ...' : ''));
  }

  lines.push('');
  if (f.commit_url) lines.push('The upload: ' + f.commit_url);
  if (f.file_url) lines.push('File history: ' + f.file_url);
  if (f.event_url) lines.push('Live page: ' + f.event_url);
  if (f.status_url) lines.push('Status page: ' + f.status_url);
  if (f.kind !== 'path') {
    lines.push('', 'To fix it, upload the right talks.csv into ' + f.folder + '/_db/ again, or revert the upload on GitHub.',
               'If this was intentional, ignore this email. The red bar on the status page goes away when you add',
               f.sha.slice(0, 7) + ' to redflag_ack in home/metadata.yml.');
  }
  lines.push('', 'Best,', 'The build');
  return { subject: subject, text: lines.join('\n') };
}

// ---- input hygiene -----------------------------------------------------------------

function normalizeFlag(d, brand) {
  var key = String(d.brand || '').toLowerCase();
  function names(list) {
    var out = [];
    (Array.isArray(list) ? list : []).slice(0, 5).forEach(function (n) { n = clean(n, 80); if (n) out.push(n); });
    return out;
  }
  function link(v, hosts) {
    v = clean(v, 300);
    var m = /^https:\/\/([^\/?#]+)(\/[^\s]*)?$/.exec(v);
    if (!m) return '';
    var host = m[1].toLowerCase().replace(/^www\./, '');
    return hosts.indexOf(host) !== -1 ? v : '';
  }
  function count(v) { v = parseInt(v, 10); return v > 0 && v < 100000 ? v : 0; }
  return {
    brand: key, test: d.test === true,
    kind: clean(d.kind, 12), folder: clean(d.folder, 60).replace(/[^\w.-]/g, ''), path: clean(d.path, 160),
    event_name: clean(d.event_name, 90), suspect_name: clean(d.suspect_name, 90),
    identical: d.identical === true, overlap_pct: Math.min(count(d.overlap_pct), 100),
    gone: count(d.gone), before_n: count(d.before_n), added: count(d.added), after_n: count(d.after_n),
    gone_names: names(d.gone_names), added_names: names(d.added_names),
    sha: clean(d.sha, 40).replace(/[^0-9a-zA-Z]/g, ''), when: clean(d.when, 40), author: clean(d.author, 60),
    commit_url: link(d.commit_url, ['github.com']), file_url: link(d.file_url, ['github.com']),
    event_url: link(d.event_url, [brand.site]), status_url: link(d.status_url, [brand.site])
  };
}

// ---- token, lockout, daily budget, duplicates ---------------------------------------

function props() { return PropertiesService.getScriptProperties(); }

function expectedToken() {
  return String(props().getProperty('REDFLAG_TOKEN') || '').trim();
}

function authorized(token) {
  var expected = expectedToken();
  return !!expected && String(token || '').trim() === expected;
}

// seconds remaining on an active lock, else 0
function currentLock() {
  var lock = readJson('REDFLAG_LOCK');
  var left = Math.ceil(((lock.locked_until || 0) - Date.now()) / 1000);
  return left > 0 ? left : 0;
}

// Count a wrong token; returns lock seconds when this attempt trips a threshold, else 0.
function registerFailure() {
  var lock = readJson('REDFLAG_LOCK');
  lock.count = (lock.count || 0) + 1;
  var secs = LOCK_STEPS[lock.count] || 0;
  if (secs) lock.locked_until = Date.now() + secs * 1000;
  if (lock.count >= 10) lock.count = 0;          // after the 24 h lock the ladder starts again
  props().setProperty('REDFLAG_LOCK', JSON.stringify(lock));
  return secs;
}

function resetFailures() {
  props().deleteProperty('REDFLAG_LOCK');
}

// true when today's budget allows another alert (and records it)
function dailyBudget() {
  var today = Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd');
  var d = readJson('REDFLAG_DAILY');
  if (d.day !== today) d = { day: today, sends: 0 };
  if (d.sends + 1 > DAILY_MAX) return false;
  d.sends += 1;
  props().setProperty('REDFLAG_DAILY', JSON.stringify(d));
  return true;
}

// one alert per (brand, kind, folder, commit): {key: epoch ms}, pruned after SEEN_DAYS
function alreadySent(key) {
  return !!readJson('REDFLAG_SEEN')[key];
}

function remember(key) {
  var seen = readJson('REDFLAG_SEEN'), cutoff = Date.now() - SEEN_DAYS * 86400000, kept = {};
  Object.keys(seen).forEach(function (k) { if (seen[k] > cutoff) kept[k] = seen[k]; });
  kept[key] = Date.now();
  props().setProperty('REDFLAG_SEEN', JSON.stringify(kept));
}

function readJson(key) {
  try { return JSON.parse(props().getProperty(key) || '{}') || {}; } catch (e) { return {}; }
}

// ---- helpers (same as lead-form.gs) -----------------------------------------

function respond(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
function clean(v, max) {
  return String(v == null ? '' : v).replace(/[\x00-\x1f\x7f]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max);
}

// ---- editor test ------------------------------------------------------------
// Run from the editor: logs the email, sends nothing.
function testAlert() {
  var brand = BRANDS.sreday;
  var mail = composeAlert(normalizeFlag({
    brand: 'sreday', kind: 'swap', folder: '2026-london-q3', event_name: 'SREday London 2026 Q3',
    suspect_name: 'SREday San Francisco 2026 Q4', identical: true, overlap_pct: 100,
    gone: 29, before_n: 29, added: 26, after_n: 26,
    gone_names: ['Miko Pawlikowski', 'Peter Marshall', 'Rob Reid'], added_names: ['Arjun Iyer', 'Aron Eidelman'],
    sha: '3a98fc0b54754255ed0c1216709be2e52d72b3dc', when: 'Mon 21 Sep, 00:15', author: 'Mark Pawlikowski',
    commit_url: 'https://github.com/sreday/sreday/commit/3a98fc0b54754255ed0c1216709be2e52d72b3dc',
    file_url: 'https://github.com/sreday/sreday/commits/main/2026-london-q3/_db/talks.csv',
    event_url: 'https://www.sreday.com/2026-london-q3/', status_url: 'https://www.sreday.com/status/'
  }, brand));
  Logger.log(mail.subject + '\n\n' + mail.text);
}
