-- One-off: trim stray tabs, line breaks and spaces from library titles, artists and albums.
-- New and edited rows are tidied by the Asset model since 2026-11-18; this fixes the old ones.
-- Run by hand, after a backup of the assets table. Step 1 only counts; step 2 changes rows.
-- Tabs and line breaks become one space, the ends are trimmed, an artist or album that is
-- left empty becomes NULL. A title that would be left empty is not touched (look at those).

-- 1. How many rows would change, and a sample
WITH t AS (
  SELECT id, title, artist, album,
         btrim(regexp_replace(title,  '[\t\r\n\x0b\x0c ]+', ' ', 'g')) AS new_title,
         NULLIF(btrim(regexp_replace(artist, '[\t\r\n\x0b\x0c ]+', ' ', 'g')), '') AS new_artist,
         NULLIF(btrim(regexp_replace(album,  '[\t\r\n\x0b\x0c ]+', ' ', 'g')), '') AS new_album
  FROM assets
)
SELECT count(*) FILTER (WHERE new_title <> '' AND title IS DISTINCT FROM new_title)  AS titles,
       count(*) FILTER (WHERE artist IS DISTINCT FROM new_artist)                    AS artists,
       count(*) FILTER (WHERE album  IS DISTINCT FROM new_album)                     AS albums,
       count(*) FILTER (WHERE (new_title <> '' AND title IS DISTINCT FROM new_title)
                           OR artist IS DISTINCT FROM new_artist
                           OR album  IS DISTINCT FROM new_album)                     AS rows_changed,
       count(*) FILTER (WHERE new_title = '')                                        AS empty_titles_left_alone
FROM t;

-- 2. The change (same rules). Run inside a transaction and compare the count with step 1.
-- BEGIN;
-- UPDATE assets a SET
--   title  = CASE WHEN t.new_title <> '' THEN t.new_title ELSE a.title END,
--   artist = t.new_artist,
--   album  = t.new_album
-- FROM (
--   SELECT id,
--          btrim(regexp_replace(title,  '[\t\r\n\x0b\x0c ]+', ' ', 'g')) AS new_title,
--          NULLIF(btrim(regexp_replace(artist, '[\t\r\n\x0b\x0c ]+', ' ', 'g')), '') AS new_artist,
--          NULLIF(btrim(regexp_replace(album,  '[\t\r\n\x0b\x0c ]+', ' ', 'g')), '') AS new_album
--   FROM assets
-- ) t
-- WHERE a.id = t.id
--   AND ((t.new_title <> '' AND a.title IS DISTINCT FROM t.new_title)
--        OR a.artist IS DISTINCT FROM t.new_artist
--        OR a.album  IS DISTINCT FROM t.new_album);
-- COMMIT;
