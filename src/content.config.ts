// src/content.config.ts - Astro 5 Content Layer API
import { defineCollection, z } from 'astro:content';
import { glob, file } from 'astro/loaders';

// === SHARED SCHEMAS ===

const venueSchema = z.object({
  name: z.string(),
  address: z.string(),
  city: z.string(),
  mapEmbedUrl: z.string(),
  images: z.array(z.string()).default([]),
});

const breakSchema = z.object({
  title: z.string(),
  duration: z.number(),
  comment: z.string().default(''),
  talks_before: z.number(),
});

const sponsorSchema = z.object({
  logo: z.string(),
  url: z.string(),
});

// === EVENTS COLLECTION (YAML data) ===

const events = defineCollection({
  loader: glob({ pattern: '*.yaml', base: './src/content/events' }),
  schema: z.object({
    state: z.enum(['before', 'active', 'after']),
    startTime: z.string(), // ISO datetime string
    dateString: z.string(),
    locationString: z.string(),
    days: z.number().default(1),
    attendees: z.number().default(100),
    rooms: z.array(z.string()),
    breaks: z.array(breakSchema).default([]),
    sponsors: z.array(sponsorSchema).default([]),
    lumaEventId: z.string().default(''),
    cfpUrl: z.string().default(''),
    heroPictures: z.array(z.string()).default([]),
    venue: venueSchema,
    // Social links
    twitterUrl: z.string().default(''),
    linkedinUrl: z.string().default(''),
    youtubeUrl: z.string().default(''),
    mailto: z.string().default(''),
  }),
});

// === TALKS COLLECTION (Markdown content) ===

const talks = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/talks' }),
  schema: z.object({
    // Talk metadata
    status: z.enum(['confirmed', 'keynote']).default('confirmed'),
    track: z.coerce.string().default('1'),
    day: z.coerce.number().default(1),
    duration: z.coerce.number().default(30),
    title: z.string(),
    youtube: z.string().default(''),
    // Speaker info
    speaker: z.object({
      name: z.string(),
      organization: z.string().default(''),
      photo: z.string(), // filename, resolved to /speakers/{photo}
      linkedin: z.string().default(''),
      twitter: z.string().default(''),
    }),
    // Co-speaker (optional)
    coSpeaker: z
      .object({
        name: z.string(),
        linkedin: z.string().default(''),
        twitter: z.string().default(''),
      })
      .optional(),
  }),
});

// === TESTIMONIALS COLLECTION (YAML data) ===

const testimonialItemSchema = z.object({
  id: z.string(),
  name: z.string(),
  job: z.string().default(''),
  company: z.string().default(''),
  text: z.string(),
  event: z.string().default(''),
  isSpeaker: z.boolean().default(false),
  headshot: z.string().default(''),
  twitter: z.string().default(''),
  linkedin: z.string().default(''),
});

const testimonials = defineCollection({
  loader: file('./src/content/testimonials/all.yaml'),
  schema: testimonialItemSchema,
});

export const collections = { events, talks, testimonials };

// === EXPORT INFERRED TYPES ===
// With Content Layer API, types are inferred differently
export type Event = z.infer<typeof events.schema>;
export type Talk = z.infer<typeof talks.schema>;
export type Testimonial = z.infer<typeof testimonialItemSchema>;
export type Break = z.infer<typeof breakSchema>;
export type Venue = z.infer<typeof venueSchema>;
export type Sponsor = z.infer<typeof sponsorSchema>;
