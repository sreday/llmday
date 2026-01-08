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

const sponsorSchema = z.object({
  logo: z.string(),
  url: z.string(),
});

const speakerSchema = z.object({
  name: z.string(),
  organization: z.string().default(''),
  photo: z.string(),
  linkedin: z.string().default(''),
  twitter: z.string().default(''),
});

// Schedule item: either a talk or a break
// duration is optional for both - falls back to event's defaultDuration
const scheduleItemSchema = z.union([
  z.object({
    talk: z.string(),
    duration: z.number().optional(),
  }),
  z.object({
    break: z.string(),
    duration: z.number().optional(),
  }),
]);

// === EVENTS COLLECTION (YAML data) ===

const events = defineCollection({
  loader: glob({ pattern: '*.yaml', base: './src/content/events' }),
  schema: z.object({
    state: z.enum(['before', 'active', 'after']),
    startTime: z.string(), // ISO datetime string
    locationString: z.string(),
    days: z.number().default(1),
    attendees: z.number().default(100),
    defaultDuration: z.number().default(30),
    schedule: z.record(z.string(), z.array(scheduleItemSchema)).default({}),
    sponsors: z.array(sponsorSchema).default([]),
    lumaEventId: z.string().default(''),
    cfpUrl: z.string().default(''),
    heroPictures: z.array(z.string()).default([]),
    venue: venueSchema,
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
    title: z.string(),
    duration: z.coerce.number().optional(),
    youtube: z.string().default(''),
    speakers: z.array(speakerSchema).min(1),
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
export type Event = z.infer<typeof events.schema>;
export type Talk = z.infer<typeof talks.schema>;
export type Testimonial = z.infer<typeof testimonialItemSchema>;
export type Speaker = z.infer<typeof speakerSchema>;
export type ScheduleItem = z.infer<typeof scheduleItemSchema>;
export type Venue = z.infer<typeof venueSchema>;
export type Sponsor = z.infer<typeof sponsorSchema>;
