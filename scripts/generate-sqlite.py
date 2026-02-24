#!/usr/bin/env python3
"""Build the unified scriptures.db using the simplified 4-table schema.

Tables
------
volumes(id, code, title, long_title, subtitle, short_title, lds_url)
books(id, volume_id, title, long_title, subtitle, lds_url)
chapters(id, book_id, number, label, chapter_type_id)
content(id, chapter_id, position_id, verse_number, reference, text, pilcrow, content_type_id)

Enum helpers
------------
chapter_types(id, value, label) : chapter, section, official_declaration, facsimile, introduction
content_types(id, value, label) : verse, paragraph, title, subtitle, subsubtitle, signature,
                                 closing_text, chapter_name, heading, psalm_119_name,
                                 psalm_119_heading, media_binary, space, link
"""

from __future__ import annotations

from datetime import datetime
import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent / ".."
DEFAULT_DB = ROOT / "scriptures.db"

JSON_SOURCES = {
    "old-testament": ROOT / "old-testament.json",
    "new-testament": ROOT / "new-testament.json",
    "book-of-mormon": ROOT / "book-of-mormon.json",
    "doctrine-and-covenants": ROOT / "doctrine-and-covenants.json",
    "pearl-of-great-price": ROOT / "pearl-of-great-price.json",
}

CHAPTER_TYPES = [
    ("chapter", "Chapter"),
    ("section", "Section"),
    ("official_declaration", "Official Declaration"),
    ("facsimile", "Facsimile"),
    ("introduction", "Introduction"),
]

CONTENT_TYPES = [
    ("verse", "Verse"),
    ("paragraph", "Paragraph"),
    ("title", "Title"),
    ("subtitle", "Subtitle"),
    ("subsubtitle", "Subsubtitle"),
    ("signature", "Signature"),
    ("closing_text", "Closing Text"),
    ("chapter_name", "Chapter Name"),
    ("heading", "Heading"),
    ("psalm_119_name", "Psalm 119 Section Name"),
    ("psalm_119_heading", "Psalm 119 Section Heading"),
    ("media_binary", "Media Binary"),
    ("space", "Whitespace Spacer"),
    ("link", "Link"),
]


def load_payloads() -> Dict[str, Dict[str, Any]]:
    payloads = {}
    for key, path in JSON_SOURCES.items():
        with path.open("r", encoding="utf-8") as fh:
            payloads[key] = json.load(fh)
    return payloads


def ensure_schema(conn: sqlite3.Connection) -> None:
    # Schema plus navigation indexes tuned for common queries:
    # - precomputed content id ranges per chapter/book/volume
    # - content by chapter (and verse ranges)
    # - chapters by book
    # - books by volume
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS chapter_types (
            id INTEGER PRIMARY KEY,
            value TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS content_types (
            id INTEGER PRIMARY KEY,
            value TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS volumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            long_title TEXT,
            subtitle TEXT,
            short_title TEXT,
            lds_url TEXT,
            content_start_id INTEGER NOT NULL DEFAULT -1,
            content_end_id INTEGER NOT NULL DEFAULT -1,
            book_count INTEGER NOT NULL DEFAULT -1
        );

        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            volume_id INTEGER NOT NULL REFERENCES volumes(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            long_title TEXT NOT NULL,
            subtitle TEXT,
            lds_url TEXT,
            content_start_id INTEGER NOT NULL DEFAULT -1,
            content_end_id INTEGER NOT NULL DEFAULT -1,
            chapter_count INTEGER NOT NULL DEFAULT -1
        );

        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            number INTEGER,
            label TEXT NOT NULL,
            chapter_type_id INTEGER REFERENCES chapter_types(id),
            content_start_id INTEGER NOT NULL DEFAULT -1,
            content_end_id INTEGER NOT NULL DEFAULT -1,
            content_count INTEGER NOT NULL DEFAULT -1,
            verse_count INTEGER NOT NULL DEFAULT -1
        );

        CREATE TABLE IF NOT EXISTS content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
            position_id INTEGER NOT NULL,
            verse_number INTEGER,
            reference TEXT,
            text TEXT,
            media BLOB,
            pilcrow BOOLEAN NOT NULL DEFAULT FALSE,
            content_type_id INTEGER NOT NULL REFERENCES content_types(id),
            char_count INTEGER NOT NULL DEFAULT -1,
            UNIQUE(chapter_id, position_id)
        );

        CREATE INDEX IF NOT EXISTS idx_books_volume_id
          ON books(volume_id);

        CREATE INDEX IF NOT EXISTS idx_chapters_book_id
          ON chapters(book_id);

        CREATE INDEX IF NOT EXISTS idx_content_chapter_id
          ON content(chapter_id);

        CREATE INDEX IF NOT EXISTS idx_content_chapter_id_verse
          ON content(chapter_id, verse_number);
        """
    )
    conn.executemany(
        "INSERT OR IGNORE INTO chapter_types(id,value,label) VALUES (?, ?, ?)",
        [(idx + 1, value, label) for idx, (value, label) in enumerate(CHAPTER_TYPES)],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO content_types(id,value,label) VALUES (?, ?, ?)",
        [(idx + 1, value, label) for idx, (value, label) in enumerate(CONTENT_TYPES)],
    )


def enum_lookup(conn: sqlite3.Connection, table: str) -> Dict[str, int]:
    cur = conn.execute(f"SELECT value, id FROM {table}")
    return {value: enum_id for value, enum_id in cur.fetchall()}


def insert_volume(
    conn: sqlite3.Connection,
    *,
    payload: Dict[str, Any],
) -> int:
    title = "Error" # should be set later to remove error
    short_title = "Error"
    match payload.get("title"):
        case "The Old Testament":
            title = "Old Testament"
            short_title = "OT"
        case "The New Testament":
            title = "New Testament"
            short_title = "NT"
        case "The Book of Mormon":
            title = "Book of Mormon"
            short_title = "BoM"
        case "The Doctrine and Covenants":
            title = "Doctrine and Covenants"
            short_title = "D&C"
        case "The Pearl of Great Price":
            title = "Pearl of Great Price"
            short_title = "PGP"
    conn.execute(
        """
        INSERT INTO volumes (title, long_title, subtitle, short_title, lds_url)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            title,
            payload.get("title"),
            payload.get("subtitle"),
            short_title,
            payload.get("lds_slug"),
        ),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def insert_book(
    conn: sqlite3.Connection,
    volume_id: int,
    *,
    title: str,
    long_title: str,
    subtitle: Optional[str] = None,
    lds_url: Optional[str] = None,
) -> int:
    conn.execute(
        """
        INSERT INTO books (
            volume_id, title, long_title, subtitle, lds_url
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            volume_id,
            title,
            long_title,
            subtitle,
            lds_url,
        ),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def insert_chapter(
    conn: sqlite3.Connection,
    book_id: int,
    *,
    number: Optional[int],
    label: Optional[str],
    chapter_type_id: Optional[int],
) -> int:
    conn.execute(
        """
        INSERT INTO chapters (book_id, number, label, chapter_type_id)
        VALUES (?, ?, ?, ?)
        """,
        (book_id, number, label or number, chapter_type_id),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def insert_content(
    conn: sqlite3.Connection,
    chapter_id: int,
    *,
    position_id: int,
    content_type_id: int,
    text: Optional[str] = None,
    media: Optional[bytes] = None,
    verse_number: Optional[int] = None,
    reference: Optional[str] = None,
    pilcrow: bool = False,
):
    char_count = len(text) if text else 0
    conn.execute(
        """
        INSERT INTO content (
            chapter_id, position_id, verse_number, reference, text,
            media, pilcrow, content_type_id, char_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chapter_id,
            position_id,
            verse_number,
            reference,
            text,
            media,
            pilcrow,
            content_type_id,
            char_count,
        ),
    )

def compute_content_ranges(conn: sqlite3.Connection) -> None:
    """Populate precomputed content id ranges for chapters, books, and volumes."""
    conn.executescript(
        """
        UPDATE chapters SET
          content_start_id = (
            SELECT MIN(c.id) FROM content c WHERE c.chapter_id = chapters.id
          ),
          content_end_id = (
            SELECT MAX(c.id) FROM content c WHERE c.chapter_id = chapters.id
          ),
          content_count = (
            SELECT COUNT(*) FROM content c WHERE c.chapter_id = chapters.id
          ),
          verse_count = (
            SELECT COUNT(*)
            FROM content c
            JOIN content_types ct ON c.content_type_id = ct.id
            WHERE c.chapter_id = chapters.id AND ct.value = 'verse'
          );

        UPDATE books SET
          content_start_id = (
            SELECT MIN(c.id)
            FROM chapters ch
            JOIN content c ON c.chapter_id = ch.id
            WHERE ch.book_id = books.id
          ),
          content_end_id = (
            SELECT MAX(c.id)
            FROM chapters ch
            JOIN content c ON c.chapter_id = ch.id
            WHERE ch.book_id = books.id
          ),
          chapter_count = (
            SELECT COUNT(*) FROM chapters ch WHERE ch.book_id = books.id
          );

        UPDATE volumes SET
          content_start_id = (
            SELECT MIN(c.id)
            FROM books b
            JOIN chapters ch ON ch.book_id = b.id
            JOIN content c ON c.chapter_id = ch.id
            WHERE b.volume_id = volumes.id
          ),
          content_end_id = (
            SELECT MAX(c.id)
            FROM books b
            JOIN chapters ch ON ch.book_id = b.id
            JOIN content c ON c.chapter_id = ch.id
            WHERE b.volume_id = volumes.id
          ),
          book_count = (
            SELECT COUNT(*) FROM books b WHERE b.volume_id = volumes.id
          );
        """
    )


def fold_lines(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, list):
        parts = [part.strip() for part in value if str(part).strip()]
        return "\n\n".join(parts) if parts else None
    text = str(value).strip()
    return text or None


def append_intro_content(
    conn: sqlite3.Connection,
    *,
    book_id: int,
    intro_chapters: List[Dict[str, Any]],
    chapter_types: Dict[str, int],
    content_types: Dict[str, int],
) -> None:
    if not intro_chapters:
        return
    intro_type_id = chapter_types["introduction"]
    for sort_idx, chapter in enumerate(intro_chapters, start=1):
        chapter_id = insert_chapter(
            conn,
            book_id,
            number=None,
            label=chapter["title"],
            chapter_type_id=intro_type_id
        )
        position = 0
        for entry in chapter["entries"]:
            position += 1
            insert_content(
                conn,
                chapter_id,
                position_id=position,
                content_type_id=content_types[entry["type"]],
                text=entry.get("text"),
            )


def build_database(output: Path) -> None:
    payloads = load_payloads()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    conn = sqlite3.connect(output)
    try:
        ensure_schema(conn)
        conn.commit()
        chapter_types = enum_lookup(conn, "chapter_types")
        content_types = enum_lookup(conn, "content_types")
        conn.execute("BEGIN")

        for dataset_code, payload in payloads.items():
            volume_id = insert_volume(conn, payload=payload)
            if dataset_code != "doctrine-and-covenants":
                intro_book_id = insert_book(
                    conn,
                    volume_id,
                    title="Intro",
                    long_title="Introduction",
                )

                intro_chapters = build_intro_chapters(dataset_code, payload)
                append_intro_content(
                    conn,
                    book_id=intro_book_id,
                    intro_chapters=intro_chapters,
                    chapter_types=chapter_types,
                    content_types=content_types,
                )

            books_payload = normalize_books(dataset_code, payload)
            for sort_idx, book_payload in enumerate(books_payload, start=1):
                book_id = insert_book(
                    conn,
                    volume_id,
                    title=book_payload["book"],
                    long_title=book_payload["title"],
                    subtitle=book_payload.get("book_subtitle") or book_payload.get("heading"),
                    lds_url=book_payload.get("lds_slug"),
                )
                ingest_book_content(
                    conn,
                    book_id,
                    book_payload,
                    chapter_types=chapter_types,
                    content_types=content_types,
                )
                if dataset_code == "doctrine-and-covenants":
                    append_official_declarations(
                        conn,
                        book_id,
                        chapter_types=chapter_types,
                        content_types=content_types,
                    )

            closing_text_value = payload.get("the_end")
            if dataset_code in {"new-testament", "book-of-mormon"}:
                closing_text_value = closing_text_value or "The End"
            if closing_text_value:
                append_volume_closing_text(
                    conn,
                    volume_id,
                    closing_text_value,
                    content_types,
                )

        # Precompute content id ranges for fast navigation in downstream apps.
        compute_content_ranges(conn)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def build_intro_chapters(dataset_code: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    def entry(content_type: str, text: Optional[str] = None) -> Dict[str, Any]:
        return {"type": content_type, "text": text}

    def space() -> Dict[str, Any]:
        return entry("space")

    chapters: List[Dict[str, Any]] = []

    if dataset_code == "old-testament":
        chapters.append(
            {
                "title": "Holy Bible Title Page",
                "entries": [
                    entry("subsubtitle", "THE"),
                    entry("title", "HOLY BIBLE"),
                    space(),
                    entry("subsubtitle", "CONTAINING THE"),
                    entry("subtitle", "OLD AND NEW TESTAMENTS"),
                    space(),
                    entry(
                        "subsubtitle",
                        "TRANSLATED OUT OF THE ORIGINAL TONGUES AND WITH THE FORMER TRANSLATION DILIGENTLY COMPARED AND REVISED BY HIS MAJESTY’S SPECIAL COMMAND",
                    ),
                ],
            }
        )
        ot_entries = [
            entry("subtitle", "THE"),
            entry("title", "OLD TESTAMENT"),
            space(),
            entry("subsubtitle", "TO THE MOST HIGH AND MIGHTY PRINCE"),
            entry("subtitle", "JAMES"),
            space(),
            entry("subsubtitle", "BY THE GRACE OF GOD"),
            space(),
            entry("subtitle", "KING OF GREAT BRITAIN, FRANCE, AND IRELAND, DEFENDER OF THE FAITH, &C."),
            space(),
            entry(
                "subsubtitle",
                "THE TRANSLATORS OF THE BIBLE WISH GRACE, MERCY, AND PEACE, THROUGH JESUS CHRIST OUR LORD",
            ),
        ]
        ot_paragraphs = [
            "Great and manifold were the blessings, most dread Sovereign, which Almighty God, the Father of all mercies, bestowed upon us the people of England, when first he sent Your Majesty’s Royal Person to rule and reign over us. For whereas it was the expectation of many, who wished not well unto our Sion, that upon the setting of that bright Occidental Star, Queen Elizabeth of most happy memory, some thick and palpable clouds of darkness would so have overshadowed this Land, that men should have been in doubt which way they were to walk; and that it should hardly be known, who was to direct the unsettled State; the appearance of Your Majesty, as of the Sun in his strength, instantly dispelled those supposed and surmised mists, and gave unto all that were well affected exceeding cause of comfort; especially when we beheld the Government established in Your Highness, and Your hopeful Seed, by an undoubted Title, and this also accompanied with peace and tranquillity at home and abroad.",
            "But among all our joys, there was no one that more filled our hearts, than the blessed continuance of the preaching of God’s sacred Word among us; which is that inestimable treasure, which excelleth all the riches of the earth; because the fruit thereof extendeth itself, not only to the time spent in this transitory world, but directeth and disposeth men unto that eternal happiness which is above in heaven.",
            "Then not to suffer this to fall to the ground, but rather to take it up, and to continue it in that state, wherein the famous Predecessor of Your Highness did leave it: nay, to go forward with the confidence and resolution of a Man in maintaining the truth of Christ, and propagating it far and near, is that which hath so bound and firmly knit the hearts of all Your Majesty’s loyal and religious people unto You, that Your very name is precious among them: their eye doth behold You with comfort, and they bless You in their hearts, as that sanctified Person, who, under God, is the immediate Author of their true happiness. And this their contentment doth not diminish or decay, but every day increaseth and taketh strength, when they observe, that the zeal of Your Majesty toward the house of God doth not slack or go backward, but is more and more kindled, manifesting itself abroad in the farthest parts of Christendom, by writing in defence of the Truth, (which hath given such a blow unto that man of sin, as will not be healed,) and every day at home, by religious and learned discourse, by frequenting the house of God, by hearing the Word preached, by cherishing the Teachers thereof, by caring for the Church, as a most tender and loving nursing Father.",
            "There are infinite arguments of this right Christian and religious affection in Your Majesty; but none is more forcible to declare it to others than the vehement and perpetuated desire of accomplishing and publishing of this work, which now with all humility we present unto Your Majesty. For when Your Highness had once out of deep judgment apprehended how convenient it was, that out of the Original Sacred Tongues, together with comparing of the labours, both in our own, and other foreign Languages, of many worthy men who went before us, there should be one more exact Translation of the holy Scriptures into the English Tongue; Your Majesty did never desist to urge and to excite those to whom it was commended, that the work might be hastened, and that the business might be expedited in so decent a manner, as a matter of such importance might justly require.",
            "And now at last, by the mercy of God, and the continuance of our labours, it being brought unto such a conclusion, as that we have great hopes that the Church of England shall reap good fruit thereby; we hold it our duty to offer it to Your Majesty, not only as to our King and Sovereign, but as to the principal Mover and Author of the work: humbly craving of Your most Sacred Majesty, that since things of this quality have ever been subject to the censures of illmeaning and discontented persons, it may receive approbation and patronage from so learned and judicious a Prince as Your Highness is, whose allowance and acceptance of our labours shall more honour and encourage us, than all the calumniations and hard interpretations of other men shall dismay us. So that if, on the one side, we shall be traduced by Popish Persons at home or abroad, who therefore will malign us, because we are poor instruments to make God’s holy Truth to be yet more and more known unto the people, whom they desire still to keep in ignorance and darkness; or if, on the other side, we shall be maligned by selfconceited Brethren, who run their own ways, and give liking unto nothing, but what is framed by themselves, and hammered on their anvil; we may rest secure, supported within by the truth and innocency of a good conscience, having walked the ways of simplicity and integrity, as before the Lord; and sustained without by the powerful protection of Your Majesty’s grace and favour, which will ever give countenance to honest and Christian endeavours against bitter censures and uncharitable imputations.",
        ]
        for paragraph in ot_paragraphs:
            ot_entries.append(entry("paragraph", paragraph))
        ot_entries.append(space())
        ot_entries.append(
            entry(
                "heading",
                "The Lord of heaven and earth bless Your Majesty with many and happy days, that, as his heavenly hand hath enriched Your Highness with many singular and extraordinary graces, so You may be the wonder of the world in this latter age for happiness and true felicity, to the honour of that great GOD, and the good of his Church, through Jesus Christ our Lord and only Saviour.",
            )
        )
        chapters.append({"title": "Title Page", "entries": ot_entries})
    elif dataset_code == "new-testament":
        chapters.append(
            {
                "title": "Title Page",
                "entries": [
                    entry("subsubtitle", "THE"),
                    entry("title", "NEW TESTAMENT"),
                    space(),
                    entry("subsubtitle", "OF OUR LORD AND SAVIOUR"),
                    entry("subsubtitle", "JESUS CHRIST"),
                    space(),
                    entry(
                        "subsubtitle",
                        "TRANSLATED OUT OF THE ORIGINAL GREEK: AND WITH THE FORMER TRANSLATIONS DILIGENTLY COMPARED AND REVISED, BY HIS MAJESTY’S SPECIAL COMMAND",
                    ),
                ],
            }
        )
    elif dataset_code == "book-of-mormon":
        bom_entries = [
            entry("subsubtitle", "THE"),
            entry("title", "BOOK OF MORMON"),
            space(),
            entry("subtitle", "ANOTHER TESTAMENT OF"),
            entry("subtitle", "JESUS CHRIST"),
            space(),
            entry("subtitle", "AN ACCOUNT WRITTEN BY"),
            entry("subsubtitle", "THE HAND OF MORMON"),
            space(),
            entry("title", "UPON PLATES"),
            entry("title", "TAKEN FROM THE PLATES OF NEPHI"),
            space(),
            entry(
                "paragraph",
                "Wherefore, it is an abridgment of the record of the people of Nephi, and also of the Lamanites—Written to the Lamanites, who are a remnant of the house of Israel; and also to Jew and Gentile—Written by way of commandment, and also by the spirit of prophecy and of revelation—Written and sealed up, and hid up unto the Lord, that they might not be destroyed—To come forth by the gift and power of God unto the interpretation thereof—Sealed by the hand of Moroni, and hid up unto the Lord, to come forth in due time by way of the Gentile—The interpretation thereof by the gift of God.",
            ),
            entry(
                "paragraph",
                "An abridgment taken from the Book of Ether also, which is a record of the people of Jared, who were scattered at the time the Lord confounded the language of the people, when they were building a tower to get to heaven—Which is to show unto the remnant of the house of Israel what great things the Lord hath done for their fathers; and that they may know the covenants of the Lord, that they are not cast off forever—And also to the convincing of the Jew and Gentile that Jesus is the Christ, the Eternal God, manifesting himself unto all nations—And now, if there are faults they are the mistakes of men; wherefore, condemn not the things of God, that ye may be found spotless at the judgment-seat of Christ.",
            ),
            entry("signature", "TRANSLATED BY JOSEPH SMITH, Jun."),
        ]
        chapters.append({"title": "Title Page", "entries": bom_entries})
        for testimony in payload.get("testimonies", []):
            entries = [
                entry("chapter_name", testimony.get("title")),
                entry("paragraph", fold_lines(testimony.get("text"))),
            ]
            for witness in testimony.get("witnesses") or []:
                entries.append(entry("signature", witness))
            chapters.append({"title": testimony.get("title"), "entries": entries})
    elif dataset_code == "doctrine-and-covenants":
        chapters.append(
            {
                "title": "Title Page",
                "entries": [
                    entry("subsubtitle", "THE"),
                    entry("title", "DOCTRINE AND COVENANTS"),
                    space(),
                    entry("subsubtitle", "OF THE CHURCH OF JESUS CHRIST OF LATTER-DAY SAINTS"),
                    space(),
                    entry(
                        "subtitle",
                        "CONTAINING REVELATIONS GIVEN TO JOSEPH SMITH, THE PROPHET WITH SOME ADDITIONS BY HIS SUCCESSORS IN THE PRESIDENCY OF THE CHURCH",
                    ),
                ],
            }
        )
    elif dataset_code == "pearl-of-great-price":
        chapters.append(
            {
                "title": "Title Page",
                "entries": [
                    entry("subsubtitle", "THE"),
                    entry("title", "PEARL OF GREAT PRICE"),
                    space(),
                    entry(
                        "subtitle",
                        "A SELECTION FROM THE REVELATIONS, TRANSLATIONS, AND NARRATIONS OF JOSEPH SMITH FIRST PROPHET, SEER, AND REVELATOR TO THE CHURCH OF JESUS CHRIST OF LATTER-DAY SAINTS",
                    ),
                ],
            }
        )

    return chapters

def build_official_declarations() -> List[List[Tuple[str, str]]]:
    chapters: List[List[Tuple[str, str]]] = [
        [
            ("chapter_name", "Official Declaration 1"),
            ("heading", "To Whom It May Concern:"),
            ("paragraph", "Press dispatches having been sent for political purposes, from Salt Lake City, which have been widely published, to the effect that the Utah Commission, in their recent report to the Secretary of the Interior, allege that plural marriages are still being solemnized and that forty or more such marriages have been contracted in Utah since last June or during the past year, also that in public discourses the leaders of the Church have taught, encouraged and urged the continuance of the practice of polygamy—"),
            ("paragraph", "I, therefore, as President of The Church of Jesus Christ of Latter-day Saints, do hereby, in the most solemn manner, declare that these charges are false. We are not teaching polygamy or plural marriage, nor permitting any person to enter into its practice, and I deny that either forty or any other number of plural marriages have during that period been solemnized in our Temples or in any other place in the Territory."),
            ("paragraph", "One case has been reported, in which the parties allege that the marriage was performed in the Endowment House, in Salt Lake City, in the Spring of 1889, but I have not been able to learn who performed the ceremony; whatever was done in this matter was without my knowledge. In consequence of this alleged occurrence the Endowment House was, by my instructions, taken down without delay."),
            ("paragraph", "Inasmuch as laws have been enacted by Congress forbidding plural marriages, which laws have been pronounced constitutional by the court of last resort, I hereby declare my intention to submit to those laws, and to use my influence with the members of the Church over which I preside to have them do likewise."),
            ("paragraph", "There is nothing in my teachings to the Church or in those of my associates, during the time specified, which can be reasonably construed to inculcate or encourage polygamy; and when any Elder of the Church has used language which appeared to convey any such teaching, he has been promptly reproved. And I now publicly declare that my advice to the Latter-day Saints is to refrain from contracting any marriage forbidden by the law of the land."),
            ("signature", "Wilford Woodruff"),
            ("signature", "President of The Church of Jesus Christ of Latter-day Saints."),
        ],
        [
            ("chapter_name", "Official Declaration 2"),
            ("heading", "Official Declaration 2 is currently under copyright. The text may be found at:"),
            ("link", "https://www.churchofjesuschrist.org/study/scriptures/dc-testament/od/2"),
        ],
    ]
    return chapters


def append_official_declarations(
    conn: sqlite3.Connection,
    book_id: int,
    *,
    chapter_types: Dict[str, int],
    content_types: Dict[str, int]
) -> None:
    declarations = build_official_declarations()
    if not declarations:
        return

    decl_type_id = chapter_types["official_declaration"]

    for idx, entries in enumerate(declarations, start=1):
        chapter_id = insert_chapter(
            conn,
            book_id,
            number=None,
            label=f"Official Declaration {idx}",
            chapter_type_id=decl_type_id,
        )
        position = 0
        for content_type_value, text in entries:
            position += 1
            insert_content(
                conn,
                chapter_id,
                position_id=position,
                content_type_id=content_types[content_type_value],
                text=text,
            )


def normalize_books(dataset_code: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if dataset_code == "doctrine-and-covenants":
        intro_chapters = build_intro_chapters(dataset_code, payload)
        prepended_chapters = [
            {
                "label": intro["title"],
                "_chapter_type": "introduction",
                "entries": intro["entries"],
            }
            for intro in intro_chapters
        ]

        sections = payload.get("sections") or []
        synthetic_book = {
            "code": "dc",
            "title": payload.get("title", "Doctrine and Covenants"),
            "book": payload.get("title"),
            "chapters": prepended_chapters + [
                {
                    "chapter": section.get("section"),
                    "section": section.get("section"),
                    "reference": section.get("reference"),
                    "verses": section.get("verses"),
                    "signature": section.get("signature"),
                    "_chapter_type": "section",
                }
                for section in sections
            ],
        }
        return [synthetic_book]

    books = payload.get("books") or []
    for book in books:
        book["code"] = book.get("lds_slug") or book.get("book", "").lower().replace(" ", "-")
        book["title"] = book.get("full_title") or book.get("book")
    return books


def is_articles_of_faith(book_payload: Dict[str, Any]) -> bool:
    raw_title = book_payload.get("book") or book_payload.get("title") or ""
    return raw_title.strip().lower() == "articles of faith"


def ingest_book_content(
    conn: sqlite3.Connection,
    book_id: int,
    book_payload: Dict[str, Any],
    *,
    chapter_types: Dict[str, int],
    content_types: Dict[str, int],
) -> None:
    chapters = book_payload.get("chapters") or []
    for sort_idx, chapter_payload in enumerate(chapters, start=1):
        chapter_type_value = chapter_payload.get("_chapter_type", "chapter")
        chapter_type_id = chapter_types.get(chapter_type_value)
        label = chapter_payload.get("label")
        chapter_id = insert_chapter(
            conn,
            book_id,
            number=chapter_payload.get("chapter"),
            label=label,
            chapter_type_id=chapter_type_id,
        )
        emit_book_metadata_content(
            conn,
            chapter_id,
            book_payload,
            chapter_payload,
            is_first_chapter=(sort_idx == 1),
            chapter_type_value=chapter_type_value,
            content_types=content_types,
        )
        emit_chapter_heading_and_notes(
            conn, chapter_id, chapter_payload, content_types=content_types
        )

        if chapter_payload.get("entries"):
            position = chapter_id_content_position(conn, chapter_id)
            for entry in chapter_payload["entries"]:
                position += 1
                insert_content(
                    conn,
                    chapter_id,
                    position_id=position,
                    content_type_id=content_types[entry["type"]],
                    text=entry.get("text"),
                )

        emit_verses(conn, chapter_id, chapter_payload.get("verses") or [], content_types)

        signature = chapter_payload.get("signature")
        if signature:
            append_signature(conn, chapter_id, signature, content_types)

    if book_payload.get("facsimiles"):
        ingest_facsimiles(
            conn,
            book_id,
            book_payload,
            book_payload["facsimiles"],
            chapter_types,
            content_types,
        )

    if is_articles_of_faith(book_payload):
        append_articles_of_faith_signature(conn, book_id, content_types)


def emit_book_metadata_content(
    conn: sqlite3.Connection,
    chapter_id: int,
    book_payload: Dict[str, Any],
    chapter_payload: Dict[str, Any],
    *,
    is_first_chapter: bool,
    chapter_type_value: str,
    content_types: Dict[str, int],
) -> None:
    book_code = (book_payload.get("code") or "").lower()
    if book_code.endswith("-intro") or chapter_type_value == "introduction":
        return

    position = chapter_id_content_position(conn, chapter_id)

    def push(value: Optional[str], content_type: str) -> None:
        nonlocal position
        if value:
            position += 1
            insert_content(
                conn,
                chapter_id,
                position_id=position,
                content_type_id=content_types[content_type],
                text=value,
            )

    if is_first_chapter:
        push(book_payload.get("title"), "title")
        subtitle = (
            book_payload.get("book_subtitle")
            or book_payload.get("full_subtitle")
            or book_payload.get("subtitle")
        )
        push(subtitle, "subtitle")
        push(book_payload.get("heading"), "heading")

    chapter_name = None
    chapter_number = chapter_payload.get("chapter")
    section_number = chapter_payload.get("section")
    if chapter_type_value == "section":
        num = section_number or chapter_number
        if num is not None:
            chapter_name = f"Section {num}"
    elif chapter_type_value == "official_declaration":
        num = chapter_number or section_number
        if num is not None:
            chapter_name = f"Official Declaration {num}"
    elif chapter_type_value == "facsimile":
        chapter_name = chapter_payload.get("label")
    else:
        if chapter_number is not None:
            chapter_name = f"Chapter {chapter_number}"

    if not chapter_name:
        chapter_name = chapter_payload.get("reference")

    push(chapter_name, "chapter_name")


def emit_chapter_heading_and_notes(
    conn: sqlite3.Connection,
    chapter_id: int,
    chapter_payload: Dict[str, Any],
    *,
    content_types: Dict[str, int],
):
    position = chapter_id_content_position(conn, chapter_id)
    if chapter_payload.get("heading"):
        position += 1
        insert_content(
            conn,
            chapter_id,
            position_id=position,
            content_type_id=content_types["heading"],
            text=chapter_payload["heading"],
        )

    if chapter_payload.get("note"):
        position += 1
        insert_content(
            conn,
            chapter_id,
            position_id=position,
            content_type_id=content_types["heading"],
            text=chapter_payload["note"],
            pilcrow=True,
        )


def emit_verses(
    conn: sqlite3.Connection,
    chapter_id: int,
    verses: List[Dict[str, Any]],
    content_types: Dict[str, int],
):
    position = chapter_id_content_position(conn, chapter_id)
    for verse in verses:
        position += 1
        insert_content(
            conn,
            chapter_id,
            position_id=position,
            content_type_id=content_types["verse"],
            text=verse.get("text"),
            verse_number=verse.get("verse"),
            reference=verse.get("reference"),
            pilcrow=bool(verse.get("pilcrow")),
        )
        if verse.get("heading"):
            position += 1
            insert_content(
                conn,
                chapter_id,
                position_id=position,
                content_type_id=content_types["psalm_119_name"],
                text=verse["heading"],
            )
        if verse.get("subheading"):
            position += 1
            insert_content(
                conn,
                chapter_id,
                position_id=position,
                content_type_id=content_types["psalm_119_heading"],
                text=verse["subheading"],
            )

def append_signature(
    conn: sqlite3.Connection, chapter_id: int, signature: str, content_types: Dict[str, int]
):
    position = chapter_id_content_position(conn, chapter_id) + 1
    insert_content(
        conn,
        chapter_id,
        position_id=position,
        content_type_id=content_types["signature"],
        text=signature,
    )


def append_articles_of_faith_signature(
    conn: sqlite3.Connection,
    book_id: int,
    content_types: Dict[str, int],
    signature_text: str = "Joseph Smith.",
) -> None:
    row = conn.execute(
        "SELECT id FROM chapters WHERE book_id = ? ORDER BY id DESC LIMIT 1",
        (book_id,),
    ).fetchone()
    if not row:
        return
    chapter_id = row[0]
    position = chapter_id_content_position(conn, chapter_id) + 1
    insert_content(
        conn,
        chapter_id,
        position_id=position,
        content_type_id=content_types["signature"],
        text=signature_text,
    )


def ingest_facsimiles(
    conn: sqlite3.Connection,
    book_id: int,
    book_payload: Dict[str, Any],
    facsimiles: List[Dict[str, Any]],
    chapter_types: Dict[str, int],
    content_types: Dict[str, int],
):
    for idx, fac in enumerate(facsimiles, start=1):
        chapter_id = insert_chapter(
            conn,
            book_id,
            number=None,
            label=f"Facsimile {idx}",
            chapter_type_id=chapter_types["facsimile"],
        )
        chapter_stub = {
            "chapter": fac.get("number"),
            "label": fac.get("title"),
            "reference": fac.get("title"),
            "_chapter_type": "facsimile",
        }
        emit_book_metadata_content(
            conn,
            chapter_id,
            book_payload,
            chapter_stub,
            is_first_chapter=False,
            chapter_type_value="facsimile",
            content_types=content_types,
        )
        position = chapter_id_content_position(conn, chapter_id)
        
        # Read and embed the PNG file as binary data
        facsimile_path = ROOT / "facsimiles" / f"facsimile_{idx}.png"
        if facsimile_path.exists():
            position += 1
            with facsimile_path.open("rb") as img_file:
                binary_data = img_file.read()
            insert_content(
                conn,
                chapter_id,
                position_id=position,
                content_type_id=content_types["media_binary"],
                media=binary_data,
            )
        elif fac.get("image_url"):
            # Fallback to URL if PNG file doesn't exist (for backwards compatibility)
            position += 1
            insert_content(
                conn,
                chapter_id,
                position_id=position,
                content_type_id=content_types["media_binary"],
                text=fac["image_url"],
            )
        for explanation in fac.get("explanations") or []:
            position += 1
            insert_content(
                conn,
                chapter_id,
                position_id=position,
                content_type_id=content_types["paragraph"],
                text=explanation,
            )
        if fac.get("note"):
            position += 1
            insert_content(
                conn,
                chapter_id,
                position_id=position,
                content_type_id=content_types["paragraph"],
                text=fac["note"],
            )


def chapter_id_content_position(conn: sqlite3.Connection, chapter_id: int) -> int:
    cur = conn.execute(
        "SELECT MAX(position_id) FROM content WHERE chapter_id = ?", (chapter_id,)
    )
    value = cur.fetchone()[0]
    return value or 0


def append_volume_closing_text(
    conn: sqlite3.Connection,
    volume_id: int,
    text: Optional[str],
    content_types: Dict[str, int],
) -> None:
    if not text:
        return
    row = conn.execute(
        """
        SELECT c.id
        FROM chapters c
        JOIN books b ON b.id = c.book_id
        WHERE b.volume_id = ?
        ORDER BY c.id DESC
        LIMIT 1
        """,
        (volume_id,),
    ).fetchone()
    if not row:
        return
    chapter_id = row[0]
    position = chapter_id_content_position(conn, chapter_id) + 1
    insert_content(
        conn,
        chapter_id,
        position_id=position,
        content_type_id=content_types["closing_text"],
        text=text,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate scriptures.db from JSON bundles.")
    parser.add_argument(
        "-o", "--output", type=Path, default=DEFAULT_DB, help="Destination SQLite path"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    build_database(args.output)
    print(f"Built SQLite at {args.output}")


if __name__ == "__main__":
    main()