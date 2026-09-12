/**
 * Speaker onboarding backend - Google Apps Script web app.
 *
 * Serves the hidden /<event>/onboarding/ pages (template: _event_template/_templates/onboarding.html,
 * facts built by _event_template/_build/generate.py). The page posts:
 *   { action: 'preview' | 'send', pass, brand, emails: [...], event: {...facts...}, website (honeypot), page }
 * The script owns the ONE universal "Info for speakers" email (composeOnboarding below), fills it with the
 * event facts, and on 'send' emails it From the brand alias, To that same alias (hello@ is deliberately NOT
 * copied - Marek's call 2026-09-12), with every speaker in Bcc.
 * The thread is then moved to the Inbox as unread + important under the "Speaker onboarding" label, so it
 * shows up like a sponsor lead and speaker "OK" replies land on it. 'preview' returns subject + html only.
 *
 * Abuse guards (the /exec URL is public): passphrase checked against the Script Property
 * ONBOARDING_PASSPHRASE; 3 wrong attempts -> locked 15 min, 10 -> locked 24 h (a correct passphrase resets
 * the counter; clear a lock by deleting the ONBOARDING_LOCK property); daily caps of 50 sends / 500
 * recipients; max 50 recipients per send (Apps Script limit); template, sender and links are pinned here,
 * so a leaked passphrase can only send THIS email to more people, never arbitrary content.
 *
 * Deploy (one-time, from the Google account that sends as mark@sreday.com / mark@llmday.com / mark@platformday.com):
 *   1. https://script.google.com -> New project -> paste this file -> save as "Speaker onboarding".
 *   2. Project Settings (gear) -> Script properties -> add ONBOARDING_PASSPHRASE = <the passphrase>.
 *   3. Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone.
 *   4. Authorise the Gmail scope when prompted, copy the .../exec URL.
 *   5. Put that URL into home/metadata.yml -> onboarding_form_url in sreday, llmday AND platformday, rebuild.
 *   Re-deploy after editing: Deploy -> Manage deployments -> edit -> new version (the URL stays the same).
 */

var BRANDS = {
  sreday:      { inbox: 'hello@sreday.com',      from: 'mark@sreday.com',      site: 'sreday.com',      code: 'SRE20' },
  llmday:      { inbox: 'hello@llmday.com',      from: 'mark@llmday.com',      site: 'llmday.com',      code: 'LLM20' },
  platformday: { inbox: 'hello@platformday.com', from: 'mark@platformday.com', site: 'platformday.com', code: 'PLATFORM20' }
};
var SENDER_NAME = 'Mark Pawlikowski';
var LEAD_LABEL = 'Speaker onboarding';
var MAX_RECIPIENTS = 50;           // Apps Script: recipients per message
var DAILY_MAX_SENDS = 50;
var DAILY_MAX_RECIPIENTS = 500;
var LOCK_STEPS = { 3: 15 * 60, 10: 24 * 60 * 60 };   // failed attempts -> lock seconds

function doGet() {
  // Health check. Never reveals the passphrase, only whether one is configured and whether the endpoint is locked.
  var lock = readJson('ONBOARDING_LOCK');
  return respond({ ok: true, service: 'speaker-onboarding', passphrase_set: !!expectedPassphrase(),
                   failed_attempts: lock.count || 0, locked_for: currentLock(), version: 4 });
}

function doPost(e) {
  var data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (err) {
    return respond({ ok: false, error: 'bad json' });
  }
  if (data.website) return respond({ ok: true });            // honeypot -> pretend success

  var lock = currentLock();
  if (lock > 0) return respond({ ok: false, error: 'locked', retry_in: lock });
  if (!authorized(data.pass)) {
    var retry = registerFailure();
    return retry > 0 ? respond({ ok: false, error: 'locked', retry_in: retry }) : respond({ ok: false, error: 'unauthorized' });
  }
  resetFailures();                                             // a correct passphrase clears the ladder

  var brand = BRANDS[String(data.brand || '').toLowerCase()];
  if (!brand) return respond({ ok: false, error: 'bad brand' });
  var ev = normalizeEvent(data.event || {}, brand);
  var emails = normalizeEmails(data.emails);
  if (emails.bad.length) return respond({ ok: false, error: 'invalid emails', bad: emails.bad });
  if (emails.ok.length > MAX_RECIPIENTS) return respond({ ok: false, error: 'too many' });

  var mail = composeOnboarding(ev, brand);
  var from = GmailApp.getAliases().indexOf(brand.from) !== -1 ? brand.from : Session.getEffectiveUser().getEmail();

  if (data.action === 'preview') {
    return respond({ ok: true, subject: mail.subject, html: mail.html, text: mail.text, from: from, to: from, recipients: emails.ok.length });
  }
  if (!emails.ok.length) return respond({ ok: false, error: 'no recipients' });
  if (!dailyBudget(emails.ok.length)) return respond({ ok: false, error: 'too many today' });

  var options = { name: SENDER_NAME, bcc: emails.ok.join(','), htmlBody: mail.html };
  if (from === brand.from) options.from = brand.from;
  // Send via a draft so we get the message back, then pull the thread into the Inbox (unread, important,
  // labelled) - a mail sent from this very account would otherwise sit read in "Sent" only.
  // To = the sending alias itself (Gmail needs one To address); speakers only ever see Bcc.
  var message = GmailApp.createDraft(from, mail.subject, mail.text, options).send();
  try {
    var thread = message.getThread();
    thread.moveToInbox();
    thread.markUnread();
    thread.markImportant();
    var label = GmailApp.getUserLabelByName(LEAD_LABEL) || GmailApp.createLabel(LEAD_LABEL);
    thread.addLabel(label);
  } catch (err) {
    Logger.log('Sent, but could not file the thread: ' + err);
  }
  Logger.log('Onboarding sent: %s -> %s recipients (bcc) from %s [%s]', ev.event_name, emails.ok.length, from, emails.ok.join(', '));
  return respond({ ok: true, sent: emails.ok.length });
}

// ---- the email --------------------------------------------------------------
// Written ONCE in a tiny markup: *bold*, [label](url), "- " bullets (a "\n" inside a bullet = continuation
// line), "1. " numbered items, "" = blank line, "[y] "/"[g] " prefix = yellow/green highlighted line (HTML only).
// renderText()/renderHtml() turn the same lines into the plain-text and HTML bodies. Wording: Marek, 2026-09-12.

function composeOnboarding(ev, brand) {
  var talk = Math.max(ev.slot_minutes - 5, 5);
  var lines = [
    'Hello!',
    '',
    "We're excited to have you speaking at [" + ev.event_name + '](' + ev.event_url + ') on ' + ev.date + '.',
    '',
    'Venue: *' + ev.venue_name + '*' + (ev.venue_address && ev.venue_address !== ev.venue_name ? ', ' + ev.venue_address : ''),
    '',
    "[y] This conference is strictly in-person and *there's no option to present remotely*",
    ''
  ];
  if (ev.extra) { lines.push(ev.extra); lines.push(''); }
  lines = lines.concat([
    'What happens now:',
    '',
    '- Your participation is confirmed and your talk is added to the website',
    '- Please redeem your free ticket with the code *SPEAKERFREE* here [' + ev.tickets_url + '](' + ev.tickets_url + ')\n  (on luma, select the general self funding / general admission, and then find "Add a coupon" in the top right corner)',
    '- Speaking slots are ' + ev.slot_minutes + ' minutes (' + talk + ' min talk + 5 min Q&A)',
    '- You will present from your own laptop. Please share your slides with us in advance as a backup.',
    "- As speakers confirm, we'll update the website and prepare social media graphics and promo posts for sharing.",
    '- The v1 schedule will be published as soon as we get most speakers confirmed',
    '- Most regular talks will take place in the afternoon, between 12PM-6PM. Some last minute schedule changes may happen, so please avoid tight travel planning.',
    '- We may use AI to create graphics and short videos about your talk for social media promotion. Please let us know if you would prefer to opt out.',
    '',
    '[g] Please reply with a quick "OK" so we know everything is accepted and acknowledged.',
    ''
  ]);
  if (ev.dinner.toLowerCase() !== 'none') {
    lines.push(ev.dinner.toUpperCase() === 'TBC'
      ? "If the budget allows, we may organize a speaker dinner after the conference. If that's the case, we'll send you a calendar invite in advance."
      : ev.dinner);
    lines.push('');
  }
  lines = lines.concat([
    'See you soon,',
    'Mark',
    '',
    '*FAQ:*',
    '',
    '1. Expected attendance - around ' + (ev.attendees || 100) + ' people',
    '1. There will be WiFi access in the venue',
    '1. Previous ' + ev.brand_name + ' talks: ' + (ev.youtube_url ? '[' + ev.youtube_url + '](' + ev.youtube_url + ')' : 'on our YouTube channel'),
    '1. Bring a friend for free with the code *PLUSONE*',
    '1. 50% off for your team and colleagues - share code *DREAMTEAM*',
    '1. 20% off code you can spread everywhere on social media: *' + brand.code + '*',
    "1. Laptop connection to present: wired or wireless. If it's wireless then Google Meet / ZOOM. If wired, then we'll have HDMI and USB-C connectors",
    '1. Travel / accommodation / speaker compensation - unfortunately, those are not covered',
    "1. Your company wants to sponsor the event? Here's the form: [" + ev.event_url + '#sponsors](' + ev.event_url + '#sponsors)',
    '1. Visa support letter is available on request'
  ]);
  return {
    subject: ev.event_name + ' - ' + ev.month_day + ' - Info for speakers',
    text: renderText(lines),
    html: renderHtml(lines)
  };
}

var INLINE_LINK = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
var INLINE_BOLD = /\*([^*\n]+)\*/g;
var HIGHLIGHT = { y: '#fff59d', g: '#c8e6c9' };

function renderText(lines) {
  var n = 0;
  return lines.map(function (l) {
    var m = /^\d+\. (.*)$/.exec(l);
    if (m) { n += 1; l = n + '. ' + m[1]; } else if (!/^- /.test(l)) { n = 0; }
    return l.replace(/^\[[yg]\] /, '')
            .replace(INLINE_LINK, function (_, label, url) { return label === url ? url : label + ' (' + url + ')'; })
            .replace(INLINE_BOLD, '$1');
  }).join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

function renderHtml(lines) {
  var out = [], list = null;   // list = 'ul' | 'ol' | null
  function closeList() { if (list) { out.push('</' + list + '>'); list = null; } }
  function inline(s) {
    return esc(s).replace(INLINE_LINK, function (_, label, url) { return '<a href="' + url + '">' + label + '</a>'; })
                 .replace(INLINE_BOLD, '<b>$1</b>')
                 .replace(/\n\s*/g, '<br><span style="color:#555">') + (/\n/.test(s) ? '</span>' : '');
  }
  var para = [];
  function flushPara() { if (para.length) { out.push('<p>' + para.join('<br>') + '</p>'); para = []; } }
  lines.forEach(function (l) {
    var m;
    if ((m = /^- ([\s\S]*)$/.exec(l))) { flushPara(); if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul'; } out.push('<li>' + inline(m[1]) + '</li>'); }
    else if ((m = /^\d+\. (.*)$/.exec(l))) { flushPara(); if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol'; } out.push('<li>' + inline(m[1]) + '</li>'); }
    else if ((m = /^\[([yg])\] (.*)$/.exec(l))) { flushPara(); closeList(); out.push('<p><span style="background:' + HIGHLIGHT[m[1]] + '">' + inline(m[2]) + '</span></p>'); }
    else if (l === '') { flushPara(); closeList(); }
    else { closeList(); para.push(inline(l)); }
  });
  flushPara(); closeList();
  return '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#111">' + out.join('\n') + '</div>';
}

// ---- validation -------------------------------------------------------------

function normalizeEvent(raw, brand) {
  var slug = /^[a-z0-9-]{3,60}$/.test(String(raw.slug || '')) ? String(raw.slug) : '';
  var site = 'https://' + brand.site + '/';
  var okUrl = function (u, fallback) {
    u = clean(u, 300);
    return (/^https:\/\/(www\.)?/.test(u) && u.replace(/^https:\/\/(www\.)?/, '').indexOf(brand.site + '/') === 0) ? u : fallback;
  };
  var ev = {
    brand_name:    clean(raw.brand_name, 40) || brand.site.replace(/\.com$/, ''),
    event_name:    clean(raw.event_name, 80),
    city:          clean(raw.city, 60) || 'town',
    date:          clean(raw.date, 60) || 'the conference day',
    month_day:     clean(raw.month_day, 40),
    event_url:     okUrl(raw.event_url, site + (slug ? slug + '/' : '')),
    tickets_url:   okUrl(raw.tickets_url, site + (slug ? slug + '/#tickets' : '')),
    venue_name:    clean(raw.venue_name, 120) || 'the venue',
    venue_address: clean(raw.venue_address, 200),
    attendees:     parseInt(raw.attendees, 10) > 0 ? parseInt(raw.attendees, 10) : 0,
    youtube_url:   /^https:\/\/(www\.)?youtube\.com\//.test(String(raw.youtube_url || '')) ? clean(raw.youtube_url, 200) : '',
    calendly_url:  /^https:\/\/calendly\.com\//.test(String(raw.calendly_url || '')) ? clean(raw.calendly_url, 200) : 'https://calendly.com/sreday/30min',
    slot_minutes:  parseInt(raw.slot_minutes, 10) >= 10 && parseInt(raw.slot_minutes, 10) <= 90 ? parseInt(raw.slot_minutes, 10) : 30,
    dinner:        clean(raw.dinner, 300) || 'TBC',
    extra:         clean(raw.extra, 600)
  };
  if (!ev.event_name) ev.event_name = ev.brand_name + ' ' + ev.city;
  if (!ev.month_day) ev.month_day = ev.date.replace(/,\s*\d{4}\s*$/, '');
  return ev;
}

function normalizeEmails(values) {
  var ok = [], bad = [], seen = {};
  (Array.isArray(values) ? values : []).slice(0, 200).forEach(function (v) {
    var e = clean(v, 254).toLowerCase();
    if (!e || seen[e]) return;
    seen[e] = true;
    (/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(e) ? ok : bad).push(e);
  });
  return { ok: ok, bad: bad };
}

// ---- passphrase, lockout, daily budget -------------------------------------

function props() { return PropertiesService.getScriptProperties(); }

function expectedPassphrase() {
  return String(props().getProperty('ONBOARDING_PASSPHRASE') || '').trim();
}

function authorized(pass) {
  var expected = expectedPassphrase();
  return !!expected && String(pass || '').trim() === expected;
}

// seconds remaining on an active lock, else 0
function currentLock() {
  var lock = readJson('ONBOARDING_LOCK');
  var left = Math.ceil(((lock.locked_until || 0) - Date.now()) / 1000);
  return left > 0 ? left : 0;
}

// Count a wrong passphrase; returns lock seconds when this attempt trips a threshold, else 0.
function registerFailure() {
  var lock = readJson('ONBOARDING_LOCK');
  lock.count = (lock.count || 0) + 1;
  var secs = LOCK_STEPS[lock.count] || 0;
  if (secs) lock.locked_until = Date.now() + secs * 1000;
  if (lock.count >= 10) lock.count = 0;          // after the 24 h lock the ladder starts again
  props().setProperty('ONBOARDING_LOCK', JSON.stringify(lock));
  return secs;
}

function resetFailures() {
  props().deleteProperty('ONBOARDING_LOCK');
}

// true when today's budget allows another send of n recipients (and records it)
function dailyBudget(n) {
  var today = Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd');
  var d = readJson('ONBOARDING_DAILY');
  if (d.day !== today) d = { day: today, sends: 0, recipients: 0 };
  if (d.sends + 1 > DAILY_MAX_SENDS || d.recipients + n > DAILY_MAX_RECIPIENTS) return false;
  d.sends += 1; d.recipients += n;
  props().setProperty('ONBOARDING_DAILY', JSON.stringify(d));
  return true;
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
function esc(v) {
  return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---- editor test ------------------------------------------------------------
// Run from the editor: previews (does not send). Flip action to 'send' to send a real one to the inbox only.
function testOnboarding() {
  var e = { postData: { contents: JSON.stringify({
    action: 'preview',
    pass: props().getProperty('ONBOARDING_PASSPHRASE'),
    brand: 'sreday',
    emails: ['hello@sreday.com'],
    event: {
      brand: 'sreday', brand_name: 'SREday', slug: '2026-san-francisco-q4',
      event_name: 'SREday San Francisco 2026 Q4', city: 'San Francisco', date: 'October 2, 2026', month_day: 'October 2',
      event_url: 'https://www.sreday.com/2026-san-francisco-q4/', tickets_url: 'https://www.sreday.com/2026-san-francisco-q4/#tickets',
      venue_name: 'Harness Office', venue_address: '55 Stockton St, San Francisco, CA 94108', attendees: 100,
      youtube_url: 'https://www.youtube.com/@sreday', calendly_url: 'https://calendly.com/sreday/30min',
      slot_minutes: 30, dinner: 'TBC', extra: ''
    }
  }) } };
  Logger.log(doPost(e).getContent());
}
