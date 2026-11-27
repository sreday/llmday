/**
 * Deck dump backend - Google Apps Script web app ("Deck dump").
 *
 * Serves the hidden /deckdump/ page on every brand's home site (template: home/_templates/deckdump.html, lineups built
 * by home/_build/generate.py). A speaker picks their conference and their talk from the lineup and drops their latest
 * slides. The page trims the file losslessly in the browser first, then:
 *   1. {action: "start"}  - this script checks the request, names the file "<Event> - <Speaker(s)> - <short_url>.<ext>"
 *      and opens a Drive resumable upload session in ONE shared folder (FOLDER_ID, the shared drive).
 *      The browser PUTs the file straight to Google in 8 MB chunks, so the size is not limited by Apps Script.
 *      Fallback when the browser cannot reach the session (CORS): {action: "upload"} with the file as base64 (35 MB cap).
 *   2. {action: "finish"} - stamps the file's Drive description with JSON {brand, folder, short_url, speaker, title,
 *      ...}, trashes the older upload for the same talk (newest replaces older, Marek 2026-10-30; the Drive trash keeps
 *      it 30 days) and emails the speaker a receipt when they left an email.
 *   3. GET ?list=1&token=<DECKDUMP_TOKEN> - the manifest for _build/deckdump_pull.py (GitHub Actions, every repo):
 *      every finished deck + a short-lived Drive access token so the job can download the files directly.
 *   4. {action: "report", token} - unused since 2026-10-31 (Marek wants no Deck dump emails); kept for manual use.
 *   5. {action: "scan", token, query} - decks already sitting in this mailbox (v2, 2026-10-31): returns only metadata
 *      (sender, subject, date, deck attachments, Drive/Slides links) so Claude can match them to talks cheaply;
 *      {action: "import", token, items} copies the matched ones into the folder server-side (Gmail attachment or Drive
 *      file; Google Slides exported to PDF), dated by the email, never replacing a newer upload for the same talk.
 *   6. PDFs only (v3, Marek 2026-10-31: "I want to have decks uploaded to the drive in PDF"): the page sends PDF only;
 *      imports convert PowerPoint/ODP to PDF through Google Slides (toPdf); Keynote cannot be converted and is reported.
 *      {action: "convert", token} converts the non-PDFs already in the folder once (original to the trash).
 *   7. {action: "remind", token, ...} (v4, Marek 2026-11-27): conference day, 12:00 local - ONE email to the speakers
 *      with no deck yet (names from the pull job, addresses from the brand's WWW sheet). See remind() below.
 *      Setup: Script property WWW_SHEETS = {"sreday":"<sheet id>","llmday":"<id>","platformday":"<id>","pec":"<id>"}
 *      (never in this file: the repo is public), then run testRemind once (authorises Sheets), then a new version.
 *      v5 (2026-11-27): late: true = the "late ask" wording, for events that ended a month or more ago.
 *      v6 (2026-11-27): {action: "mark_sent", token, brand, folder, note} records an event as reminded without
 *      sending (a reminder sent by hand, or an event Marek skipped), so the week-after catch-up leaves it alone.
 *
 * Abuse guards (no passphrase - speakers use this from the onboarding email): honeypot, daily cap (DECKDUMP_DAILY),
 * file type + size caps, names and keys checked against strict patterns. The page only offers lineup talks.
 *
 * Deploy (one-time, from the Google account that sends as mark@sreday.com / mark@llmday.com / mark@platformday.com):
 *   1. https://script.google.com -> New project -> paste this file -> save as "Deck dump".
 *   2. Project settings -> Script properties -> add DECKDUMP_TOKEN = a long random string (the pull job's key).
 *      The decks go to the "Deck Dump" folder in the shared Google Drive (FOLDER_ID below, Marek 2026-10-31: not My Drive);
 *      the script's account must be Content manager there so it can trash replaced decks. DECKDUMP_FOLDER_ID overrides it.
 *   3. Run testDeckdump once from the editor (authorises Drive + Gmail + external requests, creates the folder).
 *   4. Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone. Copy the .../exec URL.
 *   5. Put that URL into home/metadata.yml -> deckdump_form_url in all four repos, rebuild.
 *   6. GitHub -> each repo -> Settings -> Secrets -> Actions -> DECKDUMP_FEED = "<exec URL>?list=1&token=<DECKDUMP_TOKEN>".
 *   Re-deploy after editing: Deploy -> Manage deployments -> edit -> new version (the URL stays the same).
 */

var BRANDS = {
  sreday:      { from: 'mark@sreday.com',      site: 'sreday.com',      color: '#713660', name: 'SREday' },
  llmday:      { from: 'mark@llmday.com',      site: 'llmday.com',      color: '#26986A', name: 'LLMday' },
  platformday: { from: 'mark@platformday.com', site: 'platformday.com', color: '#E2971D', name: 'PLATFORMday' },
  // PEC: no PEC mail aliases exist, so it sends from the LLMday alias
  pec:         { from: 'mark@llmday.com', site: 'promptengineering.rocks', color: '#6b40d8', name: 'Prompt Engineering Conference' }
};
var SENDER_NAME = 'Mark Pawlikowski';
var LABEL = 'Deck dump';
var DAILY_MAX = 150;                          // uploads started per day, all brands together
var FOLDER_ID = '1-pce3jozMgjl3qjvdQIculgga5oBmMEY';   // "Deck Dump" in the shared drive (Marek 2026-10-31); DECKDUMP_FOLDER_ID overrides
var MAX_BYTES = 200 * 1024 * 1024;            // same cap as the page
var MAX_FALLBACK_BYTES = 35 * 1024 * 1024;    // base64 through this script (+33%; Apps Script payloads stop around 50 MB)
var TYPES = {
  pdf:  'application/pdf',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  key:  'application/x-iwork-keynote-sffkey',
  odp:  'application/vnd.oasis.opendocument.presentation'
};

function doGet(e) {
  var p = (e && e.parameter) || {};
  if (p.list) {                      // the pull job: ?list=1&token=<DECKDUMP_TOKEN> -> decks + a Drive token, as JSON
    if (!tokenOk(p.token)) return respond({ ok: false, error: 'forbidden' });
    return respond({ ok: true, access_token: ScriptApp.getOAuthToken(), files: listDecks() });
  }
  return respond({ ok: true, service: 'deckdump', version: 6 });   // v4: conference-day reminder; v5: late ask; v6: mark_sent
}

function doPost(e) {
  var data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (err) {
    return respond({ ok: false, error: 'bad json' });
  }
  if (data.website) return respond({ ok: true, upload_url: '', honeypot: true });   // honeypot -> pretend success
  if (data.action === 'report') return report(data);
  if (data.action === 'scan') return scanInbox(data);
  if (data.action === 'import') return importDecks(data);
  if (data.action === 'convert') return convertFolder(data);
  if (data.action === 'remind') return remind(data);
  if (data.action === 'mark_sent') return markSent(data);

  var brandKey = String(data.brand || '').toLowerCase();
  var brand = BRANDS[brandKey];
  if (!brand) return respond({ ok: false, error: 'bad brand' });
  var d = normalizeDeck(data, brandKey);
  if (d.errors.length) return respond({ ok: false, error: 'invalid', fields: d.errors });
  if (d.ext !== 'pdf') return respond({ ok: false, error: 'pdf only' });   // Marek 2026-10-31: the folder holds PDFs only

  if (data.action === 'start') {
    if (data.dry_run) return respond({ ok: true, dry_run: true, name: d.filename, meta: d.meta });
    if (!dailyBudget()) return respond({ ok: false, error: 'too many today' });
    try {
      return respond({ ok: true, upload_url: startSession(d, originFor(data.origin, brand)), name: d.filename });
    } catch (err) {
      Logger.log('Could not open an upload session: ' + err);
      return respond({ ok: false, error: 'drive' });
    }
  }
  if (data.action === 'upload') {                                  // fallback: the file itself, base64
    var b64 = String(data.file_b64 || '').replace(/^data:[^,]*,/, '').replace(/\s+/g, '');
    if (!b64) return respond({ ok: false, error: 'invalid', fields: ['file'] });
    if (b64.length * 3 / 4 > MAX_FALLBACK_BYTES) return respond({ ok: false, error: 'too large' });
    if (data.dry_run) return respond({ ok: true, dry_run: true, name: d.filename, meta: d.meta });
    if (!dailyBudget()) return respond({ ok: false, error: 'too many today' });
    var blob = Utilities.newBlob(Utilities.base64Decode(b64), TYPES[d.ext], d.filename);
    var file = deckFolder().createFile(blob);
    return respond(finish(file, d, brand));
  }
  if (data.action === 'finish') {
    var f = findUploaded(String(data.file_id || ''), d.filename);
    if (!f) return respond({ ok: false, error: 'not found' });
    return respond(finish(f, d, brand));
  }
  return respond({ ok: false, error: 'bad action' });
}

// ---- the request ---------------------------------------------------------------------

function normalizeDeck(data, brandKey) {
  var errors = [];
  var ev = data.event || {}, talk = data.talk || {}, file = data.file || {};
  var folder = String(ev.folder || '');
  if (!/^20\d\d-[a-z0-9-]{2,60}$/.test(folder)) errors.push('event');
  var shortUrl = String(talk.short_url || '');
  if (!/^[A-Za-z0-9_]{3,100}$/.test(shortUrl)) errors.push('talk');
  var speaker = clean(talk.name, 160);
  if (!speaker) errors.push('talk');
  var m = /\.([a-z0-9]{2,5})$/i.exec(String(file.name || ''));
  var ext = m ? m[1].toLowerCase() : '';
  if (!TYPES[ext]) errors.push('file type');
  var size = Number(file.size) || 0;
  if (size <= 0 || size > MAX_BYTES) errors.push('file size');
  var email = clean(data.email, 254);
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) errors.push('email');
  var eventName = clean(ev.event_name, 80) || folder;
  // "<Event> - <Speaker(s)> - <short_url>.<ext>": sorts by event, readable in Drive, the key makes it unique
  var filename = (eventName + ' - ' + speaker.replace(/[\\\/:*?"<>|]+/g, ' ') + ' - ' + shortUrl).replace(/\s+/g, ' ').slice(0, 220) + '.' + ext;
  return {
    errors: errors, ext: ext, size: size, filename: filename, email: email,
    meta: {
      deckdump: 1, brand: brandKey, folder: folder, event: eventName, short_url: shortUrl, speaker: speaker,
      title: clean(talk.title, 200), email: email, original_name: clean(file.name, 200), ext: ext,
      trimmed: !!data.trimmed, original_size: Number(data.original_size) || size, uploaded_at: ''
    }
  };
}

function key(meta) { return [meta.brand, meta.folder, meta.short_url].join('|'); }

// the page's own origin when it belongs to the brand, else https://<site>: Drive answers CORS only for this origin
function originFor(origin, brand) {
  origin = String(origin || '');
  var bare = origin.replace(/^https:\/\/(www\.)?/, '');
  return (/^https:\/\//.test(origin) && bare === brand.site) ? origin : 'https://' + brand.site;
}

// ---- Drive ----------------------------------------------------------------------------

// the shared-drive folder; never a silent stand-in in My Drive: no access = a loud error (testDeckdump shows it)
function deckFolder() {
  var id = props().getProperty('DECKDUMP_FOLDER_ID') || FOLDER_ID;
  var m = /[-\w]{25,}/.exec(String(id).replace(/^.*\/folders\//, ''));   // a pasted folder link works too
  if (m) id = m[0];
  try {
    return DriveApp.getFolderById(id);
  } catch (err) {
    throw new Error('No access to the Deck dump folder ' + id + ' - is this account a Content manager of the shared drive? (' + err + ')');
  }
}

// Drive API v3 resumable session, opened with this script's own token; the browser uploads to the returned URL
function startSession(d, origin) {
  var meta = JSON.parse(JSON.stringify(d.meta));
  meta.status = 'uploading';
  var res = UrlFetchApp.fetch('https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&supportsAllDrives=true&fields=id,name', {
    method: 'post',
    contentType: 'application/json; charset=UTF-8',
    headers: {
      Authorization: 'Bearer ' + ScriptApp.getOAuthToken(),
      'X-Upload-Content-Type': TYPES[d.ext],
      'X-Upload-Content-Length': String(d.size),
      Origin: origin
    },
    payload: JSON.stringify({ name: d.filename, parents: [deckFolder().getId()], mimeType: TYPES[d.ext], description: JSON.stringify(meta) }),
    muteHttpExceptions: true
  });
  var url = res.getHeaders().Location || res.getHeaders().location;
  if (res.getResponseCode() !== 200 || !url) throw new Error('HTTP ' + res.getResponseCode() + ' ' + res.getContentText().slice(0, 300));
  return url;
}

// the upload the browser just finished: by id when the page could read it, else the newest file with that name
function findUploaded(id, name) {
  var folderId = deckFolder().getId();
  if (/^[\w-]{10,}$/.test(id)) {
    try {
      var f = DriveApp.getFileById(id), parents = f.getParents();
      while (parents.hasNext()) if (parents.next().getId() === folderId) return f;
    } catch (err) {}
  }
  var it = deckFolder().getFilesByName(name), best = null;
  while (it.hasNext()) {
    var g = it.next();
    if (!best || g.getDateCreated() > best.getDateCreated()) best = g;
  }
  return best;
}

function finish(file, d, brand, when) {
  var meta = JSON.parse(JSON.stringify(d.meta));
  meta.uploaded_at = (when || new Date()).toISOString();
  meta.status = 'ok';
  file.setDescription(JSON.stringify(meta));
  if (file.getName() !== d.filename) file.setName(d.filename);
  var replaced = 0, it = deckFolder().getFiles();
  while (it.hasNext()) {                       // newest replaces older: one deck per talk in the folder
    var g = it.next();
    if (g.getId() === file.getId()) continue;
    var gm = readMeta(g);
    if (gm && key(gm) === key(meta)) { g.setTrashed(true); replaced++; }
  }
  if (d.email) receipt(d, brand, meta);
  Logger.log('Deck dump: %s for %s (%s bytes, trimmed %s), replaced %s', meta.speaker, meta.event, file.getSize(), meta.trimmed, replaced);
  return { ok: true, file_id: file.getId(), name: file.getName(), replaced: replaced };
}

function readMeta(file) {
  try {
    var m = JSON.parse(file.getDescription() || '');
    return m && m.deckdump ? m : null;
  } catch (err) {
    return null;
  }
}

// finished decks for the pull job (uploads still in flight have status "uploading" and are left out)
function listDecks() {
  var out = [], it = deckFolder().getFiles();
  while (it.hasNext()) {
    var f = it.next(), m = readMeta(f);
    if (!m || m.status !== 'ok') continue;
    out.push({ id: f.getId(), name: f.getName(), size: f.getSize(), mime: f.getMimeType(),
               modified: f.getLastUpdated().toISOString(), meta: m });
  }
  return out;
}

// ---- decks already in the inbox (v2) ------------------------------------------------------
var DECK_EXT = /\.(pdf|pptx|key|odp)$/i;
var LINK_RE = /https:\/\/(?:docs\.google\.com\/presentation\/d\/|drive\.google\.com\/file\/d\/|drive\.google\.com\/open\?id=)([-\w]{20,})/g;

function scanInbox(data) {
  if (!tokenOk(data.token)) return respond({ ok: false, error: 'forbidden' });
  var query = String(data.query || ''), start = Number(data.start) || 0, max = Math.min(Number(data.max) || 100, 200);
  var threads = GmailApp.search(query, start, max), out = [];
  threads.forEach(function (t) {
    t.getMessages().forEach(function (m) {
      var atts = m.getAttachments({ includeInlineImages: false }).filter(function (a) { return DECK_EXT.test(a.getName()) && a.getSize() > 150 * 1024; })
        .map(function (a) { return { name: a.getName(), size: a.getSize() }; });
      var links = {}, body = m.getPlainBody() || '', x;
      LINK_RE.lastIndex = 0;
      while ((x = LINK_RE.exec(body))) links[x[1]] = 1;
      if (!atts.length && !Object.keys(links).length) return;
      var linked = Object.keys(links).map(function (id) {                // what each link is, when this account can open it
        try { var f = DriveApp.getFileById(id); return { id: id, name: f.getName(), mime: f.getMimeType(), deck: isDeckFile(f) }; }
        catch (err) { return { id: id, error: 'no access' }; }
      });
      out.push({ id: m.getId(), date: m.getDate().toISOString(), from: m.getFrom(), subject: m.getSubject().slice(0, 140),
                 attachments: atts, links: linked });
    });
  });
  return respond({ ok: true, account: Session.getEffectiveUser().getEmail(), threads: threads.length, messages: out });
}

function isDeckFile(f) {
  return f.getMimeType() === MimeType.GOOGLE_SLIDES || DECK_EXT.test(f.getName());
}

function importDecks(data) {
  if (!tokenOk(data.token)) return respond({ ok: false, error: 'forbidden' });
  var results = [];
  (data.items || []).slice(0, 15).forEach(function (it) {
    var r = { short_url: it.short_url, speaker: it.speaker };
    try {
      var msg = GmailApp.getMessageById(String(it.message_id)), when = msg.getDate(), blob = null, name = '';
      if (it.attachment) {
        var a = msg.getAttachments({ includeInlineImages: false }).filter(function (x) { return x.getName() === it.attachment; })[0];
        if (!a) throw new Error('attachment not found');
        blob = a.copyBlob(); name = a.getName();
      } else if (it.drive_id) {
        var f = DriveApp.getFileById(String(it.drive_id));
        if (!isDeckFile(f)) throw new Error('not a deck: ' + f.getName() + ' (' + f.getMimeType() + ')');
        if (f.getMimeType() === MimeType.GOOGLE_SLIDES) { blob = f.getAs('application/pdf'); name = f.getName() + '.pdf'; }
        else { blob = f.getBlob(); name = f.getName(); }
        if (!DECK_EXT.test(name)) name += '.pdf';
      } else {
        throw new Error('nothing to import');
      }
      if (!/\.pdf$/i.test(name)) { blob = toPdf(blob, name); name = name.replace(/\.[a-z0-9]+$/i, '') + '.pdf'; }
      var d = normalizeDeck({ event: { folder: it.folder, event_name: it.event }, talk: { short_url: it.short_url, name: it.speaker, title: it.title },
                              file: { name: name, size: blob.getBytes().length } }, String(it.brand || ''));
      if (!BRANDS[it.brand] || d.errors.length) throw new Error('invalid: ' + d.errors.join(', '));
      d.meta.source = 'email';
      var newer = listDecks().filter(function (x) { return key(x.meta) === key(d.meta) && x.meta.uploaded_at > when.toISOString(); });
      if (newer.length) { r.skipped = 'a newer upload exists'; results.push(r); return; }
      blob.setName(d.filename);
      if (d.ext !== 'pdf' || blob.getContentType() !== TYPES.pdf) blob.setContentType(TYPES[d.ext]);
      var file = deckFolder().createFile(blob);
      d.email = '';                                                 // no receipt for imports
      var done = finish(file, d, BRANDS[it.brand], when);
      r.ok = true; r.name = done.name; r.replaced = done.replaced;
    } catch (err) {
      r.error = String(err).slice(0, 200);
    }
    results.push(r);
  });
  return respond({ ok: true, results: results });
}

// PowerPoint / ODP -> PDF through Google Slides: upload as a converted Slides file (resumable, so big decks never pass
// through a byte array), export it as PDF, trash the temporary Slides file. Keynote has no converter.
function toPdf(blob, name) {
  var ext = (/\.([a-z0-9]+)$/i.exec(name || '') || [])[1];
  ext = ext ? ext.toLowerCase() : '';
  if (ext === 'key') throw new Error('Keynote file: needs a PDF export');
  if (!TYPES[ext] || ext === 'pdf') throw new Error('cannot convert ' + name);
  var auth = { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() };
  var init = UrlFetchApp.fetch('https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id', {
    method: 'post', contentType: 'application/json; charset=UTF-8', muteHttpExceptions: true,
    headers: { Authorization: auth.Authorization, 'X-Upload-Content-Type': TYPES[ext] },
    payload: JSON.stringify({ name: 'deckdump-convert-' + Date.now(), mimeType: MimeType.GOOGLE_SLIDES })
  });
  var loc = init.getHeaders().Location || init.getHeaders().location;
  if (init.getResponseCode() !== 200 || !loc) throw new Error('convert: ' + init.getResponseCode() + ' ' + init.getContentText().slice(0, 200));
  var up = UrlFetchApp.fetch(loc, { method: 'put', contentType: TYPES[ext], payload: blob.getBytes(), muteHttpExceptions: true });
  if (up.getResponseCode() !== 200) throw new Error('convert upload: ' + up.getResponseCode() + ' ' + up.getContentText().slice(0, 200));
  var id = JSON.parse(up.getContentText()).id;
  try {
    var ex = UrlFetchApp.fetch('https://www.googleapis.com/drive/v3/files/' + id + '/export?mimeType=application%2Fpdf', { headers: auth, muteHttpExceptions: true });
    if (ex.getResponseCode() !== 200) throw new Error('export: ' + ex.getResponseCode() + ' ' + ex.getContentText().slice(0, 200));
    return ex.getBlob().setContentType('application/pdf');
  } finally {
    try { DriveApp.getFileById(id).setTrashed(true); } catch (err) {}
  }
}

// one-off: every non-PDF already in the folder becomes a PDF with the same talk data; the original goes to the trash
function convertFolder(data) {
  if (!tokenOk(data.token)) return respond({ ok: false, error: 'forbidden' });
  var out = [], started = Date.now();
  listDecks().forEach(function (x) {
    if (/\.pdf$/i.test(x.name) || Date.now() - started > 300000) return;     // stay inside the 6-minute limit; call again
    var r = { name: x.name };
    try {
      var f = DriveApp.getFileById(x.id), pdf = toPdf(f.getBlob(), x.name);
      var meta = JSON.parse(JSON.stringify(x.meta));
      meta.ext = 'pdf'; meta.converted_from = x.name.split('.').pop().toLowerCase();
      var name = x.name.replace(/\.[a-z0-9]+$/i, '') + '.pdf';
      var nf = deckFolder().createFile(pdf.setName(name));
      nf.setDescription(JSON.stringify(meta));
      f.setTrashed(true);
      r.ok = true; r.pdf = name;
    } catch (err) {
      r.error = String(err).slice(0, 200);
    }
    out.push(r);
  });
  var left = listDecks().filter(function (x) { return !/\.pdf$/i.test(x.name); }).length;
  return respond({ ok: true, results: out, non_pdf_left: left });
}

// ---- mail -----------------------------------------------------------------------------

function sender(brand) {
  return GmailApp.getAliases().indexOf(brand.from) !== -1 ? brand.from : Session.getEffectiveUser().getEmail();
}

function receipt(d, brand, meta) {
  try {
    var first = meta.speaker.split(/\s*(?:&|,| and )\s*/)[0].split(/\s+/)[0];
    var text = 'Hey ' + first + ',\n\n' +
      'Thanks, we got your slides for ' + meta.event + '!\n\n' +
      meta.title + '\n' + meta.original_name + '\n\n' +
      'If you change anything, just upload again on the same page and the new version replaces this one.\n\n' +
      'See you there!\nMark';
    var from = sender(brand), options = { name: SENDER_NAME };
    if (from === brand.from) options.from = brand.from;
    GmailApp.sendEmail(d.email, 'Your slides for ' + meta.event, text, options);
  } catch (err) {
    Logger.log('Saved, but could not send the receipt: ' + err);
  }
}

// the pull job's email to Mark: it composes the text, this script only sends it from the brand alias
function report(data) {
  if (!tokenOk(data.token)) return respond({ ok: false, error: 'forbidden' });
  var brand = BRANDS[String(data.brand || '').toLowerCase()];
  if (!brand) return respond({ ok: false, error: 'bad brand' });
  var subject = clean(data.subject, 200), text = String(data.text || '').slice(0, 50000);
  if (!subject || !text) return respond({ ok: false, error: 'invalid' });
  var from = sender(brand);
  var options = { name: SENDER_NAME };
  if (data.html) options.htmlBody = String(data.html).slice(0, 200000);
  if (from === brand.from) options.from = brand.from;
  var message = GmailApp.createDraft(from, subject, text, options).send();
  fileThread(message);
  return respond({ ok: true });
}

function fileThread(message) {
  try {
    var thread = message.getThread();
    thread.moveToInbox();
    thread.markUnread();
    var label = GmailApp.getUserLabelByName(LABEL) || GmailApp.createLabel(LABEL);
    thread.addLabel(label);
  } catch (err) {
    Logger.log('Sent, but could not file the thread: ' + err);
  }
}

// ---- budget, helpers ------------------------------------------------------------------

function props() { return PropertiesService.getScriptProperties(); }

function tokenOk(t) {
  var token = props().getProperty('DECKDUMP_TOKEN') || '';
  return !!token && String(t || '') === token;
}

function dailyBudget() {
  var today = Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd');
  var d = readJson('DECKDUMP_DAILY');
  if (d.day !== today) d = { day: today, count: 0 };
  if (d.count + 1 > DAILY_MAX) return false;
  d.count += 1;
  props().setProperty('DECKDUMP_DAILY', JSON.stringify(d));
  return true;
}

function readJson(k) {
  try { return JSON.parse(props().getProperty(k) || '{}') || {}; } catch (e) { return {}; }
}
function respond(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
function clean(v, max) {
  return String(v == null ? '' : v).replace(/[\x00-\x1f\x7f]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max);
}

// ---- conference-day reminder (v4, Marek 2026-11-27) ------------------------------------------
// {action: "remind", token, brand, folder, event_name, speakers: [{name, title}], dry, test_to, late}
// Sent by _build/deckdump_pull.py --remind (GitHub Actions, hourly) on day 1 of an event, from 12:00 local time, with
// the NAMES of speakers who have no deck yet. The addresses are looked up here, in the brand's WWW sheet (ids in the
// Script Property WWW_SHEETS, never in this public file), and never leave this script: the response holds names and
// counts only, because Actions logs of public repos are public. ONE email from mark@<brand>, speakers in Bcc, filed
// under the "Deck dump" label and archived (a reply brings the thread back to the Inbox). A second call for the same
// event sends nothing (Script Property REMIND_SENT|<brand>|<folder>).
var REMIND_MAX = 50;
var TEAM_DOMAINS = /@(sreday|llmday|platformday)\.com$/i;   // test_to may only be one of ours

function remind(data) {
  if (!tokenOk(data.token)) return respond({ ok: false, error: 'forbidden' });
  var brandKey = String(data.brand || '').toLowerCase(), brand = BRANDS[brandKey];
  if (!brand) return respond({ ok: false, error: 'bad brand' });
  var folder = String(data.folder || '');
  if (!/^20\d\d-[a-z0-9-]+$/.test(folder)) return respond({ ok: false, error: 'bad folder' });
  var eventName = clean(data.event_name, 120) || folder;
  var testTo = clean(data.test_to, 120).toLowerCase();
  if (testTo && !TEAM_DOMAINS.test(testTo)) return respond({ ok: false, error: 'test_to must be a team address' });
  var sentKey = 'REMIND_SENT|' + brandKey + '|' + folder;
  var already = props().getProperty(sentKey);
  if (already && !testTo && !data.dry) return respond({ ok: true, already_sent: already });

  var names = (data.speakers || []).slice(0, 100).map(function (s) { return clean(s && s.name, 120); }).filter(String);
  if (!names.length) return respond({ ok: false, error: 'no speakers' });
  var found;
  try {
    found = speakerEmails(brandKey, folder, names);
  } catch (err) {
    return respond({ ok: false, error: String(err.message || err) });
  }
  var emails = found.emails.filter(function (e, i, a) { return a.indexOf(e) === i; });
  if (!emails.length) return respond({ ok: false, error: 'no addresses found', tab: found.tab, unmatched: found.unmatched });
  if (emails.length > REMIND_MAX) return respond({ ok: false, error: 'more than ' + REMIND_MAX + ' recipients' });

  var mail = composeReminder(brand, folder, eventName, !!data.late);
  var out = { ok: true, tab: found.tab, recipients: emails.length, matched: found.matched, unmatched: found.unmatched };
  if (data.dry) {
    out.dry = true; out.subject = mail.subject; out.text = mail.text;
    return respond(out);
  }
  var from = sender(brand), options = { name: SENDER_NAME, htmlBody: mail.html, bcc: (testTo ? [testTo] : emails).join(',') };
  if (from === brand.from) options.from = brand.from;
  var message = GmailApp.createDraft(from, mail.subject, mail.text, options).send();
  fileQuietly(message);
  if (!testTo) props().setProperty(sentKey, new Date().toISOString() + ' to ' + emails.length);
  Logger.log('Deck dump reminder: %s -> %s (bcc, from %s)', eventName, testTo || emails.length + ' speakers', from);
  if (testTo) out.recipients = 1;
  out.sent = true;
  return respond(out);
}

// Record an event as reminded without sending: the reminder went out by hand, or Marek skipped the event.
function markSent(data) {
  if (!tokenOk(data.token)) return respond({ ok: false, error: 'forbidden' });
  var brandKey = String(data.brand || '').toLowerCase(), folder = String(data.folder || '');
  if (!BRANDS[brandKey] || !/^20\d\d-[a-z0-9-]+$/.test(folder)) return respond({ ok: false, error: 'invalid' });
  var sentKey = 'REMIND_SENT|' + brandKey + '|' + folder, already = props().getProperty(sentKey);
  if (already) return respond({ ok: true, already_sent: already });
  props().setProperty(sentKey, new Date().toISOString() + ' marked: ' + clean(data.note, 80));
  return respond({ ok: true, marked: true });
}

// Marek's wording (2026-11-27), "here" = the bold link to the conference-specific deck dump page. late = the event
// ended a month or more ago: same email, the first line owns up to the late ask.
function composeReminder(brand, folder, eventName, late) {
  var url = 'https://' + brand.site + '/deckdump/?event=' + encodeURIComponent(folder);
  var subject = late ? 'Your slides from ' + eventName : 'Reminder to upload your slides - ' + eventName;
  var ask = late ? 'We still don\'t have your slides from ' + eventName + ' - sorry for the late ask! Could you upload them '
                 : 'We don\'t have your slides yet, could you upload them ';
  var text = 'Hey!\n\n' +
    ask + 'here: ' + url + ' ?\n\n' +
    'Attendees can browse the slides on the website, and we can use the PDF to fix any problems with your session ' +
    'recording if needed - it\'s more handy than it seems!\n\n' +
    'PDF works best!\n\n' +
    'Thanks a lot!\nMark\n';
  var html = '<div>Hey!</div><br>' +
    '<div>' + ask + '<b><a href="' + url + '">here</a></b>?</div><br>' +
    '<div>Attendees can browse the slides on the website, and we can use the PDF to fix any problems with your session ' +
    'recording if needed - it\'s more handy than it seems!</div><br>' +
    '<div>PDF works best!</div><br>' +
    '<div>Thanks a lot!<br>Mark</div>';
  return { subject: subject, text: text, html: html };
}

// The event's tab: among the tabs of the brand's WWW sheet that name the event's year (and its quarter, when the tab
// names one; copies, backups and placeholders skipped), the one where most of these speakers are found. Row layout:
// a "name" column, and the address in the unlabelled column (the first cell of the row that looks like an address).
function speakerEmails(brandKey, folder, names) {
  var ids = readJson('WWW_SHEETS'), id = ids[brandKey];
  if (!id) throw new Error('no WWW sheet for ' + brandKey + ' in the WWW_SHEETS script property');
  var year = folder.slice(0, 4), q = (folder.match(/-q(\d)$/) || [])[1];
  var want = names.map(normName), best = null;
  SpreadsheetApp.openById(id).getSheets().forEach(function (sh) {
    var title = sh.getName();
    if (title.indexOf(year) < 0 || /copy of|backup|old|placeholder/i.test(title)) return;
    if (q && /\bq\d\b/i.test(title) && !new RegExp('\\bq' + q + '\\b', 'i').test(title)) return;
    var rows = sh.getDataRange().getDisplayValues();
    if (!rows.length) return;
    var col = rows[0].map(function (h) { return String(h).trim().toLowerCase(); }).indexOf('name');
    if (col < 0) return;
    var map = {};
    rows.slice(1).forEach(function (r) {
      var addr = r.filter(function (c) { return /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(String(c).trim()); })[0];
      String(r[col] || '').split(/\s*(?:&|,| and )\s*/).forEach(function (n) {
        var k = normName(n);
        if (k && addr && !map[k]) map[k] = String(addr).trim().toLowerCase();
      });
    });
    var hits = want.filter(function (k) { return map[k]; }).length;
    if (hits && (!best || hits > best.hits)) best = { tab: title, map: map, hits: hits };
  });
  if (!best) throw new Error('no ' + year + ' tab of the WWW sheet lists these speakers');
  var out = { tab: best.tab, emails: [], matched: [], unmatched: [] };
  names.forEach(function (n, i) {
    var e = best.map[want[i]];
    if (e) { out.emails.push(e); out.matched.push(n); } else out.unmatched.push(n);
  });
  return out;
}

function normName(v) {   // = norm_name in _build/deckdump_pull.py: accents off, letters only
  return String(v || '').toLowerCase().split(String.fromCharCode(322)).join('l')   // the Polish l with a stroke
    .normalize('NFD').replace(/[^\x00-\x7f]/g, '')
    .replace(/[^a-z]+/g, ' ').trim();
}

// Sent mail lives in Sent: archive the copy that lands in this mailbox, label it; a reply un-archives the thread.
function fileQuietly(message) {
  try {
    var thread = message.getThread();
    thread.moveToArchive();
    thread.markRead();
    var label = GmailApp.getUserLabelByName(LABEL) || GmailApp.createLabel(LABEL);
    thread.addLabel(label);
  } catch (err) {
    Logger.log('Sent, but could not file the thread: ' + err);
  }
}

// Editor test: authorises Sheets, then dry-runs a reminder (nothing is sent). Needs WWW_SHEETS set first.
function testRemind() {
  var e = { postData: { contents: JSON.stringify({
    action: 'remind', token: props().getProperty('DECKDUMP_TOKEN'), brand: 'llmday', folder: '2026-hamburg-q4',
    event_name: 'LLMday Hamburg 2026 Q4', speakers: [{ name: 'Marcel Koert' }, { name: 'Nobody Here' }], dry: true
  }) } };
  Logger.log(doPost(e).getContent());
}

// ---- editor test: authorises the scopes, creates the folder, dry-runs a start ------------
function testDeckdump() {
  Logger.log('Folder: ' + deckFolder().getUrl());
  Logger.log('Aliases: ' + GmailApp.getAliases().join(', '));
  UrlFetchApp.fetch('https://www.googleapis.com/drive/v3/about?fields=user', { headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() } });
  var e = { postData: { contents: JSON.stringify({
    action: 'start', dry_run: true, brand: 'sreday',
    event: { folder: '2026-london-q4', event_name: 'SREday London 2026 Q4' },
    talk: { short_url: 'Test_Speaker_Example_Test_talk', name: 'Test Speaker', title: 'Test talk' },
    file: { name: 'my deck.pdf', size: 1234567, type: 'application/pdf' }
  }) } };
  Logger.log(doPost(e).getContent());
  Logger.log('Decks in the folder: ' + listDecks().length);
}
