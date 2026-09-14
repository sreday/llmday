/**
 * Speaker invitation letter backend - Google Apps Script web app ("convince your boss").
 *
 * Serves the hidden /<event>/invitation/ pages (template: _event_template/_templates/invitation.html,
 * facts built by _event_template/_build/generate.py as `invitation_event`). The page posts:
 *   { action: 'preview' | 'send', pass, brand, speaker_name, speaker_email, cc_emails: [...],
 *     event: {...facts...}, website (honeypot), page }
 * The script owns the ONE letter (composeInvitation below), fills it with the event facts and on 'send'
 * emails it From the brand alias straight To the speaker, Cc their marketing / comms contacts when given.
 * The "About the event" paragraph adapts to the lineup tier computed at build time (early / building /
 * strong = confirmed talks vs 12 slots per track, same maths as /status/); the "your team" sentence and
 * the sponsorship paragraph appear only when someone is in Cc. The thread is then moved to the Inbox as
 * unread + important under the "Speaker invitations" label. 'preview' returns subject + html only.
 *
 * Abuse guards (the /exec URL is public): passphrase checked against the Script Property
 * ONBOARDING_PASSPHRASE (set it to the SAME value as the onboarding project, so one passphrase unlocks
 * both pages); 3 wrong attempts -> locked 15 min, 10 -> locked 24 h (a correct passphrase resets the
 * counter; clear a lock by deleting the INVITATION_LOCK property); daily cap of 30 letters; max 5 Cc;
 * template, sender and links are pinned here (URLs must be on the brand domain), so a leaked passphrase
 * can only send THIS letter to more people, never arbitrary content.
 *
 * Deploy (one-time, from the Google account that sends as mark@sreday.com / mark@llmday.com / mark@platformday.com):
 *   1. https://script.google.com -> New project -> paste this file -> save as "Speaker invitation".
 *   2. Project Settings (gear) -> Script properties -> add ONBOARDING_PASSPHRASE = <the onboarding passphrase>.
 *   3. Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone.
 *   4. Authorise the Gmail scope when prompted, copy the .../exec URL.
 *   5. Put that URL into home/metadata.yml -> invitation_form_url in sreday, llmday AND platformday, rebuild.
 *   Re-deploy after editing: Deploy -> Manage deployments -> edit -> new version (the URL stays the same).
 */

// scope = what the talks are about, crowd = who sits in the room (used by the early / building paragraphs)
var BRANDS = {
  sreday:      { from: 'mark@sreday.com',      site: 'sreday.com',
                 scope: 'SRE, platform engineering, observability, incident response and running AI systems in production',
                 crowd: 'mostly SREs, platform and DevOps engineers, engineering managers and CTOs' },
  llmday:      { from: 'mark@llmday.com',      site: 'llmday.com',
                 scope: 'LLMs, AI agents, RAG, evaluation and shipping AI products to production',
                 crowd: 'mostly AI and ML engineers, software engineers, engineering managers and CTOs' },
  platformday: { from: 'mark@platformday.com', site: 'platformday.com',
                 scope: 'platform engineering, developer experience, internal developer platforms, infrastructure and DevOps',
                 crowd: 'mostly platform and DevOps engineers, SREs, engineering managers and CTOs' }
};
var SENDER_NAME = 'Mark Pawlikowski';
var LEAD_LABEL = 'Speaker invitations';
var MAX_CC = 5;
var DAILY_MAX_SENDS = 30;
var LOCK_STEPS = { 3: 15 * 60, 10: 24 * 60 * 60 };   // failed attempts -> lock seconds
var MAX_COMPANIES = 10;                              // "Speakers come from A, B, ... and others"
var MAX_TOPICS = 4;                                  // "Most talks so far are about a, b, c, and d"
var MAX_SPONSORS = 6;
var VERSION = 3;   // v2: host company; v3: bullet structure (About / apply / Talk format / FAQ), previous edition link

function doGet() {
  // Health check. Never reveals the passphrase, only whether one is configured and whether the endpoint is locked.
  var lock = readJson('INVITATION_LOCK');
  return respond({ ok: true, service: 'speaker-invitation', passphrase_set: !!expectedPassphrase(),
                   failed_attempts: lock.count || 0, locked_for: currentLock(), version: VERSION });
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
  var speakerName = clean(data.speaker_name, 60);
  var speaker = normalizeEmails([data.speaker_email]);
  var cc = normalizeEmails(data.cc_emails);
  if (speaker.bad.length || cc.bad.length) return respond({ ok: false, error: 'invalid emails', bad: speaker.bad.concat(cc.bad) });
  if (!speakerName || !speaker.ok.length) return respond({ ok: false, error: 'no speaker' });
  cc.ok = cc.ok.filter(function (c) { return c !== speaker.ok[0]; });
  if (cc.ok.length > MAX_CC) return respond({ ok: false, error: 'too many cc' });

  var mail = composeInvitation(ev, brand, speakerName, cc.ok.length > 0);
  var from = GmailApp.getAliases().indexOf(brand.from) !== -1 ? brand.from : Session.getEffectiveUser().getEmail();

  if (data.action === 'preview') {
    return respond({ ok: true, subject: mail.subject, html: mail.html, text: mail.text, from: from, to: speaker.ok[0], cc: cc.ok, tier: ev.tier });
  }
  if (!dailyBudget()) return respond({ ok: false, error: 'too many today' });

  var options = { name: SENDER_NAME, htmlBody: mail.html };
  if (cc.ok.length) options.cc = cc.ok.join(',');
  if (from === brand.from) options.from = brand.from;
  var message = GmailApp.createDraft(speaker.ok[0], mail.subject, mail.text, options).send();
  fileThread(message);
  Logger.log('Invitation sent: %s -> %s <%s> cc [%s] from %s (%s)', ev.event_name, speakerName, speaker.ok[0], cc.ok.join(', '), from, ev.tier);
  return respond({ ok: true, sent: 1, to: speaker.ok[0], cc: cc.ok });
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

// ---- the letter -------------------------------------------------------------
// Same tiny markup as onboarding-form.gs: *bold*, [label](url), "- " bullets, "" = blank line.
// Structure and wording: Marek, 2026-09-14 evening (edited from the first real send). Do not re-word without asking.

function composeInvitation(ev, brand, firstName, hasCc) {
  var talk = Math.max(ev.slot_minutes - 5, 5);
  var lines = [
    'Hey ' + firstName + ',',
    '',
    "We'd love to have you speak at [" + ev.event_name + '](' + ev.event_url + ') on ' + ev.date + ' at ' + ev.venue_name + ', ' + ev.city + '. ' +
      'This is the official invite from the ' + ev.brand_name + ' team, feel free to pass it on to whoever needs to sign off on it.',
    ''
  ];
  if (hasCc) {
    lines.push("I'm copying your team so they have all the details too.");
    lines.push('');
  }
  lines.push('*About the event*');
  lines = lines.concat(aboutBullets(ev, brand));
  lines = lines.concat([
    '',
    '*You can apply to speak here:* [' + ev.fasttrack_url + '](' + ev.fasttrack_url + ')',
    '',
    '*Talk format*',
    '- ' + ev.slot_minutes + ' minutes on stage, in person (' + talk + ' min talk + 5 min Q&A)',
    '- We record it and put it on our YouTube channel, free for anyone to watch',
    '',
    '*FAQ*',
    '- Speaking is free. No fee, no sponsorship strings attached.',
    '- Your company name will appear on the speaker page and in the schedule.',
    "- The talk will work best if it's technical or experience-based, vendor pitches don't work very well with our crowd.",
    '- Unfortunately, we do not cover speaker fee, travel, or accommodation.',
    ''
  ]);
  if (hasCc) {
    lines.push('And since your team is on this email: we still have sponsorship opportunities available for ' + ev.event_name +
               ", in case that's of interest. The options are at [" + ev.sponsor_page_url + '](' + ev.sponsor_page_url + ") and I'm happy to walk through them on a call.");
    lines.push('');
  }
  lines = lines.concat([
    'Would be great to have you on the lineup! If your team needs anything else from us, just reply here or grab a slot: [' + ev.calendly_url + '](' + ev.calendly_url + ')',
    '',
    'Cheers,',
    'Mark from ' + ev.brand_name + ' Team'
  ]);
  return {
    subject: "You're invited to speak at " + ev.event_name + ' - ' + ev.date,
    text: renderText(lines),
    html: renderHtml(lines)
  };
}

// The adaptive "About the event" bullets. Tier comes from generate.py (confirmed talks vs 12 slots per track):
//   strong   (50 % +)  talks / tracks / attendees, speakers + top topics, crowd, sponsors, previous edition
//   building (25-49 %) "N confirmed so far, around T planned", speakers + topic scope, crowd, sponsors, previous edition
//   early    (< 25 %)  "N confirmed so far, around T planned", topic scope, crowd, sponsors, previous edition
function aboutBullets(ev, brand) {
  var tracks = ev.tracks + ' track' + (ev.tracks === 1 ? '' : 's');
  var out = ['- ' + ev.event_name + ' is a single day, in person event' + (ev.host_company ? ' hosted by ' + ev.host_company : '')];
  if (ev.tier === 'strong') {
    out.push('- ' + ev.confirmed + ' confirmed talks / ' + tracks + ' / ' + ev.attendees + ' expected attendees');
    var s = '';
    if (ev.companies.length) s += 'Speakers come from ' + listOf(ev.companies.slice(0, MAX_COMPANIES), ev.companies.length > MAX_COMPANIES) + '.';
    if (ev.topics.length) s += (s ? ' ' : '') + 'Most talks so far are about ' + topicsPhrase(ev.topics) + '.';
    if (s) out.push('- ' + s);
  } else {
    out.push('- ' + ev.confirmed + ' confirmed talk' + (ev.confirmed === 1 ? '' : 's') + ' so far, around ' + ev.talks_target + ' planned / ' + tracks + ' / ' + ev.attendees + ' expected attendees');
    if (ev.tier === 'building' && ev.companies.length) {
      out.push('- Speakers so far come from ' + listOf(ev.companies.slice(0, MAX_COMPANIES), ev.companies.length > MAX_COMPANIES) + '. Topics span ' + brand.scope + '.');
    } else {
      out.push('- Topics: ' + brand.scope);
    }
  }
  out.push('- Crowd: ' + brand.crowd + '.');
  if (ev.sponsors.length) out.push('- Confirmed sponsors: ' + listOf(ev.sponsors.slice(0, MAX_SPONSORS), ev.sponsors.length > MAX_SPONSORS) + '.');
  if (ev.previous && ev.previous.url) {
    out.push('- ' + (ev.previous.same_city ? 'Previous edition' : 'Our most recent event') + ', for reference: [' + ev.previous.event_name + '](' + ev.previous.url + ')' +
             (ev.previous.talks ? ' (' + ev.previous.talks + ' talks)' : ''));
  }
  return out;
}

// "A, B and C" / "A, B, C and others"
function listOf(items, andOthers) {
  items = items.slice();
  if (andOthers) return items.join(', ') + ' and others';
  if (items.length <= 1) return items.join('');
  return items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1];
}

// top N categories by talk count (ties keep about.yaml order), "&" -> "and", Oxford comma; Title Case words
// become lowercase, acronyms and brand casing stay ("Site Reliability Engineering" -> "site reliability
// engineering", "DevOps & Automation" -> "DevOps and automation", "AI Agents" -> "AI agents")
function topicsPhrase(topics) {
  var top = topics.slice().sort(function (a, b) { return b.count - a.count; }).slice(0, MAX_TOPICS)
                  .map(function (t) {
                    return t.name.replace(/&/g, 'and').split(' ').map(function (w) {
                      return /^[A-Z][a-z]+$/.test(w) ? w.toLowerCase() : w;
                    }).join(' ');
                  });
  if (top.length <= 1) return top.join('');
  if (top.length === 2) return top[0] + ' and ' + top[1];
  return top.slice(0, -1).join(', ') + ', and ' + top[top.length - 1];
}

var INLINE_LINK = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
var INLINE_BOLD = /\*([^*\n]+)\*/g;

function renderText(lines) {
  return lines.map(function (l) {
    return l.replace(INLINE_LINK, function (_, label, url) { return label === url ? url : label + ' (' + url + ')'; })
            .replace(INLINE_BOLD, '$1');
  }).join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

function renderHtml(lines) {
  var out = [], list = false, para = [];
  function closeList() { if (list) { out.push('</ul>'); list = false; } }
  function flushPara() { if (para.length) { out.push('<p>' + para.join('<br>') + '</p>'); para = []; } }
  function inline(s) {
    return esc(s).replace(INLINE_LINK, function (_, label, url) { return '<a href="' + url + '">' + label + '</a>'; })
                 .replace(INLINE_BOLD, '<b>$1</b>');
  }
  lines.forEach(function (l) {
    var m;
    if ((m = /^- ([\s\S]*)$/.exec(l))) { flushPara(); if (!list) { out.push('<ul>'); list = true; } out.push('<li>' + inline(m[1]) + '</li>'); }
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
  var names = function (v, max, len) {
    var seen = {}, out = [];
    (Array.isArray(v) ? v : []).slice(0, max).forEach(function (x) {
      var s = clean(x, len);
      if (s && !seen[s.toLowerCase()]) { seen[s.toLowerCase()] = true; out.push(s); }
    });
    return out;
  };
  var eventUrl = okUrl(raw.event_url, site + (slug ? slug + '/' : ''));
  var tier = String(raw.tier || '').toLowerCase();
  var prev = raw.previous && typeof raw.previous === 'object' ? raw.previous : null;
  var ev = {
    brand_name:       clean(raw.brand_name, 40) || brand.site.replace(/\.com$/, ''),
    event_name:       clean(raw.event_name, 80),
    city:             clean(raw.city, 60) || 'town',
    date:             clean(raw.date, 60) || 'the conference day',
    event_url:        eventUrl,
    fasttrack_url:    okUrl(raw.fasttrack_url, eventUrl + 'fasttrack/'),
    sponsor_page_url: okUrl(raw.sponsor_page_url, eventUrl + '#sponsors'),
    venue_name:       clean(raw.venue_name, 120) || 'the venue',
    attendees:        parseInt(raw.attendees, 10) > 0 ? parseInt(raw.attendees, 10) : 100,
    youtube_url:      /^https:\/\/(www\.)?youtube\.com\//.test(String(raw.youtube_url || '')) ? clean(raw.youtube_url, 200) : 'https://www.youtube.com/@' + brand.site.replace(/\.com$/, ''),
    calendly_url:     /^https:\/\/calendly\.com\//.test(String(raw.calendly_url || '')) ? clean(raw.calendly_url, 200) : 'https://calendly.com/sreday/30min',
    slot_minutes:     parseInt(raw.slot_minutes, 10) >= 10 && parseInt(raw.slot_minutes, 10) <= 90 ? parseInt(raw.slot_minutes, 10) : 30,
    tracks:           parseInt(raw.tracks, 10) >= 1 && parseInt(raw.tracks, 10) <= 10 ? parseInt(raw.tracks, 10) : 1,
    confirmed:        parseInt(raw.confirmed, 10) >= 0 ? parseInt(raw.confirmed, 10) : 0,
    talks_target:     parseInt(raw.talks_target, 10) > 0 ? parseInt(raw.talks_target, 10) : 12,
    tier:             tier === 'strong' || tier === 'building' ? tier : 'early',
    companies:        names(raw.companies, 80, 60),
    topics:           (Array.isArray(raw.topics) ? raw.topics : []).slice(0, 20).map(function (t) {
                        return { name: clean(t && t.name, 60), count: parseInt(t && t.count, 10) || 0 };
                      }).filter(function (t) { return t.name && t.count > 0; }),
    sponsors:         names(raw.sponsors, 30, 60),
    host_company:     clean(raw.host_company, 60),
    previous:         prev ? {
                        event_name: clean(prev.event_name, 80),
                        url:        okUrl(prev.url, ''),
                        date:       clean(prev.date, 60),
                        talks:      parseInt(prev.talks, 10) > 0 ? parseInt(prev.talks, 10) : 0,
                        same_city:  prev.same_city === true,
                        companies:  names(prev.companies, 80, 60)
                      } : null
  };
  if (!ev.event_name) ev.event_name = ev.brand_name + ' ' + ev.city;
  if (ev.previous && !ev.previous.event_name) ev.previous = null;
  // a tier the data cannot back up falls back one step (e.g. "strong" with no companies at all)
  if (ev.tier === 'strong' && !ev.companies.length) ev.tier = 'building';
  return ev;
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
  var lock = readJson('INVITATION_LOCK');
  var left = Math.ceil(((lock.locked_until || 0) - Date.now()) / 1000);
  return left > 0 ? left : 0;
}

// Count a wrong passphrase; returns lock seconds when this attempt trips a threshold, else 0.
function registerFailure() {
  var lock = readJson('INVITATION_LOCK');
  lock.count = (lock.count || 0) + 1;
  var secs = LOCK_STEPS[lock.count] || 0;
  if (secs) lock.locked_until = Date.now() + secs * 1000;
  if (lock.count >= 10) lock.count = 0;          // after the 24 h lock the ladder starts again
  props().setProperty('INVITATION_LOCK', JSON.stringify(lock));
  return secs;
}

function resetFailures() {
  props().deleteProperty('INVITATION_LOCK');
}

// true when today's budget allows another letter (and records it)
function dailyBudget() {
  var today = Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd');
  var d = readJson('INVITATION_DAILY');
  if (d.day !== today) d = { day: today, sends: 0 };
  if (d.sends + 1 > DAILY_MAX_SENDS) return false;
  d.sends += 1;
  props().setProperty('INVITATION_DAILY', JSON.stringify(d));
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
// Run from the editor: previews (does not send). Flip action to 'send' and the email to your own to send a real one.
function testInvitation() {
  var e = { postData: { contents: JSON.stringify({
    action: 'preview',
    pass: props().getProperty('ONBOARDING_PASSPHRASE'),
    brand: 'sreday',
    speaker_name: 'Jane',
    speaker_email: 'hello@sreday.com',
    cc_emails: ['marketing@example.com'],
    event: {
      brand: 'sreday', brand_name: 'SREday', slug: '2026-london-q3',
      event_name: 'SREday London 2026 Q3', city: 'London', date: 'September 24, 2026',
      event_url: 'https://www.sreday.com/2026-london-q3/', fasttrack_url: 'https://www.sreday.com/2026-london-q3/fasttrack/',
      sponsor_page_url: 'https://www.sreday.com/2026-london-q3/#sponsors',
      venue_name: 'Everyman Canary Wharf', attendees: 150, youtube_url: 'https://www.youtube.com/@sreday',
      calendly_url: 'https://calendly.com/sreday/30min', slot_minutes: 30,
      tracks: 3, confirmed: 29, talks_target: 36, fill_pct: 81, tier: 'strong',
      companies: ['Admiral Group Plc', 'AIS', 'Alibaba Cloud', 'Apexon', 'AWS', 'Cisco', 'Cockroach Labs', 'Cognizant', 'Dash0', 'DataArt', 'Dynatrace'],
      topics: [{ name: 'Site Reliability Engineering', count: 7 }, { name: 'Observability & Monitoring', count: 5 }, { name: 'DevOps & Automation', count: 4 },
               { name: 'Production Engineering', count: 4 }, { name: 'AI for Operations', count: 3 }],
      sponsors: ['Harness', 'Cockroach Labs', 'Imply'],
      previous: { event_name: 'SREday London 2026 Q1', date: 'March 12, 2026', talks: 33, companies: ['AWS', 'AuthZed', 'ClickHouse'] }
    }
  }) } };
  Logger.log(doPost(e).getContent());
}
