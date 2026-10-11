# City panoramas

One picture per city, used as the background of the **City view** talk teasers
(`/<event>/teasers/`, "Modern | City view" on the Talk teasers tab).

- Name the file exactly as the event's `city_name` in its `metadata.yml`:
  `Austin.png`, `NYC.png`, `San Francisco.png`, `Tel Aviv.png` (case doesn't matter).
- One file per city: every edition in that city uses it.
- `.png`, `.jpg`, `.jpeg` or `.webp`, roughly square, at least 1200 px. The skyline
  should sit in the upper half: the bottom of the picture ends up behind the footer.
- The build tints it in the brand's colours (`_build/generate.py`, `_TZ_CITY`) into
  `teasers/city-<hash>.jpg`; the original here is not deployed.
- No picture for the city means no City view for that event. A new or changed
  picture redraws only that city's City view cards.
