# Unified Scriptures SQLite Builder

A reproducible, read-only SQLite edition of the LDS standard works, built from
[`bcbooks/scriptures-json`](https://github.com/bcbooks/scriptures-json).

This project stands on the shoulders of [Ben Crowder](https://github.com/bencrowder) and his excellent
[`bcbooks/scriptures-json`](https://github.com/bcbooks/scriptures-json) repo. That project did the hard,
careful work of producing clean, public-domain JSON editions of the scriptures; this repo focuses on
turning that data into a navigation-friendly SQLite asset for apps and tools.

## Why this exists

I used scriptures-json on a project a few years ago. I ended up writing a decent chunk of code to be able to parse through everything. 

More recently, I began using a sqlite file from the Mormon Documentation Project, which scriptures-json is based on. I loved how simple it was to query information from the database (plus really fast!), but it was missing a lot of the metadata and even some text that scripture-json included.

This project is an attempt to marry the two projects and get the best of both worlds: full, rich scripture text, and ease of access.

The upstream JSON files are great for many use cases:

* They’re carefully edited and tracked.
* They avoid copyrighted material (footnotes, chapter summaries, introductions, etc.) while retaining the
  core text.
* They’re easy to process with standard tooling.

But production apps often need something slightly different:

* A **single, compact file** that can be shipped with a mobile or desktop app.
* A **normalized schema** that makes it trivial to jump to “Mosiah 18:9”, iterate over verses, or stream an entire chapter/section with all its metadata in order.
* A format that plays nicely with **ORMs and code generators** (Drizzle, Prizma, TypeORM, etc.) without custom per-app parsing.

This repo is a small layer on top of `scriptures-json` that answers those needs: it turns the JSON source
into a stable SQLite database with explicit tables for volumes, books, chapters, and a unified content
stream.

## Why not just use `bcbooks/scriptures-json`?

`scriptures-json` is perfect when you want raw JSON organized by volume/book, but it leaves a lot of work
to downstream consumers:

* **No unified navigation** – apps must manually stitch together volumes, books, chapters, title pages,
  testimonies, and facsimiles.
* **Missing metadata** – introductions, signatures, “The End” markers, and facsimile explanations are not
  normalized, so every client handles them differently (or ignores them).
* **Inefficient querying** – JSON is great for static publishing, but bundling it in a mobile app makes
  lookups (e.g., jump to `Mosiah 18:9`) slow and memory heavy.
* **Inconsistent schemas across projects** – every app tends to reinvent a schema for verses, headings, and
  metadata, making it harder to share tooling.

This repo **forks the upstream data** (see
[bcbooks/scriptures-json](https://github.com/bcbooks/scriptures-json) for original files) and provides a
generator that builds a compact, read-only SQLite asset with a consistent schema. Readers familiar with the upstream repo will see all the same text; readers coming in fresh can treat this as a self-contained dataset with ready-to-use tables.

## Overview

The package transforms the JSON volumes into a navigation-friendly database that can be embedded in
mobile/desktop apps. It adds consistent metadata (introductions, testimonies, facsimiles, signatures, etc.) so consumers can render a unified scripture experience without bespoke parsing logic.

Some intended use cases:

* **Mobile scripture apps** that need fast, offline verse lookups and cross-book navigation.
* **Textual/linguistic analysis tools** that want SQL queries instead of hand-parsed JSON.
* **Internal study tools** where you want a stable schema that won’t change under you.

## Source texts & copyright

This project does add some new textual content/editions, namely:

- Official Declaration 1 (Non Copyrighted Portion)
- Link to Official Declaration 2 (OD2 is still under copyright)
- Title Page for the Holy Bible
- Title Page for the Old Testament
- Unified Title Page for the Book of Mormon
- "The End" at the end of the Book of Mormon and New Testament
- Joseph Smith's signature at the end of the Articles of Faith
- Facsimiles, including text and images stored in binary png format. (Images sourced from [en.wikisource.org/wiki/Page:The_Pearl_of_Great_Price_1913.djvu/62](https://en.wikisource.org/wiki/Page:The_Pearl_of_Great_Price_1913.djvu/62)- since images are from 1913, they are in the public domain, so the Creative Commons Attribution-ShareAlike 4.0 International License does not apply, and we can use these images however we like without attribution! (including commerically)). 

For details on how the rest of the text was prepared and what is included/excluded, see the upstream
[`bcbooks/scriptures-json`](https://github.com/bcbooks/scriptures-json) documentation.

## High-level pipeline

1. **Source data** – the five JSON files (`old-testament.json`, `new-testament.json`, etc.) are imported
   unchanged from the upstream repo.
2. **Schema definition** – `scripts/generate-sqlite.py` describes the SQLite schema plus enum tables
   (chapter/content types).
3. **Population** – the script ingests every JSON file, inserts metadata rows, and writes the SQLite file to
   `scriptures.db` (no automatic copying into downstream apps).

## Schema tour

```text
volumes(id, title, long_title, subtitle, short_title, lds_url, content_start_id, content_end_id, book_count)
books(id, volume_id, title, long_title, subtitle, lds_url, content_start_id, content_end_id, chapter_count)
chapters(id, book_id, number, label, chapter_type_id, content_start_id, content_end_id, content_count, verse_count)
content(id, chapter_id, position_id, verse_number, reference, text, media, pilcrow, content_type_id, char_count)
chapter_types(id, value, label)
content_types(id, value, label)
```

### Indexes

The generator creates navigation-focused indexes:

- `idx_books_volume_id` on `books(volume_id)`
- `idx_chapters_book_id` on `chapters(book_id)`
- `idx_content_chapter_id` on `content(chapter_id)`
- `idx_content_chapter_id_verse` on `content(chapter_id, verse_number)`

These keep chapter, book, and verse-range lookups fast without loading full tables.

### Precomputed ranges

Each chapter/book/volume row includes non-null `content_start_id`, `content_end_id`, and count fields (populated by the generator) so callers can jump to content ranges and know counts without scanning tables.

**Volume fields**: `book_count`  
**Book fields**: `chapter_count`  
**Chapter fields**: `content_count`, `verse_count`  
**Content fields**: `char_count` (character count of text field)

### `volumes`
* **Source fields**: `title`, `subtitle`, `lds_slug` when available in the JSON root.
* **Usage**: Drive top-level navigation ("Old Testament", "New Testament"…).

### `books`
* **Source fields**: `book`, `full_title`, `full_subtitle`, `heading`, `lds_slug`. Only the lightweight
  fields live on the table; rich metadata is emitted into `content`.
* **Usage**: Navigate to chapters for a given book. Use `books.id` to join into
  `chapters` or to prefetch the first chapter for a given book.

### `chapters`
* **Source fields**: `chapter`, `reference`, `section`, `facsimiles[].number`, synthetic chapters for
  introductions/testimonies.
* **Usage**: `number` stays NULL for introductions and facsimiles. `label` contains the display name
  (e.g., "Facsimile 1", "New Testament Title Page", or just the chapter number). `chapter_type_id` maps
  to `chapter_types` so you can tell a `section` from a `facsimile`.

### `content`
This is the heart of the DB. Every verse, heading, title, introduction paragraph, signature, and facsimile
explanation shows up here. Each row is scoped to a chapter and ordered with `position_id`.

* **Source fields**:
  * JSON verse objects → `content_type="verse"` (with `verse_number`, `reference`, `text`, `pilcrow`).
  * Book headings/notes → `content_type="heading"` rows before the verses.
  * Title pages / testimonies / facsimile entries come from the helper metadata defined in
    `scripts/generate-sqlite.py` (see `build_intro_chapters` and `emit_book_metadata_content`).
  * Closing text (`The End`, signatures) gets written with `content_type="closing_text"` or
    `"signature"`.
  * Facsimile image data stored as binary in `media` column with `content_type="media_binary"`.
* **Usage**: Query by `chapter_id` + `ORDER BY position_id` to render an entire chapter/section exactly as a
  reader should see it (titles → subtitles → headings → verses → metadata). To look up a verse, filter on
  `content_type_id` joined to `content_types` where `value = 'verse'` and match `reference` or
  (`chapter_id`, `verse_number`).

### Enum tables

`chapter_types` and `content_types` are seeded at build time:

| `chapter_types.value`        | Meaning / Source                                                                 |
|-----------------------------|------------------------------------------------------------------------------------|
| `chapter`                   | Standard chapter (Genesis 1, 1 Nephi 3, etc.)                                     |
| `section`                   | Doctrine &amp; Covenants sections (derived from `sections[].section`)                 |
| `official_declaration`      | Official Declarations 1 &amp; 2 (Not found in scriptures-json, additions)        |
| `facsimile`                 | Pearl of Great Price facsimiles (chapters inserted from `facsimiles[]`)          |
| `introduction`              | Synthetic introduction books/chapters (title pages, testimonies, etc.)           |

`content_types` captures every block that can appear in reading order:

| `content_types.value`             | Example source                              | Notes                                                              |
|--------------------|---------------------------------------------|--------------------------------------------------------------------|
| `verse`            | `verses[].text`                             | Includes `verse_number`, `reference`, `pilcrow` flags              |
| `paragraph`        | Intro paragraphs, facsimile explanations    | Text-only rows; no verse refs                                      |
| `title`            | Title pages, book titles                    | Often emitted before the first chapter of a book                   |
| `subtitle`         | Subtitle lines from title pages             | e.g., "Another Testament of Jesus Christ"                          |
| `subsubtitle`      | Additional title-page lines ("THE", "THE HAND OF…") |
| `chapter_name`     | "Chapter 9", "Section 115", "A Facsimile…"  | Always the first element within a chapter                          |
| `heading`          | Chapter headings/notes from JSON            | Includes psalm headings, BOM chapter headings, etc.                |
| `psalm_119_name`   | Psalm 119 Hebrew section names (`heading`)  | Matches JSON `heading` for verses that include `pilcrow` sections  |
| `psalm_119_heading`| Psalm 119 subheadings (`subheading`)        | Rare field used in OT JSON                                         |
| `media_binary`     | Facsimile image binary data                 | Stored as binary PNG data in the media BLOB column                |
| `link`             | Hyperlinks (e.g., OD2 link)                 | Clickable URLs stored as text                                      |
| `signature`        | D&C signatures, witness names, Articles of Faith closing |
| `closing_text`     | “The End” (OT/NT/BOM)                       | Appended to the final chapter of a volume                          |
| `space`            | Layout spacer in title pages                | Handy for rendering blank lines                                    |

## Usage

### Prerequisites

* Python 3 (uses stdlib only)
* The JSON files from `bcbooks/scriptures-json` copied into this directory

### Generate the database

```bash
python3 scripts/generate-sqlite.py           # writes scriptures.db
python3 scripts/generate-sqlite.py --output ./my_scriptures.db
```

The script applies the schema and populates the tables. On success you’ll see a summary line with record
counts.

### Validating the output

```bash
sqlite3 scriptures.db ".tables"
sqlite3 scriptures.db "SELECT COUNT(*) FROM verses_view;"  # optional view
# e.g., inspect Genesis 1 metadata:
sqlite3 scriptures.db "
  SELECT ct.value, c.text
  FROM content c
  JOIN content_types ct ON ct.id = c.content_type_id
  WHERE chapter_id = (
    SELECT chapters.id FROM chapters
    JOIN books ON books.id = chapters.book_id
    WHERE books.title = 'Genesis' AND chapters.number = 1
  )
  ORDER BY position_id;
"
```

### Extending / customizing

* **Additional metadata:** Add new `content_type` rows and extend
  `emit_book_metadata_content` / `emit_intro_content` to produce them.
* **Alternate formats:** Duplicate the generator and swap the SQL DDL for MySQL/Postgres, or emit
  CSV/Parquet after the `ScriptureDatabaseBuilder` populates its internal counters.
* **Filtering volumes:** Use the `--volumes` flag (coming soon) or modify `JSON_SOURCES` to limit the input
  set.

## Acknowledgements

Huge thanks to [Ben Crowder](https://github.com/bencrowder) for creating and maintaining
[`bcbooks/scriptures-json`](https://github.com/bcbooks/scriptures-json). This project is only possible
because of that meticulous work; all credit for the underlying text and JSON structure belongs there.

## Contributing

1. Fork the repo (or keep a subtree) and run the generator locally.
2. Open PRs with schema/docs improvements or updated JSON sources.
3. Please attribute `bcbooks/scriptures-json` when redistributing derivative DBs.

## License

The upstream JSON files are public domain (per `bcbooks/scriptures-json`). All generator code in this repo
is released under the MIT License.
