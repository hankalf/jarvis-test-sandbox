"""Demo brand so a fresh install has a brain to look at.

Fictional brand and fictional competitors — replace it with your own the moment
you have real data (`POST /api/seed?reset=1` wipes and reloads it).
"""

from __future__ import annotations

from . import db

BRAND = "Nordvik"

SEED: list[tuple[str, str, str, list[str], dict]] = [
    # identity
    ("identity", "Category", "Insulated drinkware: 24oz/40oz tumblers, 16oz mugs, 1L bottles.", ["core"], {}),
    ("identity", "Positioning", "The quiet one. Nordvik is what you carry when you don't want your bottle to shout.", ["positioning"], {}),
    ("identity", "Audience", "25-44, urban, commutes or works hybrid, already owns one loud tumbler and is tired of it.", ["audience"], {}),
    ("identity", "Tone of voice", "Dry, unhurried, specific. Never exclamation marks. Never 'obsessed'.", ["voice"], {}),
    ("identity", "Visual signature", "Matte bodies, tone-on-tone wordmark, one soft gradient ground, generous negative space.", ["visual"], {}),
    ("identity", "Non-negotiables", "Logo never sits on the product in a contrasting colour. No lifestyle models holding the product to camera.", ["rules"], {}),
    # taste
    ("taste", "Always approves", "Studio-lit single product on a soft gradient, 9:16, headline in the top third, product not centred.", ["approve"], {}),
    ("taste", "Always kills", "Confetti, glossy 3D renders, stock-looking hands, drop shadows under type, more than two typefaces.", ["kill"], {}),
    # claims
    ("claims", "Cold retention", "Substantiated: 'cold up to 24 hours' (internal lab, 2025-03, ambient 22C).", ["legal", "substantiated"], {"tested": "2025-03"}),
    ("claims", "Hot retention", "Substantiated: 'hot up to 7 hours'. Do not round up to 8.", ["legal", "substantiated"], {}),
    ("claims", "Leakproof", "NOT substantiated for the straw lid. Only the flip lid may be called leakproof.", ["legal", "banned"], {}),
    ("claims", "Dishwasher safe", "Body and lid are dishwasher safe; straw is hand-wash. Must say so if dishwasher is claimed.", ["legal"], {}),
    ("claims", "Materials", "18/8 stainless, BPA-free lid. 'Recycled steel' may only be used on the 40oz.", ["legal"], {}),
    # assets
    ("assets", "Packshots", "27 studio packshots: 4 colourways x 3 sizes, front/three-quarter, plus lid detail.", ["packshot"], {"count": 27}),
    ("assets", "Wordmark", "Tone-on-tone NORDVIK wordmark, SVG, plus the small mountain glyph.", ["logo"], {}),
    ("assets", "Palette + type", "Slate #2B3550, Lilac #C7B4E8, Sea #8BE0C0, Bone #F3EFE7. Type: Söhne + a script accent for drops.", ["brand"], {}),
    # angles
    ("angles", "New colour drop", "A colourway launch is the highest-intent moment. Lead with the colour name, not the specs.", ["launch"], {}),
    ("angles", "The quiet swap", "You already own a tumbler. This is the one you replace it with.", ["positioning"], {}),
    ("angles", "Habit, not hydration", "Sell the routine — desk, car cupholder, gym bag — not litres per day.", ["lifestyle"], {}),
    ("angles", "Fits the cupholder", "The 40oz base is 3.0in. Concrete, checkable, and the #1 comment on competitor ads.", ["spec"], {}),
    ("angles", "Gifting", "Q4 only. Bundle a colourway with the flip lid.", ["seasonal"], {"season": "Q4"}),
    ("angles", "One bottle, every routine", "Range shot: sizes lined up by height, single colour family.", ["range"], {}),
    # prompts
    ("prompts", "Hero packshot 9:16", "Use the ATTACHED image as the exact product. Vertical 9:16 on a soft airbrushed pastel gradient shifting from pale lilac at the top to soft mint at the base, fine grain. Product slightly right of centre, lit from the upper left, soft contact shadow. Reproduce the product faithfully.", ["hero", "9:16"], {"engine": "fal", "aspect": "9:16"}),
    ("prompts", "Colour drop lockup", "Stacked campaign lockup at the top: wordmark in black inside a small rounded cream pill, and beneath it the colour name in a big flowing script in deep purple, with tiny sparkle stars and water droplets.", ["lockup", "launch"], {}),
    ("prompts", "Range line-up", "Four sizes of the same colourway lined up by height on a flat mustard ground, 9:16, hard top light, long soft shadows to the right.", ["range"], {}),
    ("prompts", "Cupholder proof", "Three-quarter view of the tumbler seated in a car cupholder, interior blurred to a soft neutral, product sharp, morning light.", ["spec", "proof"], {}),
    ("prompts", "Desk ritual", "Tumbler on a pale oak desk beside a closed laptop and a notebook, window light from the left, nothing else in frame.", ["lifestyle"], {}),
    ("prompts", "Gym bag", "Tumbler half out of an open canvas gym bag on a concrete floor, overhead light, cool shadows.", ["lifestyle"], {}),
    ("prompts", "Lid detail macro", "Macro of the flip lid closing, shallow depth of field, matte texture readable, neutral grey ground.", ["detail"], {}),
    ("prompts", "Bone colourway", "Same hero framing, bone body against a warm sand gradient, tone-on-tone wordmark.", ["hero", "colour"], {}),
    ("prompts", "Sea colourway", "Same hero framing, sea-green body against a pale mint-to-cream gradient.", ["hero", "colour"], {}),
    ("prompts", "Gift bundle", "Two tumblers and a spare flip lid arranged on a cream ground with a thin ribbon, top-down, Q4 warmth.", ["seasonal"], {}),
    ("prompts", "Winter hot-drink", "Steam rising from an open mug on a windowsill, frost on the glass behind, warm interior light.", ["seasonal"], {}),
    ("prompts", "Motion: colour reveal", "Slow 180-degree turntable of the tumbler as the background gradient shifts from lilac to mint. 5 seconds, no cuts.", ["video"], {"kind": "video"}),
    # intel
    ("intel", "Purchase trigger", "Most buyers replace, not start. The trigger is a lid that leaked or a colour they got bored of.", ["audience"], {}),
    ("intel", "Top objection", "'Does it fit my cupholder' outranks price in comments by roughly 3 to 1.", ["objection"], {}),
    ("intel", "Seasonality", "Two peaks: early January (routine reset) and late November (gifting).", ["seasonality"], {}),
    ("intel", "Price sensitivity", "Flat between $35-45; drops off sharply above $50 without a bundle.", ["pricing"], {}),
    ("intel", "Channel behaviour", "9:16 static outperforms video on cost per click; video wins on retention for colour drops.", ["channel"], {}),
    # adspy
    ("adspy", "Yeti-style 42oz straw mug, 30% off", "Competitor A. Clean white ground, three colourways in a row, discount badge top-left. Still running.", ["competitor"], {"days_live": 281, "format": "static"}),
    ("adspy", "Owala-style 'Treat your coffee right'", "Competitor B. Warm cream gradient, ceramic-interior callout, two products angled. Still running.", ["competitor"], {"days_live": 139, "format": "static"}),
    ("adspy", "Stanley-style colour drop script", "Competitor C. Script colour name over a pastel gradient, sparkle accents. Reappears every drop.", ["competitor"], {"days_live": 94, "format": "static"}),
    ("adspy", "Range line-up on mustard", "Competitor D. Four sizes by height on a saturated flat ground. Longest-running range ad in the set.", ["competitor"], {"days_live": 212, "format": "static"}),
    # offers
    ("offers", "Launch bundle", "Any 40oz + spare flip lid for $49 through the colour drop window.", ["bundle"], {"price": 49}),
    ("offers", "Free shipping", "Free shipping over $40, always on. Do not present it as a limited offer.", ["shipping"], {}),
    # performance
    ("performance", "Hero packshot 9:16", "Best cost per click in the account. CTR 2.4%, ROAS 3.1 over $6.4k spend.", ["winner"], {"ctr": 2.4, "roas": 3.1, "spend": 6400}),
    ("performance", "Cupholder proof", "Highest hook rate at 41%. Converts worse than hero but feeds retargeting.", ["winner"], {"hook_rate": 41}),
    ("performance", "Gym bag", "Underperformed: CTR 0.8%. Product too small in frame.", ["loser"], {"ctr": 0.8}),
    ("performance", "Colour drop lockup", "Peaks in the first 72 hours of a drop then decays fast. Rotate out by day 5.", ["decay"], {}),
    ("performance", "Motion: colour reveal", "ROAS 2.2 but best thumb-stop of anything shipped. Use as a drop opener.", ["video"], {"roas": 2.2}),
]

LINKS: list[tuple[str, str, str]] = [
    ("Cupholder proof", "Top objection", "derived_from"),
    ("Fits the cupholder", "Top objection", "derived_from"),
    ("New colour drop", "Stanley-style colour drop script", "derived_from"),
    ("One bottle, every routine", "Range line-up on mustard", "derived_from"),
    ("Hero packshot 9:16", "Always approves", "supports"),
]


def load(reset: bool = False) -> dict[str, int]:
    if reset:
        db.conn().execute("DELETE FROM node")
        db.conn().execute("DELETE FROM edge")
        db.conn().commit()

    db.kv_set("brand_name", BRAND)
    by_title: dict[str, str] = {}
    for category, title, body, tags, meta in SEED:
        node = db.create_node(category, title, body, tags, meta, source="seed")
        by_title.setdefault(title, node["id"])

    for src, dst, kind in LINKS:
        if src in by_title and dst in by_title:
            db.create_edge(by_title[src], by_title[dst], kind)

    return db.category_counts()
