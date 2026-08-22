#!/usr/bin/env python3
"""Pi-side half of the automation: wake the GPU box when there is work.

The Pi cannot synthesise -- F5-TTS needs CUDA, and CPU inference on ARM is
hours per clip -- so its job is deciding *when* the machine that can should be
awake. Nothing more. The actual run is `run_batch.py --watch` on that machine,
which picks the work up once it boots.

Deliberately one-directional: this sends a magic packet and stops. It does not
SSH in, does not drive the run, and does not need credentials for the PC.
Fewer moving parts, and nothing to leave half-done if the Pi reboots
mid-cycle.

    wake_when_work.py --mac AA:BB:CC:DD:EE:FF
    wake_when_work.py --mac AA:BB:CC:DD:EE:FF --watch 30

Environment:
    SPEAKSTONE_KEY   export API key, required
    SPEAKSTONE_URL   defaults to https://speakstone.beanw.co.uk
    GPU_HOST_MAC     MAC address, if you would rather not pass --mac

Run it from cron or a systemd timer on the Pi, or leave --watch running.
"""

import argparse
import json
import os
import re
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

DEFAULT_SITE = os.environ.get("SPEAKSTONE_URL", "https://speakstone.beanw.co.uk")


def log(message):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}", flush=True)


def pending_count(site, key):
    """How many unvoiced passages Speakstone is holding.

    Asks for limit=1 and counts the payload rather than pulling the whole
    batch: the Pi only needs to know whether the number is zero, and dragging
    a few MB of text over the wire every poll to learn that would be wasteful.
    Note this deliberately does not pass pull=1 -- polling must never consume
    the batch the GPU box is about to ask for.
    """
    url = f"{site}/api/export?all=1&format=json&limit=1"
    request = urllib.request.Request(url, headers={"X-Api-Key": key})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = "check SPEAKSTONE_KEY" if error.code == 401 else error.reason
        log(f"Speakstone returned HTTP {error.code} ({detail})")
        return None
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as error:
        # A transient network blip must not kill a long-running watcher.
        log(f"Could not reach Speakstone: {error}")
        return None
    return len(data.get("passages", []))


def is_awake(host, port=3389, timeout=3):
    """Cheap liveness check so we do not wake a machine that is already up."""
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wake(mac, broadcast="255.255.255.255", port=9):
    """Send a Wake-on-LAN magic packet: 6x 0xFF then the MAC 16 times."""
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", mac)
    if len(cleaned) != 12:
        raise SystemExit(f"Not a MAC address: {mac!r}")
    payload = b"\xff" * 6 + bytes.fromhex(cleaned) * 16

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(payload, (broadcast, port))
    log(f"Sent wake packet to {mac}")


def cycle(args, key):
    count = pending_count(args.site, key)
    if count is None:
        return
    if count == 0:
        log("Nothing waiting.")
        return

    log(f"{count}+ passage(s) waiting.")
    if is_awake(args.host):
        log(f"{args.host} is already up -- its watcher will pick the work up.")
        return
    wake(args.mac, args.broadcast, args.port)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mac", default=os.environ.get("GPU_HOST_MAC"),
                        help="MAC address of the machine with the GPU")
    parser.add_argument("--host", default=os.environ.get("GPU_HOST"),
                        help="hostname or IP, to skip waking a machine already up")
    parser.add_argument("--site", default=DEFAULT_SITE)
    parser.add_argument("--broadcast", default="255.255.255.255")
    parser.add_argument("--port", type=int, default=9)
    parser.add_argument("--watch", type=int, metavar="MINUTES",
                        help="keep checking every MINUTES instead of exiting")
    args = parser.parse_args()

    if not args.mac:
        raise SystemExit("No MAC address. Pass --mac or set GPU_HOST_MAC.")
    key = os.environ.get("SPEAKSTONE_KEY")
    if not key:
        raise SystemExit("SPEAKSTONE_KEY is not set.")

    if not args.watch:
        cycle(args, key)
        return 0

    log(f"Watching {args.site} every {args.watch} minute(s). Ctrl-C to stop.")
    while True:
        cycle(args, key)
        time.sleep(args.watch * 60)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
