"""Record voice_authorizations rows for the two seeded synthetic voices ("Ava Warm", "James Calm"). Idempotent.

Provenance (subject, origin, why no consent is needed) is read from assets/voices/PROVENANCE.md, so the
authorization rows say exactly what that file says. Run after scripts/seed_voicebox_profiles.py:

  uv run python scripts/authorize_seed_voices.py [--db ./data/service015.db] [--voicebox-url http://127.0.0.1:17493]

Defaults come from the same env / .env settings as the API (SERVICE015_DB_PATH, VOICEBOX_URL). Re-running adds no rows.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from service015.config import get_settings  # noqa: E402
from service015.db import connect, migrate  # noqa: E402
from service015.errors import DomainError  # noqa: E402
from service015.voice.authz import record_authorization  # noqa: E402
from service015.voice.voicebox import VoiceboxClient  # noqa: E402

PROVENANCE = ROOT / "assets" / "voices" / "PROVENANCE.md"
SOURCE = "seed_provenance:assets/voices/PROVENANCE.md"
SEEDS = {"ava_warm": "Ava Warm", "james_calm": "James Calm"}  # wav stem -> Voicebox profile name


def parse_provenance(text: str) -> dict[str, dict[str, str]]:
    """{stem: {voice, origin, authorization}} from the first table of PROVENANCE.md."""
    rows: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            continue
        m = re.match(r"`([a-z0-9_]+)\.wav`", cells[0])
        if m:
            rows[m.group(1)] = {"voice": cells[1], "origin": cells[2], "authorization": cells[3]}
    return rows


async def run(db_path: str, voicebox_url: str) -> int:
    provenance = parse_provenance(PROVENANCE.read_text(encoding="utf-8"))
    missing = [stem for stem in SEEDS if stem not in provenance]
    if missing:
        print(f"PROVENANCE.md has no row for: {', '.join(missing)}", file=sys.stderr)
        return 1
    voicebox = VoiceboxClient(voicebox_url, 30.0)
    try:
        profiles = {p["name"]: p["id"] for p in await voicebox.list_profiles() if p.get("voice_type") == "cloned"}
    except DomainError as exc:
        print(f"cannot list Voicebox profiles: {exc.message}", file=sys.stderr)
        return 1
    conn = connect(db_path)
    try:
        migrate(conn)
        for stem, name in SEEDS.items():
            profile_id = profiles.get(name)
            if profile_id is None:
                print(f"{name}: no cloned profile in Voicebox; run scripts/seed_voicebox_profiles.py first", file=sys.stderr)
                return 1
            if conn.execute(
                "SELECT 1 FROM voice_authorizations WHERE voice_profile_id = ? AND source = ?", (profile_id, SOURCE)
            ).fetchone():
                print(f"{name} ({profile_id}): already authorized")
                continue
            row = provenance[stem]
            record_authorization(
                conn,
                voice_profile_id=profile_id,
                authorized_by="operator (scripts/authorize_seed_voices.py)",
                subject_name=row["voice"],
                consent_statement=f"{row['authorization']}. Origin: {row['origin']}.",
                source=SOURCE,
            )
            print(f"{name} ({profile_id}): authorization recorded")
    finally:
        conn.close()
    return 0


def main() -> None:
    settings = get_settings()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=settings.db_path)
    ap.add_argument("--voicebox-url", default=settings.voicebox_url)
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args.db, args.voicebox_url)))


if __name__ == "__main__":
    main()
