/**
 * Sponsor onboarding backend - Google Apps Script web app.
 *
 * Serves the hidden /<event>/onboardsponsor/ pages (template: _event_template/_templates/onboardsponsor.html,
 * facts built by _event_template/_build/generate.py). The page posts:
 *   { action: 'preview' | 'send' | 'schedule', pass, brand, company, first_name, emails: [...], items: ['booth', ...],
 *     logo_added: true (organizer confirmed the logo is on the website - required to send, the email says it is there),
 *     event: {...facts...}, website (honeypot), page }
 * The script owns the ONE "Info for sponsors" email (composeSponsorOnboarding below): a general part every
 * sponsor gets (logo, team tickets, kick-off intro, timeline) plus one section per toggled opportunity. The
 * opportunity ids are the purchasable sponsorship.yaml tier ids (leads, keynote, workshop, talk, booth,
 * logo_swag, food, clothing); the 'On request' tiers are deliberately not offered (too custom - Marek 2026-09-15).
 * On 'send' it emails From the brand alias To the sponsor team (everyone in To, they are one team), then moves
 * the thread to the Inbox as unread + important under the "Sponsor onboarding" label so replies land on it.
 * 'schedule' (delay_minutes, e.g. 60) instead leaves a Gmail DRAFT and a time-based trigger sends it later
 * (processQueue): Mark can still edit the draft, and deleting the draft cancels the send. New scope for that: run
 * testSchedule() once from the editor to grant the triggers permission before redeploying.
 * 'preview' returns subject + html only.
 *
 * Abuse guards (the /exec URL is public): passphrase checked against the Script Property
 * ONBOARDING_PASSPHRASE (shared with the speaker forms); 3 wrong attempts -> locked 15 min, 10 -> locked 24 h
 * (a correct passphrase resets the counter; clear a lock by deleting the SPONSOR_ONBOARDING_LOCK property);
 * daily cap of 30 sends; max 10 recipients per send; template, sender and links are pinned here, so a leaked
 * passphrase can only send THIS email to more people, never arbitrary content.
 *
 * Deploy (one-time, from the Google account that sends as mark@sreday.com / mark@llmday.com / mark@platformday.com):
 *   1. https://script.google.com -> New project -> paste this file -> save as "Sponsor onboarding".
 *   2. Project Settings (gear) -> Script properties -> add ONBOARDING_PASSPHRASE = <the passphrase>.
 *   3. Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone.
 *   4. Authorise the Gmail scope when prompted, copy the .../exec URL.
 *   5. Put that URL into home/metadata.yml -> sponsor_onboarding_form_url in sreday, llmday AND platformday, rebuild.
 *   Re-deploy after editing: Deploy -> Manage deployments -> edit -> new version (the URL stays the same).
 */

var BRANDS = {
  sreday:      { inbox: 'hello@sreday.com',      from: 'mark@sreday.com',      site: 'sreday.com',      code: 'SPONSOR' },
  llmday:      { inbox: 'hello@llmday.com',      from: 'mark@llmday.com',      site: 'llmday.com',      code: 'SPONSOR' },
  platformday: { inbox: 'hello@platformday.com', from: 'mark@platformday.com', site: 'platformday.com', code: 'SPONSOR' }
};
var SENDER_NAME = 'Mark Pawlikowski';
var LEAD_LABEL = 'Sponsor onboarding';
var MAX_RECIPIENTS = 10;
var DAILY_MAX_SENDS = 30;
var LOCK_STEPS = { 3: 15 * 60, 10: 24 * 60 * 60 };   // failed attempts -> lock seconds
var MAX_DELAY_MINUTES = 24 * 60;   // 'schedule' can defer a send by at most a day
var KNOWN_ITEMS = ['leads', 'booth', 'keynote', 'workshop', 'talk', 'logo_swag', 'food', 'clothing'];   // = pill order on the page

function doGet() {
  // Health check. Never reveals the passphrase, only whether one is configured and whether the endpoint is locked.
  var lock = readJson('SPONSOR_ONBOARDING_LOCK');
  return respond({ ok: true, service: 'sponsor-onboarding', passphrase_set: !!expectedPassphrase(),
                   failed_attempts: lock.count || 0, locked_for: currentLock(), queued: (readJson('SPONSOR_ONBOARDING_QUEUE').items || []).length, version: 3 });
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
  var company = clean(data.company, 80);
  var firstName = clean(data.first_name, 40);
  var items = normalizeItems(data.items);

  var from = GmailApp.getAliases().indexOf(brand.from) !== -1 ? brand.from : Session.getEffectiveUser().getEmail();

  if (data.action === 'preview') {
    var previewMail = composeSponsorOnboarding(ev, brand, company || 'Acme', firstName, items);
    return respond({ ok: true, subject: previewMail.subject, html: previewMail.html, text: previewMail.text, from: from,
                     to: emails.ok.join(', '), items: items });
  }
  if (!company) return respond({ ok: false, error: 'no company' });
  if (!emails.ok.length) return respond({ ok: false, error: 'no recipients' });
  if (data.logo_added !== true) return respond({ ok: false, error: 'no logo' });
  var delay = parseInt(data.delay_minutes, 10);
  delay = delay > 0 ? Math.min(delay, MAX_DELAY_MINUTES) : 0;
  if (data.action === 'schedule' && !delay) return respond({ ok: false, error: 'bad delay' });
  if (!dailyBudget()) return respond({ ok: false, error: 'too many today' });

  var mail = composeSponsorOnboarding(ev, brand, company, firstName, items);
  var options = { name: SENDER_NAME, htmlBody: mail.html };
  if (from === brand.from) options.from = brand.from;
  var draft = GmailApp.createDraft(emails.ok.join(','), mail.subject, mail.text, options);

  if (data.action === 'schedule') {
    var due = Date.now() + delay * 60000;
    try {
      scheduleTrigger(delay * 60000 + 15000);
    } catch (err) {                                   // triggers scope not granted yet (run testSchedule once)
      try { draft.deleteDraft(); } catch (e2) {}
      Logger.log('Cannot schedule, trigger permission missing: ' + err);
      return respond({ ok: false, error: 'no trigger permission' });
    }
    enqueue({ id: draft.getId(), due: due, subject: mail.subject, recipients: emails.ok.length });
    Logger.log('Sponsor onboarding scheduled: %s -> %s at %s (draft %s)', ev.event_name, company, new Date(due).toISOString(), draft.getId());
    return respond({ ok: true, scheduled: emails.ok.length, at: new Date(due).toISOString(), delay_minutes: delay });
  }

  var message = draft.send();
  fileThread(message);
  Logger.log('Sponsor onboarding sent: %s -> %s [%s] items: %s from %s', ev.event_name, company, emails.ok.join(', '), items.join(','), from);
  return respond({ ok: true, sent: emails.ok.length, items: items });
}

// Pull the sent message's thread into the Inbox (unread, important, labelled) - a mail sent from this very
// account would otherwise sit read in "Sent" only.
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

// ---- scheduled sends ----------------------------------------------------------
// Queue of {id: draftId, due: epoch ms, subject, recipients} in the SPONSOR_ONBOARDING_QUEUE property; a one-off
// time-based trigger runs processQueue(), which sends every due draft still present (a deleted draft = cancelled).

function enqueue(item) {
  var q = readJson('SPONSOR_ONBOARDING_QUEUE');
  q.items = (q.items || []).concat([item]);
  props().setProperty('SPONSOR_ONBOARDING_QUEUE', JSON.stringify(q));
}

function scheduleTrigger(ms) {
  ScriptApp.newTrigger('processQueue').timeBased().after(Math.max(ms, 60000)).create();
}

function clearQueueTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() !== 'processQueue') return;
    try { ScriptApp.deleteTrigger(t); } catch (err) { Logger.log('Could not delete trigger (Apps Script flake, harmless): ' + err); }
  });
}

function processQueue() {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var items = readJson('SPONSOR_ONBOARDING_QUEUE').items || [], now = Date.now(), keep = [];
    items.forEach(function (it) {
      if (it.due > now + 30000) { keep.push(it); return; }       // not due yet (triggers fire with ~1 min granularity)
      var draft = null;
      try { draft = GmailApp.getDraft(it.id); } catch (err) { draft = null; }
      if (!draft) { Logger.log('Scheduled draft gone, treating as cancelled: ' + it.subject); return; }
      try {
        fileThread(draft.send());
        Logger.log('Scheduled sponsor onboarding sent: %s (%s recipients)', it.subject, it.recipients);
      } catch (err) {
        Logger.log('Scheduled send FAILED for %s: %s', it.subject, err);
      }
    });
    props().setProperty('SPONSOR_ONBOARDING_QUEUE', JSON.stringify({ items: keep }));
    clearQueueTriggers();
    if (keep.length) {
      var next = Math.min.apply(null, keep.map(function (i) { return i.due; }));
      scheduleTrigger(next - Date.now() + 15000);
    }
  } finally {
    lock.releaseLock();
  }
}

// Run ONCE from the editor after pasting v3: creates + removes a trigger so Google asks for the new
// "manage triggers" permission. Without this, 'schedule' calls from the website fail.
function testSchedule() {
  var t = ScriptApp.newTrigger('processQueue').timeBased().after(60 * 60 * 1000).create();
  // deleteTrigger right after create() sometimes throws "Unexpected error ... deleteTrigger" - harmless:
  // the leftover trigger just runs processQueue once in an hour and cleans itself up.
  try { ScriptApp.deleteTrigger(t); } catch (err) { Logger.log('Trigger created (permission OK) but immediate delete failed: ' + err); }
  Logger.log('Trigger permission OK. Queue: ' + JSON.stringify(readJson('SPONSOR_ONBOARDING_QUEUE')));
}

// ---- the email --------------------------------------------------------------
// Same tiny markup as the speaker onboarding script: *bold*, [label](url), "- " bullets ("\n" inside a bullet =
// continuation line), "1. " numbered items, "" = blank line, "[y] "/"[g] " prefix = yellow/green highlighted
// line (HTML only). renderText()/renderHtml() turn the same lines into the plain-text and HTML bodies.
// Wording mined from Mark's real sponsor emails (2024-2026) + the /sponsorship FAQ; v2 = Marek's edits 2026-09-15
// (no paperwork block, no 'reply OK' line, FAQ link instead of the package upsell); v3 = sections in pill order with the
// short names as headings; 2026-09-15 later: no in-person line, 2-line intro (greeting + venue), 'Your sponsorship'.
// Do not re-word without asking.

function composeSponsorOnboarding(ev, brand, company, firstName, items) {
  var has = function (id) { return items.indexOf(id) !== -1; };
  var talkMin = Math.max(ev.slot_minutes - 5, 5);
  var code = ev.sponsor_code || brand.code;
  var sponsorsAnchor = ev.event_url + '#sponsors';

  var lines = [
    firstName ? 'Hey ' + firstName + ',' : 'Hello!',
    '',
    'Great to have ' + company + ' on board for [' + ev.event_name + '](' + ev.event_url + ') on ' + ev.date + '!',
    'Venue: *' + ev.venue_name + '*' + (ev.venue_address && ev.venue_address !== ev.venue_name ? ', ' + ev.venue_address : ''),
    ''
  ];
  if (ev.extra) { lines.push(ev.extra); lines.push(''); }

  // -- what they signed up for (only when something is toggled)
  if (items.length) {
    lines.push('*Your sponsorship:*');
    lines.push('');
    items.forEach(function (id) {
      var it = ev.items_by_id[id];
      if (it) lines.push('- ' + it.name + (it.benefits.length ? ' - ' + it.benefits.join(' / ') : ''));
    });
    lines.push('');
  }

  // -- basics, every sponsor
  lines = lines.concat([
    '*Next steps:*',
    '',
    '- Your logo is added here: [' + sponsorsAnchor + '](' + sponsorsAnchor + ") with your regular URL. Let us know if you'd like to change it, use an UTM, etc.",
    '- Your team: register everyone here [' + ev.tickets_url + '](' + ev.tickets_url + ') with the free code *' + code + '*\n  (the "add coupon" is tricky to find, but it\'s there in the top right corner of the luma window)',
    "- There's no limit on the code, but we recommend staffing with 2-3 people for this size of event.",
    "- You can also invite your local clients and friends to attend at no charge, happy to accommodate as long as we're not maxed out.",
    "- Kick-off: we introduce all sponsors as we open the day - we show all logos, say which sponsor does what, and you get a minute on the microphone to present what you're doing.",
    ''
  ]);

  // -- per-opportunity sections, in the pill order (KNOWN_ITEMS): the three session kinds share one block
  var SESSION_IDS = ['keynote', 'workshop', 'talk'];
  var label = function (id) { return ev.items_by_id[id] ? ev.items_by_id[id].name : id; };
  var sections = {
    leads: function () {
      return [
        '- We share the pre-conference leads on the Monday before the conference, and then the final list on the next working day after the conference. Tell us which email address should receive them.',
        "- All attendees have their LinkedIn QR codes on the badges, so your team can scan and connect on the spot. Attendees accept to be contacted by sponsors in our terms and conditions.",
        "- After the event you can also pick up to 10 attendees or speakers you'd genuinely like to connect with, and we'll send a friendly intro email with your team in cc. More on that after the conference."
      ];
    },
    booth: function () {
      return [
        '- The booth is a regular office table (around 150x100 cm), with space for your rollup banner, swag and a monitor you can plug in, along with some electricity sockets. Whatever you bring can go on top.',
        "- The rollup banner is brought by the sponsor, we don't print those - a medium one (around 200x50 cm) fits best. I'd recommend keeping the setup simple, there won't be a massive space around each booth.",
        '- Setup: your team can come as early as 7:30 in the morning, allowing 90 minutes before the kick-off.',
        "- Shipping: if you'd like to ship materials ahead, let me know and I'll share the delivery address and contact at the venue.",
        "- Tip for the day: our events are community-driven and practitioner-first, so the right approach is to go towards people rather than being passive at the booth. A conversation starting with \"I heard you use X to solve Y\" goes a long way compared to \"scan this QR code, here's your swag\". Just a recommendation, you do you!"
      ];
    },
    sessions: function () {
      var out = [];
      if (has('keynote')) out.push('- Keynote: ' + talkMin + ' mins talk + 5 mins Q&A, in the morning with the full audience in the room, no parallel talks.');
      if (has('workshop')) out.push("- Workshop: a full hour, hands-on. Unlike regular sessions, participants are encouraged to walk around, ask questions and engage. Tell us what attendees should bring or install in advance and we'll announce it with the schedule.");
      if (has('talk')) out.push('- Regular session: ' + talkMin + ' mins talk + 5 mins Q&A, in the afternoon. It may run alongside parallel tracks.');
      return out.concat([
        "- The format and content are completely up to the speaker. Our audience is very open to hear about sponsored products, but might get pushed off by straight sales pitches - show how the product solves a real problem instead.",
        '- Please share the speaker details at your earliest convenience, the quickest way is this form: [' + ev.fasttrack_url + '](' + ev.fasttrack_url + '), or just reply with:',
        '',
        "1. Speaker's LinkedIn URL",
        '1. Talk title (single phrase, shorter = better)',
        '1. Talk abstract (3+ phrases, uncapped)',
        "1. Speaker's short bio",
        "1. Speaker's picture (only if the one on LinkedIn is poor quality)",
        "1. Speaker's direct email for onboarding (we'll keep you in cc)",
        '',
        "As soon as we get those, we'll add the session to the website, start promoting it and share the promotional assets with you. Speakers present from their own laptop (HDMI / USB-C), and talks are recorded - we share the video 1-2 weeks after the conference."
      ]);
    },
    logo_swag: function () {
      return [
        '- Your logo goes on the website and you get a shoutout on stage at the kick-off.',
        "- Swag: your team can drop it off with us in the morning and we'll distribute it to all attendees during the breaks. Stickers, small items and printed material work best. Tell us what and how many you're bringing so we can prepare the space."
      ];
    },
    food: function () {
      return [
        "- Tell us which break you're taking (coffee break, lunch or happy hour). We make a proper announcement that the break is sponsored by " + company + ", you can put a rollup banner and your stickers around the tables (and keep them there during the whole day), and we give your team the microphone in the lobby to say a word before people head off.",
        '- We can also distribute your swag during the break, run a short demo, whatever makes sense to your team - just need to know in advance so we can do the groundwork :-)',
        "- Happy hour is where people open up and the juiciest conversations happen, so please invite your team to stay over for it."
      ];
    },
    clothing: function () {
      return [
        "- Please share your logo in vector format (SVG / PDF). We'll confirm the item, sizes and quantity with you before ordering, the price per person depends on the item."
      ];
    }
  };
  var sessionsDone = false;
  KNOWN_ITEMS.forEach(function (id) {
    if (!has(id)) return;
    if (SESSION_IDS.indexOf(id) !== -1) {
      if (sessionsDone) return;
      sessionsDone = true;
      var picked = SESSION_IDS.filter(has).map(label);
      lines.push('*' + picked.join(' + ') + ':*'); lines.push('');
      lines = lines.concat(sections.sessions(), ['']);
      return;
    }
    if (!sections[id]) return;
    lines.push('*' + label(id) + ':*'); lines.push('');
    lines = lines.concat(sections[id](), ['']);
  });

  // -- timeline + ask
  lines = lines.concat([
    '*What happens now:*',
    '',
    "- As speakers confirm, we update the website and prepare social media graphics and promo posts for sharing.",
    '- The schedule is published as soon as most speakers are confirmed, things might still move a bit until then.',
    '- After the event: photos within a few days, talk videos 1-2 weeks later.',
    '',
    "I'm here for any questions,",
    'Mark',
    '',
    '*FAQ:*',
    '',
    '1. Expected attendance - around ' + (ev.attendees || 100) + ' people, mid-senior level practitioners',
    '1. Who attends: roughly 59% software, platform and DevOps engineers, 22% founders and engineering leaders, 14% business, consulting and product, 5% ML/AI/data. Having chatty, seasoned technical folks at the event is recommended.',
    '1. There will be WiFi access in the venue',
    '1. Previous ' + ev.brand_name + ' talks: ' + (ev.youtube_url ? '[' + ev.youtube_url + '](' + ev.youtube_url + ')' : 'on our YouTube channel'),
    '1. Prefer a call? Grab a slot: [' + ev.calendly_url + '](' + ev.calendly_url + ')',
    '1. More frequently asked questions: [' + ev.faq_url + '](' + ev.faq_url + ')'
  ]);
  return {
    subject: "Sponsor's onboarding - " + company + ' at ' + ev.event_name,
    text: renderText(lines),
    html: renderHtml(lines)
  };
}

var INLINE_LINK = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
var INLINE_BOLD = /\*([^*\n]+)\*/g;
var HIGHLIGHT = { y: '#fff59d', g: '#c8e6c9' };
var FONT = 'font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#111';

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
  function flushPara() { if (para.length) { out.push('<p style="' + FONT + '">' + para.join('<br>') + '</p>'); para = []; } }
  lines.forEach(function (l) {
    var m;
    if ((m = /^- ([\s\S]*)$/.exec(l))) { flushPara(); if (list !== 'ul') { closeList(); out.push('<ul style="' + FONT + '">'); list = 'ul'; } out.push('<li style="' + FONT + '">' + inline(m[1]) + '</li>'); }
    else if ((m = /^\d+\. (.*)$/.exec(l))) { flushPara(); if (list !== 'ol') { closeList(); out.push('<ol style="' + FONT + '">'); list = 'ol'; } out.push('<li style="' + FONT + '">' + inline(m[1]) + '</li>'); }
    else if ((m = /^\[([yg])\] (.*)$/.exec(l))) { flushPara(); closeList(); out.push('<p style="' + FONT + '"><span style="background:' + HIGHLIGHT[m[1]] + '">' + inline(m[2]) + '</span></p>'); }
    else if (l === '') { flushPara(); closeList(); }
    else { closeList(); para.push(inline(l)); }
  });
  flushPara(); closeList();
  return '<div style="' + FONT + '">' + out.join('\n') + '</div>';
}

// ---- validation -------------------------------------------------------------

function normalizeEvent(raw, brand) {
  var slug = /^[a-z0-9-]{3,60}$/.test(String(raw.slug || '')) ? String(raw.slug) : '';
  var site = 'https://' + brand.site + '/';
  var okUrl = function (u, fallback) {
    u = clean(u, 300);
    return (/^https:\/\/(www\.)?/.test(u) && u.replace(/^https:\/\/(www\.)?/, '').indexOf(brand.site + '/') === 0) ? u : fallback;
  };
  var eventUrl = okUrl(raw.event_url, site + (slug ? slug + '/' : ''));
  var ev = {
    brand_name:       clean(raw.brand_name, 40) || brand.site.replace(/\.com$/, ''),
    event_name:       clean(raw.event_name, 80),
    city:             clean(raw.city, 60) || 'town',
    date:             clean(raw.date, 60) || 'the conference day',
    month_day:        clean(raw.month_day, 40),
    event_url:        eventUrl,
    tickets_url:      okUrl(raw.tickets_url, eventUrl + '#tickets'),
    sponsor_page_url: okUrl(raw.sponsor_page_url, eventUrl + 'sponsorship.html'),
    fasttrack_url:    eventUrl + 'fasttrack/',
    faq_url:          eventUrl + 'sponsorship#faq',
    host_url:         okUrl(raw.host_url, site + 'host'),
    venue_name:       clean(raw.venue_name, 120) || 'the venue',
    venue_address:    clean(raw.venue_address, 200),
    attendees:        parseInt(raw.attendees, 10) > 0 ? parseInt(raw.attendees, 10) : 0,
    youtube_url:      /^https:\/\/(www\.)?youtube\.com\//.test(String(raw.youtube_url || '')) ? clean(raw.youtube_url, 200) : '',
    calendly_url:     /^https:\/\/calendly\.com\//.test(String(raw.calendly_url || '')) ? clean(raw.calendly_url, 200) : 'https://calendly.com/sreday/30min',
    slot_minutes:     parseInt(raw.slot_minutes, 10) >= 10 && parseInt(raw.slot_minutes, 10) <= 90 ? parseInt(raw.slot_minutes, 10) : 30,
    sponsor_code:     /^[A-Z0-9]{3,20}$/.test(String(raw.sponsor_code || '')) ? String(raw.sponsor_code) : '',
    extra:            clean(raw.extra, 600),
    items_by_id:      {}
  };
  (Array.isArray(raw.items) ? raw.items : []).slice(0, 30).forEach(function (it) {
    var id = String((it && it.id) || '');
    if (KNOWN_ITEMS.indexOf(id) === -1) return;
    ev.items_by_id[id] = { name: clean(it.name, 60) || id,
                           benefits: (Array.isArray(it.benefits) ? it.benefits : []).slice(0, 4).map(function (b) { return clean(b, 120); }).filter(Boolean) };
  });
  if (!ev.event_name) ev.event_name = ev.brand_name + ' ' + ev.city;
  if (!ev.month_day) ev.month_day = ev.date.replace(/,\s*\d{4}\s*$/, '');
  return ev;
}

function normalizeItems(values) {
  var out = [];
  (Array.isArray(values) ? values : []).slice(0, 30).forEach(function (v) {
    var id = String(v || '').toLowerCase();
    if (KNOWN_ITEMS.indexOf(id) !== -1 && out.indexOf(id) === -1) out.push(id);
  });
  // keep the pill order, whatever order the page sent them in
  return KNOWN_ITEMS.filter(function (id) { return out.indexOf(id) !== -1; });
}

function normalizeEmails(values) {
  var ok = [], bad = [], seen = {};
  (Array.isArray(values) ? values : []).slice(0, 50).forEach(function (v) {
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
  var lock = readJson('SPONSOR_ONBOARDING_LOCK');
  var left = Math.ceil(((lock.locked_until || 0) - Date.now()) / 1000);
  return left > 0 ? left : 0;
}

// Count a wrong passphrase; returns lock seconds when this attempt trips a threshold, else 0.
function registerFailure() {
  var lock = readJson('SPONSOR_ONBOARDING_LOCK');
  lock.count = (lock.count || 0) + 1;
  var secs = LOCK_STEPS[lock.count] || 0;
  if (secs) lock.locked_until = Date.now() + secs * 1000;
  if (lock.count >= 10) lock.count = 0;          // after the 24 h lock the ladder starts again
  props().setProperty('SPONSOR_ONBOARDING_LOCK', JSON.stringify(lock));
  return secs;
}

function resetFailures() {
  props().deleteProperty('SPONSOR_ONBOARDING_LOCK');
}

// true when today's budget allows another send (and records it)
function dailyBudget() {
  var today = Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd');
  var d = readJson('SPONSOR_ONBOARDING_DAILY');
  if (d.day !== today) d = { day: today, sends: 0 };
  if (d.sends + 1 > DAILY_MAX_SENDS) return false;
  d.sends += 1;
  props().setProperty('SPONSOR_ONBOARDING_DAILY', JSON.stringify(d));
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
function testSponsorOnboarding() {
  var e = { postData: { contents: JSON.stringify({
    action: 'preview',
    pass: props().getProperty('ONBOARDING_PASSPHRASE'),
    brand: 'sreday',
    company: 'Acme',
    first_name: 'Anna',
    emails: ['hello@sreday.com'],
    items: ['booth', 'talk', 'leads', 'logo_swag'],
    event: {
      brand: 'sreday', brand_name: 'SREday', slug: '2026-san-francisco-q4',
      event_name: 'SREday San Francisco 2026 Q4', city: 'San Francisco', date: 'October 2, 2026', month_day: 'October 2',
      event_url: 'https://www.sreday.com/2026-san-francisco-q4/', tickets_url: 'https://www.sreday.com/2026-san-francisco-q4/#tickets',
      sponsor_page_url: 'https://www.sreday.com/2026-san-francisco-q4/sponsorship.html', host_url: 'https://www.sreday.com/host',
      venue_name: 'Harness Office', venue_address: '55 Stockton St, San Francisco, CA 94108', attendees: 100,
      youtube_url: 'https://www.youtube.com/@sreday', calendly_url: 'https://calendly.com/sreday/30min',
      slot_minutes: 30, event_size: 'medium', sponsor_code: '', extra: '',
      items: [{ id: 'booth', name: 'Booth / Table', benefits: ['Table (150 x 100cm), optional monitor', 'Electricity, Wi-Fi, space for a rollup banner'] },
              { id: 'talk', name: 'Regular Session Slot', benefits: ['30-minute afternoon slot', 'May run alongside parallel tracks'] },
              { id: 'leads', name: 'Conference Leads', benefits: ['Receive full attendee contact list'] },
              { id: 'logo_swag', name: 'Logo Sponsor + Swag', benefits: ['Logo on the website, on-stage mention', 'Swag distributed to all attendees'] }]
    }
  }) } };
  Logger.log(doPost(e).getContent());
}
