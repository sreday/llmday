// src/lib/schedule.ts
import type { CollectionEntry } from 'astro:content';
import type { Event, ScheduleItem as SchemaScheduleItem } from '../content.config';
import { addMinutes, parseISO, format } from 'date-fns';

// Talk with computed fields
export interface ScheduleTalk {
  id: string;
  slug: string;
  data: CollectionEntry<'talks'>['data'];
  body: string;
}

export interface ScheduleItem {
  type: 'talk' | 'break';
  timeStr: string;
  startTime: Date;
  duration: number;
  talk?: ScheduleTalk;
  breakInfo?: { title: string };
}

export interface TrackSchedule {
  trackId: string;
  roomName: string;
  items: ScheduleItem[];
}

export interface ScheduleData {
  tracks: TrackSchedule[];
  allTimes: string[];
}

export function buildSchedule(
  event: Event & { id: string },
  talkEntries: CollectionEntry<'talks'>[]
): ScheduleData {
  const eventSlug = event.id;
  const defaultDuration = event.defaultDuration;

  // Build lookup map for talks by slug
  const talksBySlug = new Map<string, ScheduleTalk>();
  for (const t of talkEntries) {
    if (t.id.startsWith(eventSlug + '/')) {
      const slug = t.id.replace(eventSlug + '/', '').replace(/\.md$/, '');
      talksBySlug.set(slug, {
        id: t.id,
        slug,
        data: t.data,
        body: t.body || '',
      });
    }
  }

  const allTimes = new Set<string>();
  const tracks: TrackSchedule[] = [];
  let trackIndex = 0;

  // Process each room in the schedule
  for (const [roomName, items] of Object.entries(event.schedule)) {
    const trackId = String(trackIndex + 1);
    const scheduleItems: ScheduleItem[] = [];
    let currentTime = parseISO(event.startTime);

    for (const item of items) {
      const timeStr = format(currentTime, 'HH:mm');
      allTimes.add(timeStr);

      if ('talk' in item) {
        // It's a talk
        const talk = talksBySlug.get(item.talk);
        const duration = item.duration ?? talk?.data.duration ?? defaultDuration;

        scheduleItems.push({
          type: 'talk',
          timeStr,
          startTime: currentTime,
          duration,
          talk,
        });
        currentTime = addMinutes(currentTime, duration);
      } else if ('break' in item) {
        // It's a break
        const duration = item.duration ?? defaultDuration;

        scheduleItems.push({
          type: 'break',
          timeStr,
          startTime: currentTime,
          duration,
          breakInfo: { title: item.break },
        });
        currentTime = addMinutes(currentTime, duration);
      }
    }

    tracks.push({ trackId, roomName, items: scheduleItems });
    trackIndex++;
  }

  return {
    tracks,
    allTimes: Array.from(allTimes).sort(),
  };
}

// Helper to get all unique speakers from schedule
export function getSpeakers(schedule: ScheduleData): ScheduleTalk[] {
  const seen = new Set<string>();
  const result: ScheduleTalk[] = [];

  for (const track of schedule.tracks) {
    for (const item of track.items) {
      if (item.talk) {
        for (const speaker of item.talk.data.speakers) {
          if (!seen.has(speaker.name)) {
            seen.add(speaker.name);
            result.push(item.talk);
          }
        }
      }
    }
  }

  return result.sort((a, b) =>
    a.data.speakers[0].name.localeCompare(b.data.speakers[0].name)
  );
}
