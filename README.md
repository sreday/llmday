# LLMday

In-person conferences for Large Language Models, Agents & AI Systems.


## Running locally

```sh
# if needed
make env
source env/bin/activate

make deps

# builds all years
make

# runs a small script to serve the pages with python
make serve
```

## Adding a new conference

1. Copy over the template (`_event_template`) to a new folder
    1. The name needs to follow the pattern `YYYY-location-qX`
    1. Let's say we add `2026-tokyo-q1`
1. Modify the `2026-tokyo-q1/metadata.yaml` file:
    1. Update the location, time, date
    1. Update the `2026-tokyo-q1/_db/talks.csv` file
1. Update the venue info
    1. Modify the address in `2026-tokyo-q1/_templates/venue.html`
    1. Upload/copy the 3 venue photos to `2026-tokyo-q1/assets/images/venue`
1. Update the luma event
    1. Don't change the embeds in `2026-tokyo-q1/_templates/tickets.html`
    1. Change the `luma_evt` field in `home/metadata.yaml`
1. Update the hero pictures
    1. Add the pictures to `photos`
    1. List the relevant ones in `2026-tokyo-q1/metadata.yaml`
1. Add the conference to the home page
    1. Upload the splash screen
        1. Put it in `assets/images/events/llmday-2026-tokyo-q1.jpeg`
    1. Modify the `home/metadata.yaml` file:
        1. Add a new item to the events list
        1. Make sure the url matches the format, e.g `2026-tokyo-q1`
        1. The `photo_url` card image doubles as the social link preview (og:image) for all of that event's pages; until it is uploaded, the build warns and falls back to the default hero photo

```yaml
events:

  - name: LLMday Tokyo 2026 Q1
    location: Tokyo, Japan
    photo_url: ./assets/images/events/llmday-2026-tokyo-q1.jpeg
    url: ./2026-tokyo-q1/
...
```

## Home-page sponsor vs partner carousels

The main website's two logo carousels are categorized via `partners.yaml` at the
repo root (home page only - conference pages are unaffected):

- **Paying sponsor**: drop the logo file into `sponsors/` - it shows up in the
  home Sponsor carousel automatically.
- **Partner** (community meetup, media/non-sponsor org, sister conference, job
  board): drop the logo into `sponsors/` AND add its filename to the right list
  in `partners.yaml` - it shows up in the Partner carousel instead.
- **Duplicates**: if a company has more than one logo file, list the extra
  variants under `hidden_duplicates` so it only appears once on the home page.

## Sponsor lead form

The home page `#sponsor` section has an "Email us" expandable form under the Calendly widget
(`home/_templates/index.html`). It posts JSON to a Google Apps Script web app whose source lives in
`_build/lead-form.gs`; the script emails `hello@llmday.com` **and** the sponsor in one message
(from `mark@llmday.com`) so the thread is open for both sides immediately.

- The deployed `/exec` URL lives in `home/metadata.yml` -> `lead_form_url`. When it is empty the form
  falls back to a prefilled `mailto:` link, so the UI can ship before the script is deployed.
- Deploy / re-deploy steps are in the header comment of `_build/lead-form.gs` (Web app, Execute as: Me,
  Who has access: Anyone). Editing the script needs a new deployment *version*; the URL stays the same.
- Same deployment can serve sreday/platformday: the form sends `brand`, the script maps it to inbox + alias.
- The email lists `Form sent from: <page URL>` (home page -> `https://llmday.com/`, event page -> its folder URL).
- Event pages (event index + talk pages) use the same form as the "Become A Sponsor" pill via
  `_event_template/_templates/_lead_form.html` (propagated into every `20*/_templates/`); the event build
  reads `lead_form_url` from `home/metadata.yml`. That partial is a deliberate COPY of the home-page block in
  `home/_templates/index.html` (label, pill colour, card border and navbar hash differ) - when changing fields
  or copy, edit both, then propagate the partial to all event folders.

## Speaker onboarding (hidden page)

Every non-frozen event gets a hidden page at `/<event>/onboarding/` (e.g. `/2026-london-q3/onboarding/`) with
a passphrase-gated form: paste one or many speaker emails, **Preview**, **Send**. A Google Apps Script then emails
the ONE universal "`<Event> - <Month Day> - Info for speakers`" message From `mark@llmday.com` To that same alias (hello@ is not copied)
with every speaker in **Bcc**, and files the thread in the Inbox unread under the "Speaker onboarding" label
(speaker "OK" replies land on it).

- Template: `_event_template/_templates/onboarding.html` (standalone, does not extend `_base.html`, `noindex`,
  never listed in the sitemap; identical in all three repos - the colour comes from `brand_color`). Propagated to
  every `20*/_templates/` like the other templates.
- Facts: `_event_template/_build/generate.py` builds `onboarding_event` from `metadata.yml` (`date_string`,
  `city_name`, `attendees`, `base_path`, `youtube_url`, `calendly_sponsor_url`) plus the venue name/address
  scraped from `_templates/venue.html` (first `<h4>` and the `<p>` after it, up to the first blank line).
  The event name is `brand + city + year + quarter` from the folder name. Optional per-event overrides in
  `metadata.yml`:
  ```yaml
  onboarding:
    event_name: "SREday London 2026 Q3"      # default: built from the folder name
    venue_name: "Everyman Canary Wharf"      # default: scraped from venue.html
    venue_address: "Crossrail Place, ..."    # default: scraped from venue.html
    slot_minutes: 25                         # default 30 (talk = slot - 5)
    dinner: "TBC"                            # "TBC" | free text | "none" (drops the dinner line)
    extra: ""                                # optional paragraph before "What happens next?"
  ```
- Email wording lives ONLY in the Apps Script `_build/onboarding-form.gs` (kept in the llmday repo; one
  deployment serves all brands). Deploy steps are in its header comment. The `/exec` URL goes into
  `home/metadata.yml` -> `onboarding_form_url` in all three repos; while it is empty the page refuses to send.
- Protection: passphrase in the Script Property `ONBOARDING_PASSPHRASE` (never in the repos); 3 wrong attempts
  lock the endpoint for 15 min, 10 for 24 h (delete the `ONBOARDING_LOCK` property to clear); daily caps of
  50 sends / 500 recipients; max 50 recipients per send. Sender, template and links are pinned server-side.
