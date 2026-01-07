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
