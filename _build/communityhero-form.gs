/**
 * Community Hero backend - Google Apps Script web app ("Community hero").
 *
 * Serves the hidden /<event>/communityhero/ pages (template: _event_template/_templates/communityhero.html,
 * facts built by _event_template/_build/generate.py). A person who applied for a Community Hero ticket (a
 * free seat in exchange for telling friends about the event) reports what they did: which actions, links to
 * their posts, screenshots of chats. The script emails ONE message From the brand alias, To anna@<brand>
 * (PEC: anna@llmday.com), Cc the hero, Reply-To the hero. Subject "<Name> - Community Hero - <Event>";
 * body = "<Name> says they have done this for <Event>:" + the action list with details + proof links +
 * the hero's details; screenshots attached raw, renamed "<Name> - proof N.<ext>". The thread is filed in
 * the Inbox unread + important under "Community hero". Anna replies to the thread by hand to confirm the
 * ticket - nothing is automated on that side on purpose.
 *
 * Abuse guards (no passphrase - heroes use this): honeypot, daily cap (COMMUNITYHERO_DAILY property), size
 * and length caps, at most 5 screenshots of 10 MB, LinkedIn host check, event URL pinned to the brand domain.
 *
 * Deploy (one-time, from the Google account that sends as mark@sreday.com / mark@llmday.com / mark@platformday.com):
 *   1. https://script.google.com -> New project -> paste this file -> save as "Community hero".
 *   2. Run testCommunityHero once from the editor (authorises Gmail), check the log.
 *   3. Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone. Copy the .../exec URL.
 *   4. Put that URL into home/metadata.yml -> communityhero_form_url in all four repos, rebuild.
 *   Re-deploy after editing: Deploy -> Manage deployments -> edit -> new version (the URL stays the same).
 */

var BRANDS = {
  sreday:      { from: 'mark@sreday.com',      site: 'sreday.com',      color: '#713660' },
  llmday:      { from: 'mark@llmday.com',      site: 'llmday.com',      color: '#26986A' },
  platformday: { from: 'mark@platformday.com', site: 'platformday.com', color: '#E2971D' },
  // PEC: no PEC mail aliases exist, so it sends from the LLMday alias and Anna's LLMday address gets it
  pec:         { from: 'mark@llmday.com', site: 'promptengineering.rocks', color: '#6b40d8', mail_domain: 'llmday.com' }
};
var SENDER_NAME = 'Mark Pawlikowski';
var TO_USER = 'anna';                       // anna@<brand mail domain> reads the reports
var LEAD_LABEL = 'Community hero';
var DAILY_MAX = 40;                         // reports per day
var MAX_IMAGE_BYTES = 10 * 1024 * 1024;     // per screenshot, same cap as the page
var MAX_SHOTS = 5;
var LIMITS = { name: 80, email: 254, company: 120, role: 120, linkedin: 300, why: 200, other: 300, proof: 2000, detail: 300 };
var ACTIONS = [                             // key on the page -> sentence in the email
  { key: 'linkedin',  text: 'posted about the event on LinkedIn',                          detail: 'post' },
  { key: 'social',    text: 'posted on X, Bluesky, Mastodon or Threads',                   detail: 'post' },
  { key: 'community', text: 'shared it in a community (Slack, Discord, WhatsApp, Meetup)', detail: 'where' },
  { key: 'invites',   text: 'messaged friends or colleagues directly',                     detail: 'count' }
];

function doGet() {
  return respond({ ok: true, service: 'communityhero', version: 1 });
}

function doPost(e) {
  var data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (err) {
    return respond({ ok: false, error: 'bad json' });
  }
  if (data.website) return respond({ ok: true });            // honeypot -> pretend success

  var brandKey = String(data.brand || '').toLowerCase();
  var brand = BRANDS[brandKey];
  if (!brand) return respond({ ok: false, error: 'bad brand' });
  var ev = normalizeEvent(data.event || {}, brand);
  var s = normalizeReport(data);
  if (s.errors.length) return respond({ ok: false, error: 'invalid', fields: s.errors });

  var attachments = [], shots = Array.isArray(data.shots) ? data.shots.slice(0, MAX_SHOTS) : [];
  for (var i = 0; i < shots.length; i++) {
    var sh = shots[i] || {};
    var blob = decodeImage(sh.b64, photoMime(sh.type, sh.name), s.name + ' - proof ' + (i + 1) + photoExt(sh.type, sh.name));
    if (blob === 'too large') return respond({ ok: false, error: 'image too large' });
    if (blob) attachments.push(blob);
  }

  var from = GmailApp.getAliases().indexOf(brand.from) !== -1 ? brand.from : Session.getEffectiveUser().getEmail();
  var to = TO_USER + '@' + (brand.mail_domain || brand.site);
  var mail = composeReport(s, ev, brand, attachments.length);
  if (data.dry_run) {
    return respond({ ok: true, dry_run: true, subject: mail.subject, from: from, to: to, cc: s.email, text: mail.text, html: mail.html,
                     attachments: attachments.map(function (b) { return b.getName(); }) });
  }
  if (!dailyBudget()) return respond({ ok: false, error: 'too many today' });

  var options = { name: SENDER_NAME, replyTo: s.email, cc: s.email, htmlBody: mail.html };
  if (attachments.length) options.attachments = attachments;
  if (from === brand.from) options.from = brand.from;
  var message = GmailApp.createDraft(to, mail.subject, mail.text, options).send();
  fileThread(message);
  Logger.log('Community hero: %s (%s) for %s -> %s, %d screenshots', s.name, s.email, ev.event_name, to, attachments.length);
  return respond({ ok: true });
}

// ---- the email --------------------------------------------------------------------

function composeReport(s, ev, brand, nShots) {
  var subject = s.name + ' - Community Hero - ' + ev.event_name;
  var did = [];
  ACTIONS.forEach(function (a) {
    var act = s.actions[a.key];
    if (!act || !act.done) return;
    var line = a.text;
    if (act.detail) {
      if (a.detail === 'post') line += ': ' + act.detail;
      else if (a.detail === 'count') line += ' (' + act.detail + ' people)';
      else line += ' (' + act.detail + ')';
    }
    did.push(line);
  });
  if (s.other) did.push(s.other);
  var links = s.proof ? s.proof.split(/\r?\n/).map(function (l) { return l.trim(); }).filter(Boolean) : [];

  var details = [['Name', s.name], ['Email', s.email], ['Company', s.company], ['Role', s.role], ['LinkedIn', s.linkedin], ['Why they come', s.why]]
    .filter(function (r) { return r[1]; });

  var text =
    'Community Hero - ' + ev.event_name + '\n\n' +
    s.name + ' says they have done this for ' + ev.event_name + ':\n' +
    did.map(function (d) { return '- ' + d; }).join('\n') + '\n\n' +
    (links.length ? 'Proof links:\n' + links.map(function (l) { return '- ' + l; }).join('\n') + '\n\n' : '') +
    (nShots ? nShots + ' screenshot' + (nShots === 1 ? '' : 's') + ' attached.\n\n' : '') +
    details.map(function (r) { return r[0] + ': ' + r[1]; }).join('\n') + '\n\n' +
    'Reply to this email to confirm the ticket (the hero is in Cc).\n';

  var accent = brand.color || '#333';
  var html =
    '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#222">' +
    '<h2 style="display:inline-block;background:' + accent + ';color:#fff;font-size:20px;margin:0 0 18px;padding:6px 12px;border-radius:4px">Community Hero - ' + esc(ev.event_name) + '</h2>' +
    '<p style="margin:0 0 10px"><b>' + esc(s.name) + '</b> says they have done this for <a href="' + esc(ev.event_url) + '">' + esc(ev.event_name) + '</a>:</p>' +
    '<ul style="margin:0 0 18px;padding-left:20px">' + did.map(function (d) { return '<li style="margin:3px 0">' + linkify(esc(d)) + '</li>'; }).join('') + '</ul>' +
    (links.length ? '<p style="margin:0 0 6px;color:' + accent + ';font-weight:bold">Proof links</p><ul style="margin:0 0 18px;padding-left:20px">' +
      links.map(function (l) { return '<li style="margin:3px 0">' + linkify(esc(l)) + '</li>'; }).join('') + '</ul>' : '') +
    (nShots ? '<p style="margin:0 0 18px">' + nShots + ' screenshot' + (nShots === 1 ? '' : 's') + ' attached.</p>' : '') +
    '<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;max-width:640px">' +
    details.map(function (r) {
      var v = /Email$/.test(r[0]) ? '<a href="mailto:' + esc(r[1]) + '">' + esc(r[1]) + '</a>' : /LinkedIn$/.test(r[0]) ? '<a href="' + esc(r[1]) + '">' + esc(r[1]) + '</a>' : esc(r[1]);
      return '<tr><td style="color:' + accent + ';font-weight:bold;padding:4px 18px 4px 0;vertical-align:top;white-space:nowrap">' + r[0] + '</td><td style="padding:4px 0;vertical-align:top">' + v + '</td></tr>';
    }).join('') +
    '</table>' +
    '<p style="margin:18px 0 0;color:#666">Reply to this email to confirm the ticket (the hero is in Cc).</p></div>';
  return { subject: subject, text: text, html: html };
}

function linkify(t) {
  return t.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1">$1</a>');
}

// ---- validation -------------------------------------------------------------------

function normalizeReport(d) {
  var s = {
    name:     clean(d.name, LIMITS.name).replace(/[\\\/:*?"<>|]/g, ''),   // also used in attachment file names
    email:    clean(d.email, LIMITS.email).toLowerCase(),
    company:  clean(d.company, LIMITS.company),
    role:     clean(d.role, LIMITS.role),
    linkedin: clean(d.linkedin, LIMITS.linkedin),
    why:      clean(d.why, LIMITS.why),
    other:    clean(d.other, LIMITS.other),
    proof:    cleanMultiline(d.proof, LIMITS.proof),
    consent:  d.consent === true || d.consent === 'yes',
    actions:  {},
    errors:   []
  };
  var raw = (d.actions && typeof d.actions === 'object') ? d.actions : {};
  var any = false;
  ACTIONS.forEach(function (a) {
    var v = raw[a.key] || {};
    s.actions[a.key] = { done: v.done === true, detail: clean(v.detail, LIMITS.detail) };
    if (v.done === true) any = true;
  });
  if (!s.consent) s.errors.push('consent');
  if (!s.name) s.errors.push('name');
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(s.email)) s.errors.push('email');
  if (s.linkedin && !/^https?:\/\/(www\.)?linkedin\.com\/.+/i.test(s.linkedin)) s.errors.push('linkedin');
  if (!any && !s.other) s.errors.push('actions');
  return s;
}

function normalizeEvent(raw, brand) {
  var slug = /^[a-z0-9-]{3,60}$/.test(String(raw.slug || '')) ? String(raw.slug) : '';
  var site = 'https://' + brand.site + '/';
  var url = clean(raw.event_url, 300);
  var okUrl = /^https:\/\/(www\.)?/.test(url) && url.replace(/^https:\/\/(www\.)?/, '').indexOf(brand.site + '/') === 0;
  return {
    brand_name: clean(raw.brand_name, 40) || brand.site.replace(/\.[a-z]+$/, ''),
    event_name: clean(raw.event_name, 80) || (clean(raw.brand_name, 40) + ' ' + clean(raw.city, 60)).trim() || brand.site,
    event_url:  okUrl ? url.replace(/\/?$/, '/') : site + (slug ? slug + '/' : '')
  };
}

var IMAGE_TYPES = { 'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/heic': '.heic', 'image/heif': '.heif', 'image/gif': '.gif', 'image/tiff': '.tif' };

function photoExt(type, name) {
  type = String(type || '').toLowerCase();
  if (IMAGE_TYPES[type]) return IMAGE_TYPES[type];
  var m = /\.([a-z0-9]{2,5})$/i.exec(String(name || ''));
  return m ? '.' + m[1].toLowerCase().replace(/^jpeg$/, 'jpg') : '.png';
}
function photoMime(type, name) {
  type = String(type || '').toLowerCase();
  if (IMAGE_TYPES[type]) return type;
  var ext = photoExt(type, name);
  for (var k in IMAGE_TYPES) if (IMAGE_TYPES[k] === ext) return k;
  return 'application/octet-stream';
}

// base64 -> Blob; returns null when empty, 'too large' when over the cap
function decodeImage(b64, mime, filename) {
  b64 = String(b64 || '').replace(/^data:[^,]*,/, '').replace(/\s+/g, '');
  if (!b64) return null;
  if (b64.length * 3 / 4 > MAX_IMAGE_BYTES) return 'too large';
  try {
    return Utilities.newBlob(Utilities.base64Decode(b64), mime, filename);
  } catch (err) {
    return null;
  }
}

// ---- filing, budget, helpers (same conventions as the other form scripts) ------------

function fileThread(message) {
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
}

function props() { return PropertiesService.getScriptProperties(); }

function dailyBudget() {
  var today = Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd');
  var d = readJson('COMMUNITYHERO_DAILY');
  if (d.day !== today) d = { day: today, count: 0 };
  if (d.count + 1 > DAILY_MAX) return false;
  d.count += 1;
  props().setProperty('COMMUNITYHERO_DAILY', JSON.stringify(d));
  return true;
}

function readJson(key) {
  try { return JSON.parse(props().getProperty(key) || '{}') || {}; } catch (e) { return {}; }
}
function respond(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
function clean(v, max) {
  return String(v == null ? '' : v).replace(/[\x00-\x1f\x7f]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max);
}
function cleanMultiline(v, max) {
  return String(v == null ? '' : v).replace(/\r\n?/g, '\n').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+/g, ' ')
    .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim().slice(0, max);
}
function esc(v) {
  return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---- editor test: composes without sending (dry run) -------------------------------
function testCommunityHero() {
  var tiny = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
  var e = { postData: { contents: JSON.stringify({
    dry_run: true, brand: 'sreday', consent: true,
    name: 'Leon Lobo', email: 'hello@sreday.com', company: 'Oracle', role: 'Software Engineering Director',
    linkedin: 'https://www.linkedin.com/in/leonlobo27/', why: 'I want to see how other teams run agents in production',
    actions: { linkedin: { done: true, detail: 'https://www.linkedin.com/posts/leonlobo27_sreday' }, social: { done: false, detail: '' },
               community: { done: true, detail: 'London SRE Slack, about 400 people' }, invites: { done: true, detail: '6' } },
    other: 'Mentioned it in our team standup', proof: 'https://www.linkedin.com/posts/leonlobo27_sreday\nhttps://example.com/thread',
    shots: [{ b64: tiny, type: 'image/png', name: 'slack.png' }],
    event: { brand: 'sreday', brand_name: 'SREday', slug: '2026-london-q3', event_name: 'SREday London 2026 Q3', city: 'London', date: 'September 24, 2026', event_url: 'https://www.sreday.com/2026-london-q3/' }
  }) } };
  Logger.log(doPost(e).getContent());
}
