/**
 * Sponsor lead form backend - Google Apps Script web app.
 *
 * Receives the JSON posted by the "Email us" form on the home page (home/_templates/index.html)
 * and sends ONE email to the brand inbox AND the sponsor, so the thread is open for both sides
 * straight away. Reply-all keeps everyone on it.
 *
 * Deploy (one-time, from the Google account that sends as mark@llmday.com / mark@sreday.com):
 * 1. https://script.google.com -> New project -> paste this file -> save as "Sponsor lead form".
 * 2. Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone.
 * 3. Authorise the Gmail scope when prompted, copy the .../exec URL.
 * 4. Put that URL into home/metadata.yml -> lead_form_url (and the sisters' metadata when propagating).
 * Re-deploy after editing: Deploy -> Manage deployments -> edit -> new version (the URL stays the same).
 *
 * The same deployment serves all three brands: the form sends `brand` and BRANDS picks inbox + alias.
 */

var BRANDS = {
  llmday: { inbox: 'hello@llmday.com', from: 'mark@llmday.com', site: 'llmday.com' },
  sreday: { inbox: 'hello@sreday.com', from: 'mark@sreday.com', site: 'sreday.com' },
  platformday: { inbox: 'hello@platformday.com', from: 'mark@platformday.com', site: 'platformday.com' }
};
var ALLOWED_BRANDS = ['LLMday', 'SREday', 'PLATFORMday'];
var ALLOWED_REGIONS = ['EU', 'US', 'LATAM', 'ASIA'];
var CALENDLY_URL = 'https://calendly.com/sreday/30min';
var SENDER_NAME = 'Mark Pawlikowski';

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
  if (!name || !company || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return respond({ ok: false, error: 'invalid fields' });
  }
  var brands = pick(data.brands, ALLOWED_BRANDS);
  var regions = pick(data.regions, ALLOWED_REGIONS);

  var firstName = name.split(/\s+/)[0];
  var brandsProse = brands.length ? joinProse(brands) : 'our';
  var regionsTail = regions.length ? ' in ' + joinProse(regions) : '';

  var subject = name + ' (' + company + ') - ' +
                (brands.length ? brands.join(', ') : 'conference') + ' sponsorship' +
                (regions.length ? ' in ' + regions.join(', ') : '');

  var body =
    'Hey ' + firstName + ',\n\n' +
    'Thanks for reaching out! Quick summary so everyone on this thread has the same picture:\n\n' +
    name + ' from ' + company + ' would like to learn more about ' + brandsProse + ' conferences' + regionsTail + '.\n\n' +
    '- Name: ' + name + '\n' +
    '- Company: ' + company + '\n' +
    '- Email: ' + email + '\n' +
    '- Conferences: ' + (brands.length ? brands.join(', ') : 'not specified') + '\n' +
    '- Regions: ' + (regions.length ? regions.join(', ') : 'not specified') + '\n' +
    '- Sent from: ' + brand.site + '\n\n' +
    'Take it from here folks! Mark will follow up shortly with dates and options. ' +
    'If you would rather talk right away: ' + CALENDLY_URL + '\n\n' +
    'Best,\n' +
    'Mark';

  var options = { name: SENDER_NAME };
  // Send from the brand alias when this account has it configured ("Send mail as"); otherwise the
  // primary address is used. getAliases() never lists the primary address, so that case falls through.
  if (GmailApp.getAliases().indexOf(brand.from) !== -1) options.from = brand.from;

  GmailApp.sendEmail(brand.inbox + ',' + email, subject, body, options);
  Logger.log('Lead sent: %s <%s> (%s) -> %s [%s / %s]', name, email, company, brand.inbox, brands.join('+'), regions.join('+'));
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

// Keep only allowed values, in the allowed list's order, without duplicates.
function pick(values, allowed) {
  if (!Array.isArray(values)) return [];
  return allowed.filter(function (a) { return values.indexOf(a) !== -1; });
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
    brands: ['LLMday', 'SREday'], regions: ['EU'], brand: 'llmday', page: 'https://www.llmday.com/#sponsor'
  }) } };
  Logger.log(doPost(e).getContent());
}
