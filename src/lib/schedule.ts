// src/lib/schedule.ts
import type { CollectionEntry } from 'astro:content';
import type { Event, Break } from '../content.config';
import { addMinutes, parseISO, format } from 'date-fns';

// Talk with computed fields
export interface ScheduleTalk {
  id: string; // Collection entry ID (path-based slug)
  slug: string; // URL slug derived from id
  data: CollectionEntry<'talks'>['data'];
  body: string; // Markdown body content
}

export interface ScheduleItem {
  type: 'talk' | 'break' | 'wrapup';
  timeStr: string;
  startTime: Date;
  duration: number;
  talk?: ScheduleTalk;
  breakInfo?: { title: string; comment: string };
}

export interface TrackSchedule {
  trackId: string;
  roomName: string;
  items: ScheduleItem[];
}

export interface ScheduleData {
  tracks: TrackSchedule[];
  allTimes: string[]; // Sorted unique times for table view
}

export function buildSchedule(
  event: Event & { id: string },
  talkEntries: CollectionEntry<'talks'>[]
): ScheduleData {
  // Extract event slug from id
  const eventSlug = event.id;

  // Filter talks for this event (by folder path)
  const eventTalks = talkEntries
    .filter((t) => t.id.startsWith(eventSlug + '/'))
    .map((t) => ({
      id: t.id,
      slug: t.id.replace(eventSlug + '/', '').replace(/\.md$/, ''),
      data: t.data,
      body: t.body || '',
    }));

  const confirmed = eventTalks.filter((t) => t.data.status === 'confirmed');
  const keynotes = eventTalks.filter((t) => t.data.status === 'keynote');

  // Group by track (preserving order from CSV)
  const trackIds: string[] = [];
  const byTrack = new Map<string, ScheduleTalk[]>();

  for (const talk of confirmed) {
    const track = talk.data.track;
    if (!byTrack.has(track)) {
      trackIds.push(track);
      byTrack.set(track, []);
    }
    byTrack.get(track)!.push(talk);
  }

  const allTimes = new Set<string>();
  const tracks: TrackSchedule[] = [];

  for (let i = 0; i < trackIds.length; i++) {
    const trackId = trackIds[i];
    const roomName = event.rooms[i] || `Track ${trackId}`;
    const trackTalks = byTrack.get(trackId) || [];
    const items: ScheduleItem[] = [];

    let currentTime = parseISO(event.startTime);
    let talkIndex = 0;

    // Insert keynotes at start (only for first track of each day)
    const currentDay = Math.floor(i / event.rooms.length) + 1;
    if (i % event.rooms.length === 0) {
      for (const keynote of keynotes.filter((k) => k.data.day === currentDay)) {
        const timeStr = format(currentTime, 'HH:mm');
        allTimes.add(timeStr);
        items.push({
          type: 'talk',
          timeStr,
          startTime: currentTime,
          duration: keynote.data.duration,
          talk: keynote,
        });
        currentTime = addMinutes(currentTime, keynote.data.duration);
      }
    } else {
      // Sync time with keynote duration for parallel tracks
      for (const keynote of keynotes.filter((k) => k.data.day === currentDay)) {
        currentTime = addMinutes(currentTime, keynote.data.duration);
      }
    }

    // Insert breaks and talks
    for (const brk of event.breaks) {
      // Add talks before this break
      for (let j = 0; j < brk.talks_before && talkIndex < trackTalks.length; j++) {
        const talk = trackTalks[talkIndex++];
        const timeStr = format(currentTime, 'HH:mm');
        allTimes.add(timeStr);
        items.push({
          type: 'talk',
          timeStr,
          startTime: currentTime,
          duration: talk.data.duration,
          talk,
        });
        currentTime = addMinutes(currentTime, talk.data.duration);
      }

      // Add the break
      const timeStr = format(currentTime, 'HH:mm');
      allTimes.add(timeStr);
      items.push({
        type: 'break',
        timeStr,
        startTime: currentTime,
        duration: brk.duration,
        breakInfo: { title: brk.title, comment: brk.comment },
      });
      currentTime = addMinutes(currentTime, brk.duration);
    }

    // Add remaining talks after all breaks
    while (talkIndex < trackTalks.length) {
      const talk = trackTalks[talkIndex++];
      const timeStr = format(currentTime, 'HH:mm');
      allTimes.add(timeStr);
      items.push({
        type: 'talk',
        timeStr,
        startTime: currentTime,
        duration: talk.data.duration,
        talk,
      });
      currentTime = addMinutes(currentTime, talk.data.duration);
    }

    // Add wrap-up
    const wrapTime = format(currentTime, 'HH:mm');
    allTimes.add(wrapTime);
    items.push({
      type: 'wrapup',
      timeStr: wrapTime,
      startTime: currentTime,
      duration: 15,
      breakInfo: { title: 'Wrap up', comment: 'Networking time!' },
    });

    tracks.push({ trackId, roomName, items });
  }

  return {
    tracks,
    allTimes: Array.from(allTimes).sort(),
  };
}

// Helper to get all unique speakers from schedule
export function getSpeakers(schedule: ScheduleData): ScheduleTalk[] {
  const seen = new Set<string>();
  const speakers: ScheduleTalk[] = [];

  for (const track of schedule.tracks) {
    for (const item of track.items) {
      if (item.talk && !seen.has(item.talk.data.speaker.name)) {
        seen.add(item.talk.data.speaker.name);
        speakers.push(item.talk);
      }
    }
  }

  return speakers.sort((a, b) =>
    a.data.speaker.name.localeCompare(b.data.speaker.name)
  );
}
