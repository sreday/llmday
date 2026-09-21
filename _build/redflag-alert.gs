/**
 * Red flag alert - Google Apps Script (Gmail), timer based.
 *
 * "Did you just nuke SREday London 2026 Q3?" (Marek 2026-09-21). Every talks.csv change is a GitHub web upload;
 * when the wrong file lands in the wrong event folder, one push swaps a whole lineup. Every site build works out
 * which events look wrong right now (_build/redflag.py, the red bar on /status/) and publishes the list as
 * /status/redflags.json. This script reads the four files every 10 minutes and emails each new flag once.
 * Nothing is deployed as a web app: no URL, no token, no GitHub secrets.
 *
 * Install (one-time, from the Google account that reads mark@sreday.com):
 *   1. https://script.google.com -> New project -> paste this file -> save as "Red flag alert".
 *   2. Pick the function "setup" in the toolbar -> Run -> authorise (Gmail, external requests, triggers).
 *      setup() creates the 10-minute timer and runs the first check, so flags that are true today are emailed
 *      straight away.
 *   To stop: Triggers (clock icon) -> delete the "check" trigger. To re-send everything: run "forget", then "check".
 */

var SITES = ['https://sreday.com/', 'https://llmday.com/', 'https://platformday.com/', 'https://promptengineering.rocks/'];
var LABEL = 'Red flags';
var MAX_PER_RUN = 10;      // a broken json can never flood the inbox

function setup() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'check') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('check').timeBased().everyMinutes(10).create();
  check();
}

function check() {
  var props = PropertiesService.getScriptProperties();
  var seen = JSON.parse(props.getProperty('SEEN') || '{}');
  var me = Session.getEffectiveUser().getEmail();
  var sent = 0;
  SITES.forEach(function (site) {
    var flags = [];
    try {
      var res = UrlFetchApp.fetch(site + 'status/redflags.json?t=' + Date.now(), { muteHttpExceptions: true });
      if (res.getResponseCode() !== 200) return;               // site not rebuilt yet, or down: try again in 10 minutes
      flags = JSON.parse(res.getContentText()).flags || [];
    } catch (err) {
      Logger.log(site + ': ' + err);
      return;
    }
    flags.forEach(function (f) {
      var key = [f.brand, f.kind, f.folder, f.sha].join(':');
      if (seen[key] || sent >= MAX_PER_RUN) return;
      var mail = composeAlert(f);
      var thread = GmailApp.createDraft(me, mail.subject, mail.text, { name: 'Red flag' }).send().getThread();
      try {                                                    // a mail to yourself sits read in "Sent" otherwise
        thread.moveToInbox(); thread.markUnread(); thread.markImportant();
        thread.addLabel(GmailApp.getUserLabelByName(LABEL) || GmailApp.createLabel(LABEL));
      } catch (err) {
        Logger.log('Sent, but could not file the thread: ' + err);
      }
      seen[key] = Date.now();
      sent += 1;
      Logger.log('Red flag sent: ' + mail.subject);
    });
  });
  var keys = Object.keys(seen).sort(function (a, b) { return seen[b] - seen[a]; }).slice(0, 200);   // newest 200
  var kept = {};
  keys.forEach(function (k) { kept[k] = seen[k]; });
  props.setProperty('SEEN', JSON.stringify(kept));
}

function forget() {
  PropertiesService.getScriptProperties().deleteProperty('SEEN');
}

// The one email. Plain text, Mark's voice; the subject is Marek's wording for every kind of flag.
function composeAlert(f) {
  var gone = f.gone_names || [], added = f.added_names || [];
  var lines = ['Hey Mark,', ''];
  if (f.kind === 'path') {
    lines.push('A talks.csv for ' + f.event_name + ' was uploaded outside the _db folder, so the website ignores it:', f.path, '',
               'The lineup on the website did not change. Please upload the file again into ' + f.folder + '/_db/ and delete the stray copy.');
  } else if (f.kind === 'twin') {
    lines.push('It looks like ' + f.event_name + ' has the lineup of ' + f.suspect_name + ', please check.', '',
               f.identical ? 'The two talks.csv files are identical.' : f.overlap_pct + '% of the ' + f.after_n + ' speakers are the same in both events.');
  } else {
    if (f.suspect_name) lines.push('It looks like ' + f.event_name + ' has the lineup of ' + f.suspect_name + ', please check.');
    else if (f.kind === 'removal') lines.push('It looks like ' + f.event_name + ' just lost most of its lineup, please check.');
    else lines.push('It looks like the whole lineup of ' + f.event_name + ' was replaced in one upload, please check.');
    lines.push('', f.gone + ' of ' + f.before_n + ' speakers disappeared' + (f.added ? ' and ' + f.added + ' new ones came in' : '') +
               ' in a single upload' + (f.when ? ' on ' + f.when : '') + (f.author ? ' by ' + f.author : '') + '.');
    if (f.suspect_name) lines.push(f.identical ? 'The file is an exact copy of the ' + f.suspect_name + ' talks.csv.'
                                               : f.overlap_pct + '% of the new lineup matches ' + f.suspect_name + '.');
    if (gone.length) lines.push('', 'Gone: ' + gone.join(', ') + (f.gone > gone.length ? ', ...' : ''));
    if (added.length) lines.push('New: ' + added.join(', ') + (f.added > added.length ? ', ...' : ''));
  }
  lines.push('');
  if (f.commit_url) lines.push('The upload: ' + f.commit_url);
  if (f.file_url) lines.push('File history: ' + f.file_url);
  if (f.event_url) lines.push('Live page: ' + f.event_url);
  if (f.status_url) lines.push('Status page: ' + f.status_url);
  if (f.kind !== 'path') {
    lines.push('', 'To fix it, upload the right talks.csv into ' + f.folder + '/_db/ again, or revert the upload on GitHub.',
               'If this was intentional, ignore this email. The red bar on the status page goes away when you add',
               String(f.sha || '').slice(0, 7) + ' to redflag_ack in home/metadata.yml.');
  }
  return { subject: 'Did you just nuke ' + f.event_name + '?', text: lines.join('\n') };
}
