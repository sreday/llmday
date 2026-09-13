/**
 * Speaker fast track backend - Google Apps Script web app.
 *
 * Serves the hidden /<event>/fasttrack/ pages (template: _event_template/_templates/fasttrack.html,
 * facts built by _event_template/_build/generate.py). A speaker we already talked to submits their talk
 * (name, company, job title, email, LinkedIn, title, abstract, bio, headshot) plus the name of the team
 * member they spoke with. The script emails ONE organizer-facing message From the brand alias, To that
 * same alias, Cc only the outreach route when the team member is recognised (see TEAM), Reply-To the
 * speaker. Subject "<Name> - Fast track proposal - <Event>"; body = red heading + a Name/Email/
 * Organization/LinkedIn/Talk Title/Talk Abstract/Bio table (Marek's layout, 2026-09-13). The headshot, when
 * given, is attached twice (site-ready PNG named "<Name>.png", max 400px, not cropped + resized JPG) with no
 * mention in the body. The thread is filed in the Inbox unread + important under "Fast track".
 *
 * Abuse guards (no passphrase - speakers use this): honeypot, daily cap (FASTTRACK_DAILY property),
 * size/length caps, LinkedIn host check, event URL pinned to the brand domain.
 *
 * Deploy (one-time, from the Google account that sends as mark@sreday.com / mark@llmday.com / mark@platformday.com):
 *   1. https://script.google.com -> New project -> paste this file -> save as "Fast track".
 *   2. Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone.
 *   3. Authorise the Gmail scope when prompted, copy the .../exec URL.
 *   4. Put that URL into home/metadata.yml -> fasttrack_form_url in sreday, llmday AND platformday, rebuild.
 *   Re-deploy after editing: Deploy -> Manage deployments -> edit -> new version (the URL stays the same).
 */

var BRANDS = {
  sreday:      { from: 'mark@sreday.com',      site: 'sreday.com' },
  llmday:      { from: 'mark@llmday.com',      site: 'llmday.com' },
  platformday: { from: 'mark@platformday.com', site: 'platformday.com' }
};
var SENDER_NAME = 'Mark Pawlikowski';
var LEAD_LABEL = 'Fast track';
var DAILY_MAX = 30;                       // submissions per day
var MAX_IMAGE_BYTES = 5 * 1024 * 1024;    // per attachment, after the browser resized it
var LIMITS = { name: 80, company: 120, jobtitle: 120, email: 254, linkedin: 300, title: 160, abstract: 8000, bio: 4000, outreach: 80 };

// Who did the speaker talk to? Aliases are matched after normalisation (lowercase, no diacritics,
// letters only). route: '' = nobody extra in Cc; '{brand}' = the brand's domain.
var TEAM = [
  { name: 'Miko',       route: 'aleksandra@sreday.com', aliases: ['miko', 'mikolaj', 'mikko', 'micko', 'mikus', 'miko pawlikowski', 'mikolaj pawlikowski'] },
  { name: 'Mark',       route: 'aleksandra@sreday.com', aliases: ['mark', 'marek', 'marc', 'mareczek', 'mark pawlikowski', 'marek pawlikowski'] },
  { name: 'Aleksandra', route: 'aleksandra@sreday.com', aliases: ['aleksandra', 'alexandra', 'oleksandra', 'ola', 'olka', 'aleks', 'alex', 'alexa', 'sandra', 'aleksandra los'] },
  { name: 'Petras',     route: '',                      aliases: ['petras', 'peter', 'piotr', 'pete', 'petr', 'petras bazdaras'] },
  { name: 'Magdalena',  route: '',                      aliases: ['magdalena', 'magda', 'madga', 'magdalene', 'maggie', 'lena', 'magdalena marcinkiewicz'] },
  { name: 'Emilia',     route: '',                      aliases: ['emilia', 'emilka', 'emily', 'emilie', 'emi', 'milka'] },
  { name: 'Anna',       route: 'anna@{brand}',          aliases: ['anna', 'ania', 'anya', 'anka', 'anja', 'ann', 'annie', 'hanna', 'anna andriushchenko'] },
  { name: 'Blanka',     route: 'anna@{brand}',          aliases: ['blanka', 'blanca', 'bianca', 'blanka pawlikowska', 'blanka pawlikowska michalak', 'blanka michalak'] },
  { name: 'Sylwia',     route: 'anna@{brand}',          aliases: ['sylwia', 'sylvia', 'sylvie', 'silvia', 'sylwka', 'syl', 'sylwia pawlikowska', 'sylvia pawlikowska'] }
];

function doGet() {
  // Health + the alias table, so the page can show a live "sounds like Magdalena" hint from one source of truth.
  return respond({ ok: true, service: 'fasttrack', version: 4,
                   team: TEAM.map(function (t) { return { name: t.name, aliases: t.aliases }; }) });
}

function doPost(e) {
  var data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (err) {
    return respond({ ok: false, error: 'bad json' });
  }
  if (data.website) return respond({ ok: true });            // honeypot -> pretend success

  var brand = BRANDS[String(data.brand || '').toLowerCase()];
  if (!brand) return respond({ ok: false, error: 'bad brand' });
  var ev = normalizeEvent(data.event || {}, brand);
  var s = normalizeSubmission(data);
  if (s.errors.length) return respond({ ok: false, error: 'invalid', fields: s.errors });

  var png = decodeImage(data.photo_png_b64, 'image/png', s.name + '.png');
  var jpg = decodeImage(data.photo_jpg_b64, 'image/jpeg', s.name + ' - original.jpg');
  if (png === 'too large' || jpg === 'too large') return respond({ ok: false, error: 'image too large' });
  var attachments = [png, jpg].filter(function (b) { return b && b !== 'too large'; });   // headshot is optional

  var match = matchOutreach(s.outreach);
  var from = GmailApp.getAliases().indexOf(brand.from) !== -1 ? brand.from : Session.getEffectiveUser().getEmail();
  var cc = [];                                                     // organizer-facing: the speaker is NOT copied
  if (match.person && match.person.route) cc.push(match.person.route.replace('{brand}', brand.site));

  var mail = composeSubmission(s, ev, match);
  if (data.dry_run) {
    return respond({ ok: true, dry_run: true, subject: mail.subject, from: from, to: from, cc: cc, text: mail.text, html: mail.html,
                     match: match.person ? match.person.name : null, attachments: attachments.map(function (b) { return b.getName(); }) });
  }
  if (!dailyBudget()) return respond({ ok: false, error: 'too many today' });

  var options = { name: SENDER_NAME, replyTo: s.email, htmlBody: mail.html };
  if (cc.length) options.cc = cc.join(',');
  if (attachments.length) options.attachments = attachments;
  if (from === brand.from) options.from = brand.from;
  var message = GmailApp.createDraft(from, mail.subject, mail.text, options).send();
  fileThread(message);
  Logger.log('Fast track: %s (%s) for %s via %s -> cc %s', s.name, s.company, ev.event_name, match.person ? match.person.name : '(unmatched: ' + s.outreach + ')', cc.join(', '));
  return respond({ ok: true, match: match.person ? match.person.name : null });
}

// ---- outreach matching -----------------------------------------------------------

function normalizeName(v) {
  var t = String(v == null ? '' : v).toLowerCase();
  try { t = t.normalize('NFD').replace(/[\u0300-\u036f]/g, ''); } catch (err) {}
  t = t.replace(/ł/g, 'l').replace(/ø/g, 'o').replace(/ß/g, 'ss').replace(/æ/g, 'ae');
  return t.replace(/[^a-z]+/g, ' ').replace(/\s+/g, ' ').trim();
}

// Optimal string alignment distance (Levenshtein + adjacent transposition)
function editDistance(a, b) {
  var d = [], i, j;
  for (i = 0; i <= a.length; i++) { d[i] = [i]; }
  for (j = 0; j <= b.length; j++) { d[0][j] = j; }
  for (i = 1; i <= a.length; i++) {
    for (j = 1; j <= b.length; j++) {
      var cost = a[i - 1] === b[j - 1] ? 0 : 1;
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
    }
  }
  return d[a.length][b.length];
}

// Returns { person: TEAM entry | null, how: 'exact'|'prefix'|'fuzzy'|'none', input }
function matchOutreach(text) {
  var norm = normalizeName(text);
  var candidates = norm ? [norm].concat(norm.split(' ').filter(function (w) { return w.length >= 2 && w !== norm; })) : [];
  for (var c = 0; c < candidates.length; c++) {
    var cand = candidates[c];
    // 1. exact alias
    for (var t = 0; t < TEAM.length; t++) {
      if (TEAM[t].aliases.indexOf(cand) !== -1) return { person: TEAM[t], how: 'exact', input: text };
    }
    // 2. unique prefix (>= 3 letters)
    if (cand.length >= 3) {
      var owners = {};
      TEAM.forEach(function (p) { p.aliases.forEach(function (a) { if (a.indexOf(cand) === 0) owners[p.name] = p; }); });
      var names = Object.keys(owners);
      if (names.length === 1) return { person: owners[names[0]], how: 'prefix', input: text };
    }
    // 3. fuzzy: best edit distance within the alias-length threshold, unique winner only
    var best = null, bestScore = 99, tie = false;
    TEAM.forEach(function (p) {
      p.aliases.forEach(function (a) {
        var limit = a.length <= 5 ? 1 : 2;
        var dist = editDistance(cand, a);
        if (dist > limit) return;
        if (dist < bestScore) { bestScore = dist; best = p; tie = false; }
        else if (dist === bestScore && best && best.name !== p.name) { tie = true; }
      });
    });
    if (best && !tie) return { person: best, how: 'fuzzy', input: text };
  }
  return { person: null, how: 'none', input: text };
}

// ---- the email -------------------------------------------------------------------
// Format follows Anna's outreach mails ("Marek, hi! Speaker for LLMday Redwood: ... 1. Title 2. Abstract ...").

function composeSubmission(s, ev, match) {
  var via = match.person ? match.person.name : (s.outreach ? s.outreach + ' (not matched)' : 'unknown');
  var formUrl = ev.event_url + 'fasttrack/';
  var subject = s.name + ' - Fast track proposal - ' + ev.event_name;
  var rows = [
    ['Name', s.name], ['Email', s.email], ['Organization', s.company], ['LinkedIn', s.linkedin],
    ['Talk Title', s.title], ['Talk Abstract', s.abstract], ['Bio', s.bio]
  ];

  var text =
    'Fast track - ' + ev.event_name + '\n\n' +
    'Speaker invited by ' + via + ', and they have successfully submitted their talk here: ' + formUrl + '\n\n' +
    rows.map(function (r) { return r[0] + ': ' + r[1]; }).join('\n\n') + '\n';

  var red = '#a61c1c';
  var html =
    '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#222">' +
    '<h2 style="color:' + red + ';font-size:20px;margin:0 0 12px">Fast track - ' + esc(ev.event_name) + '</h2>' +
    '<p style="margin:0 0 16px">Speaker invited by <b>' + esc(via) + '</b>, and they have successfully submitted their talk here: <a href="' + esc(formUrl) + '">' + esc(formUrl) + '</a></p>' +
    '<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;max-width:640px">' +
    rows.map(function (r) {
      var v = r[0] === 'Email' ? '<a href="mailto:' + esc(r[1]) + '">' + esc(r[1]) + '</a>'
            : r[0] === 'LinkedIn' ? '<a href="' + esc(r[1]) + '">' + esc(r[1]) + '</a>'
            : esc(r[1]).replace(/\n/g, '<br>');
      return '<tr><td style="color:' + red + ';font-weight:bold;padding:4px 18px 4px 0;vertical-align:top;white-space:nowrap">' + r[0] + '</td>' +
             '<td style="padding:4px 0;vertical-align:top">' + v + '</td></tr>';
    }).join('') +
    '</table></div>';
  return { subject: subject, text: text, html: html };
}

function csvCell(v) {
  v = String(v == null ? '' : v);
  return /[",\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
}

// Port of generate_talk_url() in _event_template/_build/generate.py: spaces -> _, drop non-word chars, 100 chars.
function talkSlug(name, company, title) {
  var url = name.replace(/ /g, '_') +
            (company ? '_' + company.replace(/ /g, '_') : '') +
            (title ? '_' + title.replace(/,/g, ' _').replace(/ /g, '_') : '');
  url = url.replace(/[^\x20-\x7e\t\n\r\x0b\x0c]/g, '');   // string.printable
  url = url.replace(/\W+/g, '');
  return url.slice(0, 100);
}

// ---- validation ------------------------------------------------------------------

function normalizeSubmission(d) {
  var s = {
    outreach: clean(d.outreach, LIMITS.outreach),
    name:     clean(d.name, LIMITS.name).replace(/[\\\/:*?"<>|]/g, ''),   // also used as the attachment file name
    company:  clean(d.company, LIMITS.company),
    jobtitle: clean(d.jobtitle, LIMITS.jobtitle),
    email:    clean(d.email, LIMITS.email).toLowerCase(),
    linkedin: clean(d.linkedin, LIMITS.linkedin),
    title:    clean(d.title, LIMITS.title),
    abstract: cleanMultiline(d.abstract, LIMITS.abstract),
    bio:      cleanMultiline(d.bio, LIMITS.bio),
    consent:  d.consent === true || d.consent === 'yes',
    errors:   []
  };
  if (!s.consent) s.errors.push('consent');     // "I agree to share all the information above with the organizer"
  if (!s.outreach) s.errors.push('outreach');
  if (!s.name) s.errors.push('name');
  if (!s.company) s.errors.push('company');
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(s.email)) s.errors.push('email');
  if (!/^https?:\/\/(www\.)?linkedin\.com\/.+/i.test(s.linkedin)) s.errors.push('linkedin');
  if (!s.title) s.errors.push('title');
  if (!s.abstract) s.errors.push('abstract');
  if (!s.bio) s.errors.push('bio');
  return s;
}

function normalizeEvent(raw, brand) {
  var slug = /^[a-z0-9-]{3,60}$/.test(String(raw.slug || '')) ? String(raw.slug) : '';
  var site = 'https://' + brand.site + '/';
  var url = clean(raw.event_url, 300);
  var okUrl = /^https:\/\/(www\.)?/.test(url) && url.replace(/^https:\/\/(www\.)?/, '').indexOf(brand.site + '/') === 0;
  return {
    brand_name: clean(raw.brand_name, 40) || brand.site.replace(/\.com$/, ''),
    event_name: clean(raw.event_name, 80) || (clean(raw.brand_name, 40) + ' ' + clean(raw.city, 60)).trim() || brand.site,
    event_url:  okUrl ? url.replace(/\/?$/, '/') : site + (slug ? slug + '/' : '')
  };
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

// ---- filing, budget, helpers (same conventions as onboarding-form.gs) --------------

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
  var d = readJson('FASTTRACK_DAILY');
  if (d.day !== today) d = { day: today, count: 0 };
  if (d.count + 1 > DAILY_MAX) return false;
  d.count += 1;
  props().setProperty('FASTTRACK_DAILY', JSON.stringify(d));
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
// keeps line breaks (markdown paragraphs), strips other control chars, normalises CRLF
function cleanMultiline(v, max) {
  return String(v == null ? '' : v).replace(/\r\n?/g, '\n').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+/g, ' ')
    .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim().slice(0, max);
}
function esc(v) {
  return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---- editor test: composes without sending (dry run) -------------------------------
function testFasttrack() {
  var tiny = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
  var e = { postData: { contents: JSON.stringify({
    dry_run: true, brand: 'sreday', outreach: 'Magda', consent: true,
    name: 'Leon Lobo', company: 'Oracle', jobtitle: 'Software Engineering Director', email: 'hello@sreday.com',
    linkedin: 'https://www.linkedin.com/in/leonlobo27/', title: 'Designing Hybrid Intelligence for Enterprise Finance',
    abstract: 'AI agents in Enterprise finance products improve user experiences.\n\nHowever, finance processes also depend on strict rules.\nThis session explores the balance.',
    bio: 'Leon Lobo is a Software Engineering Director at Oracle with 15+ years of experience.',
    photo_png_b64: tiny, photo_jpg_b64: '',
    event: { brand: 'sreday', brand_name: 'SREday', slug: '2026-london-q3', event_name: 'SREday London 2026 Q3', city: 'London', date: 'September 24, 2026', event_url: 'https://www.sreday.com/2026-london-q3/' }
  }) } };
  Logger.log(doPost(e).getContent());
}
