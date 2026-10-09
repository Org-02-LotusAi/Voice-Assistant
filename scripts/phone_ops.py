"""LiveKit helpers for the phone dogfood runbook (docs/PHONE_DOGFOOD.md). Uses LIVEKIT_URL/KEY/SECRET from env / .env.

  uv run python scripts/phone_ops.py participants ava-001
  uv run python scripts/phone_ops.py remove ava-001 phone-3210-ab12
  uv run python scripts/phone_ops.py dispatch-direct ava-001 --trunk-id ST_inbound   # what Service 014 would run

`dispatch-direct` is NOT something Service 015 does in production: Service 014 owns inbound SIP trunks and dispatch rules.
It is here so the runbook's inbound step is an exact, copyable call (needs the livekit/sip service: compose `sip` profile).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from livekit import api  # noqa: E402

from service015.config import get_settings  # noqa: E402

KINDS = {0: "STANDARD", 1: "INGRESS", 2: "EGRESS", 3: "SIP", 4: "AGENT"}


async def run(args: argparse.Namespace) -> int:
    s = get_settings()
    lk = api.LiveKitAPI(s.livekit_url, s.livekit_api_key, s.livekit_api_secret)
    try:
        if args.cmd == "participants":
            res = await lk.room.list_participants(api.ListParticipantsRequest(room=args.room))
            for p in res.participants:
                print(f"{p.identity}\tkind={KINDS.get(p.kind, p.kind)}\tstate={api.ParticipantInfo.State.Name(p.state)}\tname={p.name}")
            if not res.participants:
                print(f"(no participants in room {args.room})")
        elif args.cmd == "remove":
            await lk.room.remove_participant(api.RoomParticipantIdentity(room=args.room, identity=args.identity))
            print(f"removed {args.identity} from {args.room}")
        elif args.cmd == "dispatch-direct":
            req = api.CreateSIPDispatchRuleRequest(
                rule=api.SIPDispatchRule(dispatch_rule_direct=api.SIPDispatchRuleDirect(room_name=args.room)),
                trunk_ids=[args.trunk_id],
                name=f"{args.room} inbound",
            )
            info = await lk.sip.create_sip_dispatch_rule(req)
            print(f"created dispatch rule {info.sip_dispatch_rule_id} -> room {args.room}")
    finally:
        await lk.aclose()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("participants").add_argument("room")
    r = sub.add_parser("remove")
    r.add_argument("room")
    r.add_argument("identity")
    d = sub.add_parser("dispatch-direct")
    d.add_argument("room")
    d.add_argument("--trunk-id", required=True, help="inbound SIP trunk id (ST_...) the rule applies to")
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
