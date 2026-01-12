// @ts-check
import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';

// https://astro.build/config
export default defineConfig({
  site: process.env.ASTRO_SITE || 'https://llmday.com',
  base: process.env.ASTRO_BASE || '/',
  vite: {
    plugins: [tailwindcss()],
  },
});
