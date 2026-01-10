# LLMday Conference Website

Conference website for LLMday - Large Language Models, AI and ML in-person conferences.

Built with [Astro 5](https://astro.build/) using the Content Layer API.

## Project Structure

```
/
├── public/             # Static assets (favicon, venue photos)
├── src/
│   ├── assets/         # Images (speakers, sponsors, photos)
│   ├── components/     # Astro components
│   │   ├── cards/      # Speaker cards, etc.
│   │   ├── layout/     # Header, Footer, BaseLayout
│   │   ├── schedule/   # Schedule views (timeline, table)
│   │   └── sections/   # Page sections (Hero, Venue, etc.)
│   ├── content/        # Content collections
│   │   ├── events/     # Event YAML files
│   │   ├── talks/      # Talk markdown files
│   │   └── testimonials/
│   ├── lib/            # Utilities and helpers
│   ├── pages/          # Routes
│   │   ├── [event]/    # Dynamic event pages
│   │   └── index.astro # Home page
│   └── styles/         # Global CSS
└── package.json
```

## Development

```bash
# Install dependencies
pnpm install

# Start dev server (localhost:4324)
pnpm dev

# Build for production
pnpm build

# Preview production build
pnpm preview
```

## Content Schema

### Event (`src/content/events/{event-slug}.yaml`)

```yaml
state: active | upcoming | past
startTime: "2026-02-12T09:00:00+01:00"
defaultDuration: 30                    # default talk/break duration in minutes
locationString: "CIC Warsaw, Poland"
days: 1
attendees: 100

schedule:
  "Main Room":
    - break: Registration & Coffee
      duration: 30                     # optional, uses defaultDuration if omitted
    - talk: speaker-name-talk-title    # references talk file slug
    - talk: another-talk
      duration: 60                     # override duration for this talk
    - break: Lunch
  "Second Room":
    - break: Registration & Coffee
      duration: 30
    - talk: workshop-talk
      duration: 120

sponsors:
  - logo: sponsor.png                  # in src/assets/sponsors/
    url: https://sponsor.com/

venue:
  name: CIC Warsaw
  address: Chmielna 73
  city: 00-801 Warsaw, Poland
  mapEmbedUrl: "https://..."
  images: [venue-1.jpg, venue-2.jpg]   # in src/assets/venue/

twitterUrl: https://twitter.com/llmday_com
```

### Talk (`src/content/talks/{event-slug}/{talk-slug}.md`)

```yaml
---
title: "Talk Title Here"
youtube: "dQw4w9WgXcQ"                 # optional, video ID
speakers:                              # at least one required
  - name: "Speaker Name"
    organization: "Company"
    photo: "speaker-name.png"          # in src/assets/speakers/
    linkedin: "https://linkedin.com/in/..."
    twitter: "handle"                  # optional
  - name: "Co-Speaker Name"            # additional speakers optional
    organization: "Other Company"
    photo: "co-speaker.png"
    linkedin: "https://..."
---

## Abstract

Talk description here...

## Bio

Speaker bio here...
```

## Adding Events

1. Create a YAML file in `src/content/events/` (e.g., `2026-warsaw-q1.yaml`)
2. Add speaker photos to `src/assets/speakers/`
3. Add talk markdown files to `src/content/talks/{event-slug}/`
4. Add venue images to `src/assets/venue/`
5. Add sponsor logos to `src/assets/sponsors/`

## Tech Stack

- **Astro 5** - Static site generator with Content Layer API
- **TypeScript** - Type safety
- **CSS** - Scoped component styles + global CSS variables
