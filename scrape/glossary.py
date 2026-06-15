"""Static translation glossary for LLM extraction.

Event names decompose as roughly ``[franchise/group/unit] + [descriptor] + [venue/leg]``.
The model handles numbers, "LIVE TOUR", and Hepburn venue romanization well on its
own; what it gets *inconsistent* is the proper nouns (it will invent "Renoku" instead
of the official "Hasunosora"). So the highest-leverage static asset is a pinned list
of official English names, plus the standard ticket-round vocabulary (which is highly
conventional and translates 1:1).

These dicts are injected into the extractor's prompt via :func:`prompt_block`, and
are also importable by the site/editor if we want to surface official names there.

Entries marked ``# review`` are lower-confidence official spellings worth a human
double-check; everything else is the spelling used in official English materials.
"""

from __future__ import annotations

# --- Franchises (the JP strings here mirror scrape.llfans.SERIES) -> official EN ---
SERIES_EN: dict[str, str] = {
    "ラブライブ！": "Love Live!",
    "ラブライブ！サンシャイン!!": "Love Live! Sunshine!!",
    "虹ヶ咲学園スクールアイドル同好会": "Nijigasaki High School Idol Club",
    "ラブライブ！虹ヶ咲学園スクールアイドル同好会": "Love Live! Nijigasaki High School Idol Club",
    "ラブライブ！スーパースター!!": "Love Live! Superstar!!",
    "スクールアイドルミュージカル": "School Idol Musical",
    "蓮ノ空女学院スクールアイドルクラブ": "Hasunosora Girls' High School Idol Club",
    "蓮ノ空女学院": "Hasunosora Girls' High School",
    "幻日のヨハネ -SUNSHINE in the MIRROR-": "Yohane the Parhelion -SUNSHINE in the MIRROR-",
    "幻日のヨハネ": "Yohane the Parhelion",
    "イキヅライブ！ LOVELIVE! BLUEBIRD": "Love Live! Bluebird",  # review
    "ラブライブ！蓮ノ空女学院スクールアイドルクラブ": "Love Live! Hasunosora Girls' High School Idol Club",
}

# --- Groups, sub-units, duos (JP or stylized -> official EN). Most official unit
#     names are already Latin-script; we still list them so the model keeps the exact
#     stylization (CYaRon!, QU4RTZ, 5yncri5e!) instead of normalizing it. ---
GROUPS_EN: dict[str, str] = {
    # μ's (Love Live!)
    "μ's": "μ's",
    "BiBi": "BiBi",
    "lily white": "lily white",
    "Printemps": "Printemps",
    "A-RISE": "A-RISE",
    # Aqours (Sunshine!!)
    "Aqours": "Aqours",
    "Guilty Kiss": "Guilty Kiss",
    "CYaRon!": "CYaRon!",
    "AZALEA": "AZALEA",
    "Saint Snow": "Saint Snow",
    "Saint Aqours Snow": "Saint Aqours Snow",
    # Nijigasaki
    "A・ZU・NA": "A・ZU・NA",
    "QU4RTZ": "QU4RTZ",
    "DiverDiva": "DiverDiva",
    "R3BIRTH": "R3BIRTH",
    # Liella! (Superstar!!)
    "Liella!": "Liella!",
    "Sunny Passion": "Sunny Passion",
    "CatChu!": "CatChu!",
    "KALEIDOSCORE": "KALEIDOSCORE",
    "5yncri5e!": "5yncri5e!",
    # Hasunosora
    "スリーズブーケ": "Cerise Bouquet",
    "DOLLCHESTRA": "DOLLCHESTRA",
    "みらくらぱーく！": "Mira-Cra Park!",  # review
    "みらくらぱーく": "Mira-Cra Park!",  # review
    "Edel Note": "Edel Note",  # review
    # Yohane the Parhelion
    "幻日のヨハネ": "Yohane the Parhelion",
    # Project Sekai (プロセカ) — units that appear in tracked seiyuu events
    "プロジェクトセカイ": "Project Sekai",
    "プロセカ": "Project Sekai",
    "25時、ナイトコードで。": "Nightcord at 25:00",
    "ニーゴ": "Nightcord at 25:00",
    "Leo/need": "Leo/need",
    "レオニード": "Leo/need",
    "MORE MORE JUMP！": "MORE MORE JUMP!",
    "ビビッドバッドスクワッド": "Vivid BAD SQUAD",
    "ワンダーランズ×ショウタイム": "Wonderlands×Showtime",
}

# Combined proper-noun map (longest keys first so multi-word names win during the
# model's substitution; the prompt presents these as "use this English exactly").
NAMES_EN: dict[str, str] = {**SERIES_EN, **GROUPS_EN}

# --- Ticket-round vocabulary (highly conventional, 1:1) -> EN ---
TICKET_TERMS: dict[str, str] = {
    "抽選": "lottery",
    "先行": "advance sale",
    "先行抽選": "advance lottery",
    "最速先行": "earliest presale",
    "先着": "first-come-first-served",
    "先着販売": "first-come-first-served sale",
    "一般販売": "general sale",
    "一般発売": "general sale",
    "1次先行": "1st-round presale",
    "2次先行": "2nd-round presale",
    "3次先行": "3rd-round presale",
    "二次先行": "2nd-round presale",
    "ファンクラブ先行": "fanclub presale",
    "FC先行": "fanclub presale",
    "オフィシャル先行": "official presale",
    "プレイガイド先行": "playguide presale",
    "受付": "application",
    "受付期間": "application period",
    "申込": "application",
    "申込期間": "application period",
    "当落発表": "results announcement",
    "抽選結果発表": "lottery results announcement",
    "入金": "payment",
    "入金期間": "payment period",
    "支払": "payment",
}

# --- Common event-name descriptors -> EN (numbers/symbols are kept verbatim) ---
EVENT_TERMS: dict[str, str] = {
    "ライブ": "Live",
    "ツアー": "Tour",
    "単独ライブ": "Solo Live",
    "ユニットライブ": "Unit Live",
    "ファンミーティング": "Fan Meeting",
    "記念": "Anniversary",
    "周年": "Anniversary",  # e.g. 7周年 -> 7th Anniversary
    "公演": "Performance",
    "昼公演": "Matinee",
    "夜公演": "Evening Performance",
    "スペシャル": "Special",
    "ツアーファイナル": "Tour Final",
}


# --- kind classification: map common Japanese event-name cues -> a KINDS bucket.
#     Surveyed from the eventernote calendar. The bucket set itself lives in
#     schema.models.KINDS (the single source of truth); this just guides the LLM. ---
KIND_CUES: dict[str, str] = {
    "ライブ / ワンマンライブ / Live / シンフォニー / 単独公演": "concert",
    "全国ツアー / LIVE TOUR / ホールツアー": "tour",
    "フェス / FES / 対バン (multi-act)": "festival",
    "発売記念 / リリースイベント / リリイベ": "release",
    "サイン会 / 握手会 / ミート＆グリート / お見送り会 / チェキ会": "meet-greet",
    "ファンミーティング / FAN MEETING / サロン": "fan-meeting",
    "トークイベント / トークショー / 公開録音・公開収録 / 朗読会 / お話し会 / "
    "生誕祭・バースデーイベント / 単独・ソロの声優イベント": "talk",
    "舞台 / ミュージカル / 朗読劇": "stage",
    "上映会 / プレミア / 先行上映 / 舞台挨拶": "screening",
    "物販 (goods only)": "goods",
    "配信 / オンライン / 生配信": "stream",
}


def _fmt(d: dict[str, str]) -> str:
    return "\n".join(f"  {jp} = {en}" for jp, en in d.items())


def kind_block(kinds) -> str:
    """Render the kind-classification rule for the prompt. ``kinds`` is schema.KINDS."""
    return (
        f"- kind MUST be exactly one of: {', '.join(kinds)}. Pick the single closest "
        "bucket for the event's primary purpose (use 'other' only if none fit):\n"
        f"{_fmt(KIND_CUES)}"
    )


def prompt_block() -> str:
    """Render the glossary + translation rules for the extractor's system prompt."""
    return (
        "TRANSLATION — also produce English renderings:\n"
        "- name_en: a natural English version of the event name. Keep text already in "
        "English/Latin, all numbers, and symbols VERBATIM. Replace Japanese proper "
        "nouns with the official English in the PROPER NOUNS list below; Hepburn-"
        "romanize any other Japanese proper noun (venue, person). Translate descriptive "
        "words via COMMON TERMS. Never add information not present in the source name.\n"
        "- EVERY round MUST have name_en (this is required, not optional): an English "
        "version of the round's name field, translating each Japanese term via TICKET "
        "TERMS. Translate the WHOLE name, not just one word, and put it in name_en — do "
        "NOT put the translation in the type field. Examples: '1次先行抽選' -> "
        "'1st-round advance lottery'; '一般発売' -> 'general sale'; '応募シリアル配布・"
        "申込期間' -> 'entry serial distribution / application period'. Keep digits as written.\n"
        "PROPER NOUNS (use this English exactly; do not re-romanize):\n"
        f"{_fmt(NAMES_EN)}\n"
        "TICKET TERMS:\n"
        f"{_fmt(TICKET_TERMS)}\n"
        "COMMON TERMS:\n"
        f"{_fmt(EVENT_TERMS)}"
    )
