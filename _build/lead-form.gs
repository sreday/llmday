/**
 * Sponsor lead form backend - Google Apps Script web app.
 *
 * Receives the JSON posted by the "Email us" form on the home page (home/_templates/index.html)
 * and sends ONE email To the sponsor with the brand inbox in Cc, so the thread is open for both
 * sides straight away. Reply-all keeps everyone on it.
 * The thread is moved to the Inbox as unread + important under the "Sponsor leads" label, because a
 * message sent from this account would otherwise only show up (read) in "Sent".
 *
 * Deploy (one-time, from the Google account that sends as mark@llmday.com / mark@sreday.com):
 *   1. https://script.google.com -> New project -> paste this file -> save as "Sponsor lead form".
 *   2. Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone.
 *   3. Authorise the Gmail scope when prompted, copy the .../exec URL.
 *   4. Put that URL into home/metadata.yml -> lead_form_url (and the sisters' metadata when propagating).
 *   Re-deploy after editing: Deploy -> Manage deployments -> edit -> new version (the URL stays the same).
 *
 * The same deployment serves all three brands: the form sends `brand` and BRANDS picks inbox + alias.
 */

var BRANDS = {
  llmday: { inbox: 'hello@llmday.com', from: 'mark@llmday.com', site: 'llmday.com' },
  sreday: { inbox: 'hello@sreday.com', from: 'mark@sreday.com', site: 'sreday.com' },
  platformday: { inbox: 'hello@platformday.com', from: 'mark@platformday.com', site: 'platformday.com' }
};
var ALLOWED_INTERESTS = ['Sponsor', 'Host'];
var ALLOWED_BRANDS = ['LLMday', 'SREday', 'PLATFORMday'];
var ALLOWED_REGIONS = ['US', 'EU', 'ASIA', 'LATAM'];
var ALLOWED_BUDGETS = ['No budget', '$1K-5K', '$5K-10K', '$10K+'];
var INTEREST_WORDS = { Sponsor: 'Sponsoring', Host: 'Hosting' };
var CALENDLY_URL = 'https://calendly.com/sreday/30min';
var SENDER_NAME = 'Mark Pawlikowski';
var LEAD_LABEL = 'Sponsor leads'; // Gmail label the lead threads are filed under (created on first use)

function doGet() {
  return respond({ ok: true, service: 'sponsor-lead-form' });
}

function doPost(e) {
  var data;
  try {
    data = JSON.parse(e.postData.contents);
  } catch (err) {
    return respond({ ok: false, error: 'bad json' });
  }

  // Honeypot filled in -> a bot. Pretend success, send nothing.
  if (data.website) return respond({ ok: true });

  var brandKey = String(data.brand || '').toLowerCase();
  var brand = BRANDS[brandKey] || BRANDS.llmday;
  var name = clean(data.name, 80);
  var company = clean(data.company, 120);
  var email = clean(data.email, 254).toLowerCase();
  var interests = pick(data.interests, ALLOWED_INTERESTS);
  var brands = pick(data.brands, ALLOWED_BRANDS);
  var regions = pick(data.regions, ALLOWED_REGIONS);
  var budget = ALLOWED_BUDGETS.indexOf(data.budget) !== -1 ? data.budget : '';
  var source = sourceFromPage(clean(data.page, 300));
  var consent = data.consent === true;

  // Everything is mandatory: the form enforces it, this is the backstop.
  if (!name || !company || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ||
      !interests.length || !brands.length || !regions.length || !budget || !consent) {
    return respond({ ok: false, error: 'invalid fields' });
  }

  var firstName = name.split(/\s+/)[0];
  var interestPhrase = joinProse(interests.map(function (i) { return INTEREST_WORDS[i]; }));

  var subject = company + ' <> ' + brands.join(', ');

  var confLabel = brands.length > 1 ? 'Conferences' : 'Conference';
  var intro = firstName + ',' + '\n\n' +
    'Thank you for submitting the form, here' + '\'' + 's what we' + '\'' + 're working with:' + '\n\n';
  var bullets = [
    'Email: ' + email,
    confLabel + ': ' + brands.join(', '),
    'Regions: ' + regions.join(', '),
    'Budget: ' + budget,
    'Form sent from: ' + source
  ];
  var outro = '\nMark will reply soon. In the meantime, you can schedule a quick call here: ' + CALENDLY_URL + '\n\n' +
    'Best,' + '\n' + 'Mark';

  // plain-text version (fallback for clients that do not render HTML)
  var body =
    'Hey ' + intro +
    name + ' from ' + company + ' would like to learn more about ' + interestPhrase + ':' + '\n\n' +
    bullets.map(function (b) { return '- ' + b; }).join('\n') + '\n' +
    outro;

  // HTML version: same text, with the name and company in bold
  var htmlBody =
    '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#111">' +
    nl2br(esc('Hey ' + intro)) +
    '<b>' + esc(name) + '</b> from <b>' + esc(company) + '</b> would like to learn more about ' + esc(interestPhrase) + ':<br><br>' +
    '<ul style="margin:0 0 0 18px;padding:0">' + bullets.map(function (b) { return '<li>' + esc(b) + '</li>'; }).join('') + '</ul>' +
    nl2br(esc(outro)).replace(esc(CALENDLY_URL), '<a href="' + CALENDLY_URL + '">' + CALENDLY_URL + '</a>') +
    '</div>';

  // Sponsor in To, brand inbox in Cc: a plain Reply from Mark then goes to the sponsor and
  // Reply-all keeps hello@ on the thread (set Gmail's default reply behaviour to Reply all).
  var options = { name: SENDER_NAME, cc: brand.inbox, htmlBody: htmlBody };
  // Send from the brand alias when this account has it configured ("Send mail as"); otherwise the
  // primary address is used. getAliases() never lists the primary address, so that case falls through.
  if (GmailApp.getAliases().indexOf(brand.from) !== -1) options.from = brand.from;

  // Send via a draft so we get the message back: a mail sent from this very account would otherwise
  // sit read-only in "Sent". Pull its thread into the Inbox, unread + important, under a label.
  var message = GmailApp.createDraft(email, subject, body, options).send();
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
  Logger.log('Lead sent: %s <%s> (%s) -> %s [%s / %s / %s / %s]', name, email, company, brand.inbox,
             interests.join('+'), brands.join('+'), regions.join('+'), budget + ' from ' + source);
  return respond({ ok: true });
}

// ---- helpers -------------------------------------------------------------

function respond(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// Trim, collapse whitespace, strip control characters, cap the length.
function clean(v, max) {
  return String(v == null ? '' : v).replace(/[\x00-\x1f\x7f]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, max);
}

// Minimal HTML escaping for user-supplied strings in the HTML body
function esc(v) {
  return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function nl2br(v) {
  return String(v).replace(/\n/g, '<br>');
}

// Keep only allowed values, in the allowed list's order, without duplicates.
function pick(values, allowed) {
  if (!Array.isArray(values)) return [];
  return allowed.filter(function (a) { return values.indexOf(a) !== -1; });
}

// Page URL the form was submitted from -> clean origin for the email:
// 'https://www.llmday.com/#sponsor' -> 'https://llmday.com/', '.../2026-nyc-q4/index.html?x' -> 'https://llmday.com/2026-nyc-q4/'
function sourceFromPage(url) {
  var m = /^https?:\/\/([^\/?#]+)([^?#]*)/.exec(url || '');
  if (!m) return 'unknown';
  var host = m[1].toLowerCase().replace(/^www\./, '');
  var path = (m[2] || '/').replace(/index\.html$/, '');
  if (!path) path = '/';
  return 'https://' + host + path;
}

// ['A'] -> 'A'; ['A','B'] -> 'A and B'; ['A','B','C'] -> 'A, B and C'
function joinProse(arr) {
  if (arr.length <= 1) return arr.join('');
  return arr.slice(0, -1).join(', ') + ' and ' + arr[arr.length - 1];
}

// Run from the editor to test without the website: sends a real email to the inbox only (sponsor = inbox).
function testLead() {
  var e = { postData: { contents: JSON.stringify({
    name: 'Anna Kowalska', email: 'hello@llmday.com', company: 'Chainguard',
    interests: ['Sponsor', 'Host'], brands: ['LLMday', 'SREday'], regions: ['EU'], budget: '$5K-10K', consent: true,
    brand: 'llmday', page: 'https://www.llmday.com/2026-redwood-city-q4/?v=2#sponsors'
  }) } };
  Logger.log(doPost(e).getContent());
}
