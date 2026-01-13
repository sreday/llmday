#!/usr/bin/env node

/**
 * Generate social media shareable images for conference speakers
 * Usage: node scripts/generate-speaker-images.js [event-id]
 * Example: node scripts/generate-speaker-images.js 2026-warsaw-q1
 */

import fs from "fs";
import path from "path";
import sharp from "sharp";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const ROOT_DIR = path.join(__dirname, "..");
const TALKS_DIR = path.join(ROOT_DIR, "src/content/talks");
const SPEAKERS_DIR = path.join(ROOT_DIR, "src/assets/speakers");
const EVENTS_DIR = path.join(ROOT_DIR, "src/assets/events");
const OUTPUT_DIR = path.join(ROOT_DIR, "dist/speaker-cards");

// Image dimensions (optimal for LinkedIn/Twitter)
const WIDTH = 1200;
const HEIGHT = 630;

// Brand colors
const BRAND_GREEN = "#059669"; // Matches the event banner
const BRAND_WHITE = "#FFFFFF";

/**
 * Parse frontmatter from markdown file
 */
function parseFrontmatter(content) {
  const match = content.match(/^---\n([\s\S]*?)\n---/);
  if (!match) return null;

  const frontmatter = {};
  const lines = match[1].split("\n");
  let currentKey = null;
  let inSpeakers = false;
  let currentSpeaker = null;

  for (const line of lines) {
    if (line.startsWith("title:")) {
      frontmatter.title = line.replace("title:", "").trim().replace(/^"|"$/g, "");
    } else if (line === "speakers:") {
      inSpeakers = true;
      frontmatter.speakers = [];
    } else if (inSpeakers && line.trim().startsWith("- name:")) {
      if (currentSpeaker) frontmatter.speakers.push(currentSpeaker);
      currentSpeaker = { name: line.replace("- name:", "").trim().replace(/^"|"$/g, "") };
    } else if (inSpeakers && currentSpeaker && line.trim().startsWith("organization:")) {
      currentSpeaker.organization = line.replace(/.*organization:/, "").trim().replace(/^"|"$/g, "");
    } else if (inSpeakers && currentSpeaker && line.trim().startsWith("photo:")) {
      currentSpeaker.photo = line.replace(/.*photo:/, "").trim().replace(/^"|"$/g, "");
    } else if (inSpeakers && currentSpeaker && line.trim().startsWith("linkedin:")) {
      currentSpeaker.linkedin = line.replace(/.*linkedin:/, "").trim().replace(/^"|"$/g, "");
    }
  }
  if (currentSpeaker) frontmatter.speakers.push(currentSpeaker);

  return frontmatter;
}

/**
 * Escape XML special characters
 */
function escapeXml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/**
 * Wrap text to fit within a certain width (approximate)
 */
function wrapText(text, maxCharsPerLine) {
  const words = text.split(" ");
  const lines = [];
  let currentLine = "";

  for (const word of words) {
    if ((currentLine + " " + word).trim().length <= maxCharsPerLine) {
      currentLine = (currentLine + " " + word).trim();
    } else {
      if (currentLine) lines.push(currentLine);
      currentLine = word;
    }
  }
  if (currentLine) lines.push(currentLine);

  return lines;
}

/**
 * Create SVG overlay for text content
 */
function createTextOverlay(speakerName, talkTitle, organization, eventName, eventDate) {
  const titleLines = wrapText(talkTitle, 35);
  const titleY = 320;
  const lineHeight = 42;

  const titleSvg = titleLines
    .map((line, i) => `<text x="480" y="${titleY + i * lineHeight}" font-family="Arial, sans-serif" font-size="36" font-weight="bold" fill="${BRAND_WHITE}">${escapeXml(line)}</text>`)
    .join("\n");

  const speakerNameY = titleY + titleLines.length * lineHeight + 30;
  const orgY = speakerNameY + 35;

  return `
    <svg width="${WIDTH}" height="${HEIGHT}" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="bgGradient" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" style="stop-color:#047857;stop-opacity:1" />
          <stop offset="100%" style="stop-color:#065f46;stop-opacity:1" />
        </linearGradient>
        <clipPath id="circleClip">
          <circle cx="240" cy="315" r="160"/>
        </clipPath>
      </defs>

      <!-- Background -->
      <rect width="${WIDTH}" height="${HEIGHT}" fill="url(#bgGradient)"/>

      <!-- Decorative elements -->
      <circle cx="1100" cy="100" r="200" fill="rgba(255,255,255,0.05)"/>
      <circle cx="1150" cy="550" r="150" fill="rgba(255,255,255,0.03)"/>

      <!-- Top bar with event info -->
      <rect x="0" y="0" width="${WIDTH}" height="80" fill="rgba(0,0,0,0.2)"/>
      <text x="40" y="52" font-family="Arial, sans-serif" font-size="32" font-weight="bold" fill="${BRAND_WHITE}">LLMDAY</text>
      <text x="180" y="52" font-family="Arial, sans-serif" font-size="28" fill="${BRAND_WHITE}">${escapeXml(eventName)}</text>
      <text x="${WIDTH - 40}" y="52" font-family="Arial, sans-serif" font-size="24" fill="${BRAND_WHITE}" text-anchor="end">${escapeXml(eventDate)}</text>

      <!-- Speaker photo placeholder circle -->
      <circle cx="240" cy="355" r="170" fill="rgba(255,255,255,0.1)" stroke="rgba(255,255,255,0.3)" stroke-width="3"/>

      <!-- Talk title -->
      ${titleSvg}

      <!-- Speaker name -->
      <text x="480" y="${speakerNameY}" font-family="Arial, sans-serif" font-size="28" fill="${BRAND_WHITE}">${escapeXml(speakerName)}</text>

      <!-- Organization -->
      <text x="480" y="${orgY}" font-family="Arial, sans-serif" font-size="22" fill="rgba(255,255,255,0.8)">${escapeXml(organization || "")}</text>

      <!-- Bottom bar -->
      <rect x="0" y="${HEIGHT - 60}" width="${WIDTH}" height="60" fill="rgba(0,0,0,0.2)"/>
      <text x="40" y="${HEIGHT - 22}" font-family="Arial, sans-serif" font-size="20" fill="rgba(255,255,255,0.9)">llmday.com</text>
      <text x="${WIDTH - 40}" y="${HEIGHT - 22}" font-family="Arial, sans-serif" font-size="18" fill="rgba(255,255,255,0.7)" text-anchor="end">#LLMDay #AI #MachineLearning</text>
    </svg>
  `;
}

/**
 * Generate speaker card image
 */
async function generateSpeakerCard(speaker, talkTitle, eventId, eventName, eventDate) {
  const photoPath = path.join(SPEAKERS_DIR, speaker.photo);

  if (!fs.existsSync(photoPath)) {
    console.warn(`  Warning: Photo not found for ${speaker.name}: ${photoPath}`);
    return null;
  }

  // Create the text overlay SVG
  const textOverlay = createTextOverlay(
    speaker.name,
    talkTitle,
    speaker.organization,
    eventName,
    eventDate
  );

  // Process speaker photo - make it circular
  const photoBuffer = await sharp(photoPath)
    .resize(320, 320, { fit: "cover" })
    .toBuffer();

  // Create circular mask
  const circleMask = Buffer.from(`
    <svg width="320" height="320">
      <circle cx="160" cy="160" r="155" fill="white"/>
    </svg>
  `);

  const circularPhoto = await sharp(photoBuffer)
    .composite([{
      input: await sharp(circleMask).toBuffer(),
      blend: "dest-in"
    }])
    .png()
    .toBuffer();

  // Create the base image with the SVG overlay
  const baseImage = await sharp(Buffer.from(textOverlay))
    .png()
    .toBuffer();

  // Composite the circular photo onto the base
  const finalImage = await sharp(baseImage)
    .composite([{
      input: circularPhoto,
      left: 80,
      top: 195
    }])
    .png()
    .toBuffer();

  return finalImage;
}

/**
 * Get event info from event ID
 */
function getEventInfo(eventId) {
  const eventMap = {
    "2026-warsaw-q1": { name: "WARSAW 2026 Q1", date: "February 12, 2026" },
    "2026-london-q2": { name: "LONDON 2026 Q2", date: "Q2 2026" },
    "2026-nyc-q1": { name: "NYC 2026 Q1", date: "Q1 2026" },
  };
  return eventMap[eventId] || { name: eventId.toUpperCase(), date: "" };
}

/**
 * Main function
 */
async function main() {
  const eventId = process.argv[2] || "2026-warsaw-q1";
  const eventInfo = getEventInfo(eventId);

  console.log(`\nGenerating speaker cards for ${eventId}...`);
  console.log(`Event: ${eventInfo.name} - ${eventInfo.date}\n`);

  const talksDir = path.join(TALKS_DIR, eventId);

  if (!fs.existsSync(talksDir)) {
    console.error(`Error: Talks directory not found: ${talksDir}`);
    process.exit(1);
  }

  // Create output directory
  const outputDir = path.join(OUTPUT_DIR, eventId);
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  // Get all talk files
  const talkFiles = fs.readdirSync(talksDir).filter(f => f.endsWith(".md"));

  console.log(`Found ${talkFiles.length} talks\n`);

  let generated = 0;
  let skipped = 0;

  for (const talkFile of talkFiles) {
    const talkPath = path.join(talksDir, talkFile);
    const content = fs.readFileSync(talkPath, "utf-8");
    const frontmatter = parseFrontmatter(content);

    if (!frontmatter || !frontmatter.speakers || frontmatter.speakers.length === 0) {
      console.log(`  Skipping ${talkFile}: No speaker info`);
      skipped++;
      continue;
    }

    for (const speaker of frontmatter.speakers) {
      if (!speaker.photo) {
        console.log(`  Skipping ${speaker.name}: No photo`);
        skipped++;
        continue;
      }

      const outputFilename = `${speaker.name.toLowerCase().replace(/\s+/g, "-")}.png`;
      const outputPath = path.join(outputDir, outputFilename);

      console.log(`  Generating: ${speaker.name}`);

      try {
        const imageBuffer = await generateSpeakerCard(
          speaker,
          frontmatter.title,
          eventId,
          eventInfo.name,
          eventInfo.date
        );

        if (imageBuffer) {
          fs.writeFileSync(outputPath, imageBuffer);
          console.log(`    ✓ Saved: ${outputPath}`);
          generated++;
        } else {
          skipped++;
        }
      } catch (error) {
        console.error(`    ✗ Error: ${error.message}`);
        skipped++;
      }
    }
  }

  console.log(`\n========================================`);
  console.log(`Generated: ${generated} images`);
  console.log(`Skipped: ${skipped}`);
  console.log(`Output directory: ${outputDir}`);
  console.log(`========================================\n`);
}

main().catch(console.error);
