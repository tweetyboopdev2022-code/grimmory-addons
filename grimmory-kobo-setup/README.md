# Grimmory Kobo setup

A "Set up my Kobo" button for [Grimmory](https://github.com/grimmory-tools/grimmory). Someone who
joins your library plugs their Kobo into a computer, clicks the button, and their Kobo is connected
to *their own* account through Grimmory's built-in Kobo sync. No terminal, no hand-editing
`Kobo eReader.conf`, no copying sync URLs around.

It only uses what Grimmory already has: the `/api/v1/kobo-settings` endpoint that hands out each
user's sync key, and the `/api/kobo/<key>` sync address. It installs nothing on the Kobo besides
that one settings line.

## What it does

| Step | Detail |
|---|---|
| Sync | Turns on Kobo sync for the signed-in account (Grimmory makes the sync key) and writes `api_endpoint` under `[OneStoreServices]` in `.kobo/Kobo/Kobo eReader.conf`, changing that one line and keeping every other byte (CRLF files, missing key and duplicate keys are handled; it refuses to guess). Optional: "send new books to my Kobo automatically", which is Grimmory's auto-add to the Kobo shelf. |
| Tidy | Deletes macOS `._` files that a Mac leaves on the Kobo (only real AppleDouble files, checked by their first bytes). The Kobo can otherwise list them as broken books. |
| Backup and undo | Before changing anything, a copy of every file it will change or delete goes to `.adds/grimmory-setup/backups/<time>/` on the Kobo with a `manifest.json`. The page lists these and **Undo** puts them back. The page can also save a zip of the Kobo's settings, `KoboReader.sqlite` and `.adds` folder to the computer. |

It also shows where the Kobo syncs from now (Kobo's store, this Grimmory, or another server, with the
key masked) and its firmware.

## Three ways in

- **The page**, at `/kobo-setup/`, reached from a "Set up my Kobo" box at the top of Grimmory's Kobo
  sync settings (Settings → Devices). Grimmory shows that section only to admins and to accounts with
  the Sync Kobo permission, so the box follows the same rule. It works in Chrome or Edge on a
  computer, which can edit files on a plugged-in drive (the File System Access API). The person is
  already signed in, picks the KOBOeReader drive, and clicks Allow.
- **Mac double-click file** (`mac/Connect my Kobo.command`, offered on the page as a zip). Uses only
  what every Mac has: bash, curl, awk, osascript. Asks for the library username and password.
  macOS blocks a downloaded script the first time: System Settings → Privacy & Security → Open Anyway.
- **Windows double-click file** (`windows/Connect my Kobo.cmd`). A batch file that runs itself as
  PowerShell 5.1. SmartScreen may ask first: More info → Run anyway.

All three write the same backups, so the page's Undo works on changes made by the files too.

Each person's account needs the **Sync Kobo** permission (Settings → Users). Without it the page and
the files say so and change nothing. The Kobo must have finished its first start-up.

## Install (on the machine running Grimmory)

Assuming your compose folder holds Grimmory's `docker-compose.yml` and an nginx proxy (service
`proxy`) with its `nginx.conf`:

1. Copy this folder there as `grimmory-kobo-setup/`.
2. `python3 grimmory-kobo-setup/build.py --server https://your-library.example.com` makes the two
   double-click downloads with your library's public address in them. Run it again if the address
   changes.
3. Add the proxy volume from `docker-compose.override.yml` to the override file next to Grimmory's
   compose file (under `proxy:` → `volumes:`).
4. `python3 grimmory-kobo-setup/install-nginx.py ./nginx.conf` (safe to run twice, keeps
   `nginx.conf.kobo-setup.bak`). It adds the `/kobo-setup/` location and puts `link.js` into
   Grimmory's pages before `</head>`.
5. `docker compose up -d --force-recreate proxy` (so the proxy picks up the new folder).
6. Hard-refresh Grimmory: Settings → Devices shows a "Set up my Kobo" box above the Kobo sync settings.

The files under `/kobo-setup/` are public (no sign-in) because they hold nothing private. Everything
personal goes through Grimmory's own API with the person's own login.

## After a Kobo software update

A Kobo firmware update can clear `api_endpoint` or reset it to Kobo's own store. The page then shows
"not set" or "Kobo's own store" under "Syncs with"; running the setup again reconnects it.

## Tests

```sh
python3 build.py --server https://library.example
node --test tests/*.test.js
```

`tests/` builds a fake Kobo on disk and a fake Grimmory, then runs the Mac file (with a stand-in for
`osascript`), the Windows file (needs `pwsh`; skipped without it) and the page in Chromium (needs
Playwright; the Kobo is a folder in the browser's private file system, which has the same file API as
a real drive). The three settings-file editors are checked against each other on the same cases.

Not covered by the tests: a real Kobo, Windows PowerShell 5.1 itself (the tests use PowerShell 7), the
`.cmd` wrapper, real macOS, and Chrome's folder picker on a real USB drive.

## Limits

- The box in Settings → Devices finds Grimmory's `app-kobo-sync-setting-component`; a Grimmory UI
  change can hide it until `link.js` is updated. The page itself keeps working at `/kobo-setup/`.
- English only.
- Written with AI assistance (Claude) and reviewed by a person; see Grimmory's
  [AI policy](https://github.com/grimmory-tools/grimmory/blob/develop/AI_POLICY.md).

## Licence

AGPL-3.0, the same as Grimmory.
