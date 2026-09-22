/**
 * Speaker waitlist backend - Google Apps Script web app ("Speaker waitlist").
 *
 * Serves the hidden /<event>/waitlist/ pages (template: _event_template/_templates/waitlist.html, facts built by
 * _event_template/_build/generate.py): the fast track form for speakers we reached out to AFTER the lineup filled
 * up. Same submission as the fast track (name, company, email, LinkedIn, title, abstract, bio, headshot, optional
 * co-speaker, who they talked to). The script:
 *   1. emails ONE organizer-facing message From the brand alias, To that alias, Cc the outreach route when the team
 *      member is recognised (TEAM), Reply-To the speaker. Subject "<Name> - Waitlist - <Event>", filed under the
 *      Gmail label "Waitlist" (Inbox, unread, important). Headshot attached raw, renamed to "<Name>.<ext>".
 *   2. appends a row to a Google Sheet ("Speaker waitlist", created on first use, id in WAITLIST_SHEET_ID) which
 *      feeds the /status/ Waitlist tab of every brand site: the build fetches this script with
 *      ?list=1&token=<WAITLIST_TOKEN> (Script Property) and gets the rows as JSON, without the email column.
 *      Rows are never deleted by code: the sheet is the history, edit it by hand if needed.
 *
 * Abuse guards (no passphrase - speakers use this): honeypot, daily cap (WAITLIST_DAILY property), size/length
 * caps, LinkedIn host check, event URL pinned to the brand domain.
 *
 * Deploy (one-time, from the Google account that sends as mark@sreday.com / mark@llmday.com / mark@platformday.com):
 *   1. https://script.google.com -> New project -> paste this file -> save as "Speaker waitlist".
 *   2. Project settings -> Script properties -> add WAITLIST_TOKEN = a long random string (the build's feed key).
 *   3. Run testWaitlist once from the editor (authorises Gmail + Sheets, creates the sheet), check the log.
 *   4. Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone. Copy the .../exec URL.
 *   5. Put that URL into home/metadata.yml -> waitlist_form_url in all four repos, rebuild.
 *   6. GitHub -> each repo -> Settings -> Secrets -> Actions -> WAITLIST_FEED = "<exec URL>?list=1&token=<WAITLIST_TOKEN>".
 *   Re-deploy after editing: Deploy -> Manage deployments -> edit -> new version (the URL stays the same).
 */

var BRANDS = {
  sreday:      { from: 'mark@sreday.com',      site: 'sreday.com',      color: '#713660' },
  llmday:      { from: 'mark@llmday.com',      site: 'llmday.com',      color: '#26986A' },
  platformday: { from: 'mark@platformday.com', site: 'platformday.com', color: '#E2971D' },
  // PEC: no PEC mail aliases exist, so it sends from the LLMday alias and routes '{brand}' Cc to llmday.com
  pec:         { from: 'mark@llmday.com', site: 'promptengineering.rocks', color: '#6b40d8', mail_domain: 'llmday.com' }
};
var SENDER_NAME = 'Mark Pawlikowski';
var LEAD_LABEL = 'Waitlist';
var DAILY_MAX = 30;                       // submissions per day
var MAX_IMAGE_BYTES = 10 * 1024 * 1024;   // the raw upload, same cap as the page
var LIMITS = { name: 80, company: 120, jobtitle: 120, email: 254, linkedin: 300, title: 160, abstract: 8000, bio: 4000, outreach: 80 };

// Who did the speaker talk to? Aliases are matched after normalisation (lowercase, no diacritics,
// letters only). route: '' = nobody extra in Cc; '{brand}' = the brand's mail domain (mail_domain, else site).
var TEAM = [
  { name: 'Miko',       route: 'aleksandra@sreday.com', aliases: ['miko', 'mikolaj', 'mikko', 'micko', 'mico', 'meeko', 'miku', 'mikus', 'mikey', 'mikolay', 'mikolai', 'mikkolaj', 'mikolaj pawlikowski', 'miko pawlikowski', 'nick', 'nicholas', 'nicolas', 'nikolaj', 'nikolai', 'mikolaj p', 'miko p', 'mikola', 'micolaj', 'mickolaj', 'mikkolai', 'mykola', 'mykolaj', 'nikolas', 'niko', 'mikolaj pawlikowsky', 'miko from sreday'] },
  { name: 'Mark',       route: 'aleksandra@sreday.com', aliases: ['mark', 'marek', 'marc', 'marko', 'markus', 'marcus', 'mareczek', 'marecki', 'mareq', 'marek p', 'mark p', 'mark pawlikowski', 'marek pawlikowski', 'marek pawlikowsky', 'mark pawlikowsky', 'marik', 'marck', 'mrk', 'markp', 'marekp', 'marek pawl', 'mark pavlikowski', 'marek pavlikovski', 'marek pawlikowskii', 'mark from sreday', 'marek from sreday', 'mark from llmday'] },
  { name: 'Aleksandra', route: 'aleksandra@sreday.com', aliases: ['aleksandra', 'alexandra', 'oleksandra', 'alessandra', 'aleksandr', 'ola', 'olka', 'olcia', 'olenka', 'aleks', 'alex', 'alexa', 'alexia', 'sandra', 'sasha', 'sacha', 'los', 'ola los', 'aleksandra los', 'alexandra los', 'aleksandra l', 'ola l', 'alexsandra', 'aleksandera', 'aleksndra', 'oleksandra los', 'olla', 'olusia', 'sandy', 'alexandria', 'aleksa', 'ola from sreday'] },
  { name: 'Petras',     route: '',                      aliases: ['petras', 'peter', 'piotr', 'piotrek', 'pete', 'petr', 'petar', 'petros', 'pietro', 'pedro', 'petrus', 'petra', 'bazdaras', 'petras bazdaras', 'peter bazdaras', 'petras b', 'petras bazdaris', 'bazdaras petras', 'petras bzdaras', 'petrass', 'petraz', 'pertras', 'peteras', 'peter b', 'piotr b', 'pyotr', 'pjotr', 'petras from sreday'] },
  { name: 'Magdalena',  route: '',                      aliases: ['magdalena', 'magda', 'madga', 'magdalene', 'magdalen', 'magdalenka', 'madzia', 'magdusia', 'maggie', 'maggy', 'lena', 'marcinkiewicz', 'magdalena marcinkiewicz', 'magda marcinkiewicz', 'magda m', 'magdalena marcinkiewic', 'magda marcinkiewic', 'marcinkiewic', 'marcinkievicz', 'magdalenna', 'magdalna', 'magdaline', 'magdolna', 'magdi', 'magdaa', 'magdalena marc', 'magda from sreday'] },
  { name: 'Emilia',     route: '',                      aliases: ['emilia', 'emilka', 'emily', 'emilie', 'emilly', 'emi', 'emmy', 'milka', 'emilja', 'emillia', 'emilia s', 'emilka s', 'emiliya', 'emilija', 'emilya', 'emili', 'emmilia', 'mila', 'emy', 'emmie', 'emilia from sreday', 'emilia from llmday'] },
  { name: 'Anna',       route: 'anna@{brand}',          aliases: ['anna', 'ania', 'anya', 'anka', 'anja', 'ann', 'annie', 'hanna', 'hania', 'anusia', 'anechka', 'andriushchenko', 'andrushchenko', 'andriuschenko', 'anna andriushchenko', 'anna a', 'anna andriuschenko', 'anna andryushchenko', 'andryushchenko', 'anna andriushenko', 'anutka', 'annushka', 'anneta', 'anny', 'anaa', 'ana', 'anna from sreday', 'anna from llmday'] },
  { name: 'Blanka',     route: 'anna@{brand}',          aliases: ['blanka', 'blanca', 'bianca', 'blanche', 'blanki', 'blankah', 'blanka pawlikowska', 'blanka pawlikowska michalak', 'blanka michalak', 'michalak', 'blanka p', 'blanka m', 'blanka pavlikowska', 'blanka michalak pawlikowska', 'michalak pawlikowska', 'blancka', 'blanaka', 'blankaa', 'blanka pawlikowsk', 'blanka from sreday', 'blanka from llmday'] },
  { name: 'Sylwia',     route: 'anna@{brand}',          aliases: ['sylwia', 'sylvia', 'sylvie', 'silvia', 'silvie', 'sylwka', 'sylwunia', 'syl', 'sylwia pawlikowska', 'sylvia pawlikowska', 'sylwia p', 'sylwia pawlikowsk', 'sylwia pavlikowska', 'sylvia p', 'silwia', 'sylwiaa', 'sylvi', 'sylwya', 'silvya', 'sylwie', 'sylwia pawlik', 'sylwia from sreday', 'sylwia from llmday'] }
];

function doGet(e) {
  var p = (e && e.parameter) || {};
  if (p.list) {                                    // the status build: ?list=1&token=<WAITLIST_TOKEN> -> rows as JSON
    var token = props().getProperty('WAITLIST_TOKEN') || '';
    if (!token || String(p.token || '') !== token) return respond({ ok: false, error: 'forbidden' });
    return respond({ ok: true, rows: listWaitlist() });
  }
  // Health + the alias table, so the page can show a live "sounds like Magdalena" hint from one source of truth.
  return respond({ ok: true, service: 'waitlist', version: 1,
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

  var brandKey = String(data.brand || '').toLowerCase();
  var brand = BRANDS[brandKey];
  if (!brand) return respond({ ok: false, error: 'bad brand' });
  var ev = normalizeEvent(data.event || {}, brand);
  var s = normalizeSubmission(data);
  if (s.errors.length) return respond({ ok: false, error: 'invalid', fields: s.errors });

  // headshot is optional; attached raw, renamed to the speaker: "<Name>.<original extension>"
  var photo = decodeImage(data.photo_b64, photoMime(data.photo_type, data.photo_name), s.name + photoExt(data.photo_type, data.photo_name));
  var photo2 = s.name2 ? decodeImage(data.photo2_b64, photoMime(data.photo2_type, data.photo2_name), s.name2 + photoExt(data.photo2_type, data.photo2_name)) : null;
  if (photo === 'too large' || photo2 === 'too large') return respond({ ok: false, error: 'image too large' });
  var attachments = [photo, photo2].filter(function (b) { return b; });

  var match = matchOutreach(s.outreach);
  var from = GmailApp.getAliases().indexOf(brand.from) !== -1 ? brand.from : Session.getEffectiveUser().getEmail();
  var cc = [];                                                     // organizer-facing: the speaker is NOT copied
  if (match.person && match.person.route) cc.push(match.person.route.replace('{brand}', brand.mail_domain || brand.site));

  var mail = composeSubmission(s, ev, match, brand);
  if (data.dry_run) {
    return respond({ ok: true, dry_run: true, subject: mail.subject, from: from, to: from, cc: cc, text: mail.text, html: mail.html,
                     match: match.person ? match.person.name : null, attachments: attachments.map(function (b) { return b.getName(); }) });
  }
  if (!dailyBudget()) return respond({ ok: false, error: 'too many today' });

  var options = { name: SENDER_NAME, replyTo: s.name2 ? s.email + ',' + s.email2 : s.email, htmlBody: mail.html };
  if (cc.length) options.cc = cc.join(',');
  if (attachments.length) options.attachments = attachments;
  if (from === brand.from) options.from = brand.from;
  var message = GmailApp.createDraft(from, mail.subject, mail.text, options).send();
  fileThread(message);
  recordWaitlist(s, ev, brandKey);
  Logger.log('Waitlist: %s (%s) for %s via %s -> cc %s', s.name, s.company, ev.event_name, match.person ? match.person.name : '(unmatched: ' + s.outreach + ')', cc.join(', '));
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

function composeSubmission(s, ev, match, brand) {
  var via = match.person ? match.person.name : (s.outreach ? s.outreach + ' (not matched)' : 'unknown');
  var subject = s.name + (s.name2 ? ' & ' + s.name2 : '') + ' - Waitlist - ' + ev.event_name;
  var rows = [
    ['Invited by', via], ['Name', s.name], ['Email', s.email], ['Organization', s.company], ['LinkedIn', s.linkedin],
    ['Talk Title', s.title], ['Talk Abstract', s.abstract], ['Bio', s.bio]
  ];
  if (s.name2) {   // visual divide, then the co-speaker block with plain labels
    rows = rows.concat([['__divider__', 'Co-speaker'], ['Name', s.name2], ['Email', s.email2], ['Organization', s.company2],
                        ['LinkedIn', s.linkedin2], ['Bio', s.bio2]]);
  }

  var text =
    'Waitlist - ' + ev.event_name + '\n\n' +
    'Applied after the lineup was full. Kept on the waitlist for the next ' + ev.brand_name + ' conference.\n\n' +
    rows.map(function (r) { return r[0] === '__divider__' ? '---------- ' + r[1] + ' ----------' : r[0] + ': ' + r[1]; }).join('\n\n') + '\n';

  var accent = brand.color || '#333';   // all labels in the brand colour
  var html =
    '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#222">' +
    '<h2 style="display:inline-block;background:' + accent + ';color:#fff;font-size:20px;margin:0 0 22px;padding:6px 12px;border-radius:4px">Waitlist - ' + esc(ev.event_name) + '</h2>' +
    '<p style="margin:-10px 0 18px;color:#555">Applied after the lineup was full. Kept on the waitlist for the next ' + esc(ev.brand_name) + ' conference.</p>' +
    '<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;max-width:640px">' +
    rows.map(function (r) {
      if (r[0] === '__divider__') {
        return '<tr><td colspan="2" style="padding:18px 0 8px"><div style="border-top:2px solid ' + accent + ';margin-bottom:10px"></div>' +
               '<span style="color:' + accent + ';font-weight:bold;font-size:16px">' + esc(r[1]) + '</span></td></tr>';
      }
      var v = /email$/i.test(r[0]) ? '<a href="mailto:' + esc(r[1]) + '">' + esc(r[1]) + '</a>'
            : /LinkedIn$/.test(r[0]) ? '<a href="' + esc(r[1]) + '">' + esc(r[1]) + '</a>'
            : esc(r[1]).replace(/\n/g, '<br>');
      var labelColor = r[0] === 'Invited by' ? '#111' : accent;   // Invited by always black (Marek)
      return '<tr><td style="color:' + labelColor + ';font-weight:bold;padding:4px 18px 4px 0;vertical-align:top;white-space:nowrap">' + r[0] + '</td>' +
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
    name2:    clean(d.name2, LIMITS.name).replace(/[\\\/:*?"<>|]/g, ''),
    company2: clean(d.company2, LIMITS.company),
    email2:   clean(d.email2, LIMITS.email).toLowerCase(),
    linkedin2: clean(d.linkedin2, LIMITS.linkedin),
    bio2:     cleanMultiline(d.bio2, LIMITS.bio),
    errors:   []
  };
  if (s.name2) {                                       // optional co-speaker: once named, the rest is required
    if (!s.company2) s.errors.push('company2');
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(s.email2)) s.errors.push('email2');
    if (!/^https?:\/\/(www\.)?linkedin\.com\/.+/i.test(s.linkedin2)) s.errors.push('linkedin2');
    if (!s.bio2) s.errors.push('bio2');
  }
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
    brand_name: clean(raw.brand_name, 40) || brand.site.replace(/\.[a-z]+$/, ''),
    event_name: clean(raw.event_name, 80) || (clean(raw.brand_name, 40) + ' ' + clean(raw.city, 60)).trim() || brand.site,
    event_url:  okUrl ? url.replace(/\/?$/, '/') : site + (slug ? slug + '/' : ''),
    slug:       slug,                              // for the sheet / status page
    city:       clean(raw.city, 60),
    date:       clean(raw.date, 40)
  };
}

var IMAGE_TYPES = { 'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/heic': '.heic', 'image/heif': '.heif', 'image/gif': '.gif', 'image/tiff': '.tif' };

// extension for the renamed attachment: from the mime type, else from the original file name, else .jpg
function photoExt(type, name) {
  type = String(type || '').toLowerCase();
  if (IMAGE_TYPES[type]) return IMAGE_TYPES[type];
  var m = /\.([a-z0-9]{2,5})$/i.exec(String(name || ''));
  return m ? '.' + m[1].toLowerCase().replace(/^jpeg$/, 'jpg') : '.jpg';
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

// ---- the waitlist sheet ---------------------------------------------------------------
// One spreadsheet, created on first use and remembered in WAITLIST_SHEET_ID. Never cleared by code.

var WL_COLUMNS = ['timestamp', 'brand', 'event_slug', 'event_name', 'city', 'event_date', 'name', 'company', 'email', 'linkedin', 'name2', 'linkedin2', 'title'];

function waitlistSheet() {
  var id = props().getProperty('WAITLIST_SHEET_ID'), ss = null;
  if (id) { try { ss = SpreadsheetApp.openById(id); } catch (err) { ss = null; } }
  if (!ss) {
    ss = SpreadsheetApp.create('Speaker waitlist');
    ss.getSheets()[0].appendRow(WL_COLUMNS);
    props().setProperty('WAITLIST_SHEET_ID', ss.getId());
    Logger.log('Created the waitlist sheet: ' + ss.getUrl());
  }
  return ss.getSheets()[0];
}

function recordWaitlist(s, ev, brandKey) {
  try {
    waitlistSheet().appendRow([new Date().toISOString(), brandKey, ev.slug, ev.event_name, ev.city, ev.date,
                               s.name, s.company, s.email, s.linkedin, s.name2, s.linkedin2, s.title]);
  } catch (err) {
    Logger.log('Sent, but could not record the waitlist row: ' + err);
  }
}

// Rows for the status build, without the email column. Dates come back as ISO strings.
function listWaitlist() {
  var values = waitlistSheet().getDataRange().getValues(), rows = [];
  for (var i = 1; i < values.length; i++) {
    var v = values[i];
    if (!v[0] || !v[6]) continue;
    rows.push({ ts: v[0] instanceof Date ? v[0].toISOString() : String(v[0]), brand: String(v[1]), slug: String(v[2]),
                event: String(v[3]), city: String(v[4]), date: v[5] instanceof Date ? Utilities.formatDate(v[5], 'UTC', 'MMMM d, yyyy') : String(v[5]),
                name: String(v[6]), company: String(v[7]), linkedin: String(v[9]), name2: String(v[10]), linkedin2: String(v[11]), title: String(v[12]) });
  }
  return rows;
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
  var d = readJson('WAITLIST_DAILY');
  if (d.day !== today) d = { day: today, count: 0 };
  if (d.count + 1 > DAILY_MAX) return false;
  d.count += 1;
  props().setProperty('WAITLIST_DAILY', JSON.stringify(d));
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
function testWaitlist() {
  var tiny = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
  var e = { postData: { contents: JSON.stringify({
    dry_run: true, brand: 'sreday', outreach: 'Magda', consent: true, waitlist: true,
    name: 'Leon Lobo', company: 'Oracle', jobtitle: 'Software Engineering Director', email: 'hello@sreday.com',
    linkedin: 'https://www.linkedin.com/in/leonlobo27/', title: 'Designing Hybrid Intelligence for Enterprise Finance',
    abstract: 'AI agents in Enterprise finance products improve user experiences.\n\nHowever, finance processes also depend on strict rules.\nThis session explores the balance.',
    bio: 'Leon Lobo is a Software Engineering Director at Oracle with 15+ years of experience.',
    photo_png_b64: tiny, photo_jpg_b64: '',
    event: { brand: 'sreday', brand_name: 'SREday', slug: '2026-london-q3', event_name: 'SREday London 2026 Q3', city: 'London', date: 'September 24, 2026', event_url: 'https://www.sreday.com/2026-london-q3/' }
  }) } };
  Logger.log(doPost(e).getContent());
  Logger.log('Sheet: ' + waitlistSheet().getParent().getUrl() + ' (' + listWaitlist().length + ' rows)');
}
