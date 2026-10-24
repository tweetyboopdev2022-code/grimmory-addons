#!/usr/bin/env python3
"""Make the generated files the Kobo setup page serves. Run after every update.

  python3 build.py --server https://your-library.example.com

Writes, inside public/:
  downloads/Connect-my-Kobo-Mac.zip  the Mac double-click file, zipped so it stays runnable
  downloads/Connect-my-Kobo.cmd      the Windows double-click file, with Windows line endings
"""
import argparse
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PUBLIC = HERE / "public"
DEFAULT_SERVER = "https://library.example.com"   # placeholder in the double-click files


def with_server(text: str, server: str) -> str:
    if server != DEFAULT_SERVER:
        text = text.replace(DEFAULT_SERVER, server.rstrip("/"))
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", required=True, help="the library's public address, as people type it in a browser")
    ap.add_argument("--out", default=str(PUBLIC), help="where to write (default: public/)")
    args = ap.parse_args(argv)
    out = Path(args.out)

    (out / "downloads").mkdir(parents=True, exist_ok=True)
    mac = with_server((HERE / "mac/Connect my Kobo.command").read_text(), args.server)
    info = zipfile.ZipInfo("Connect my Kobo.command", date_time=(2026, 1, 1, 0, 0, 0))
    info.create_system = 3                      # unix, so the permissions below are kept
    info.external_attr = (0o100755 << 16)       # a runnable file
    with zipfile.ZipFile(out / "downloads/Connect-my-Kobo-Mac.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(info, mac)

    win = with_server((HERE / "windows/Connect my Kobo.cmd").read_text(), args.server)
    win = win.replace("\r\n", "\n").replace("\n", "\r\n")
    (out / "downloads/Connect-my-Kobo.cmd").write_bytes(win.encode("utf-8"))
    print("built downloads/ for %s" % args.server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
