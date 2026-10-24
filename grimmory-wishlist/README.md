# Grimmory Wishlist

A shared "books we'd like" list for a [Grimmory](https://github.com/grimmory-tools/grimmory) library,
added as a **Wishlist** entry in Grimmory's sidebar. Anyone signed in can ask for a book or a whole
series; the admin sees every request in one place, and the list notices by itself when a requested
book reaches the library.

It runs as a small sidecar container next to Grimmory (Python standard library plus SQLite, and one
package for Web Push). It never touches Grimmory's code or database: nginx injects one script and one
stylesheet into Grimmory's pages, and everything else goes through Grimmory's own API.

## What it does

- **Ask for a book or a series.** As you type, matching books and whole series come up from Hardcover,
  Google Books and Open Library (any mix you switch on). Pick one, or ignore the suggestions and add
  what you typed with an optional note.
- **Already have it?** Suggestions are marked "In library", "On the list" or "Similar in library", and
  adding by hand runs the same check (titles compared without series tags, ISBNs, near-identical
  spellings). "Add anyway" is offered for near misses.
- **"Now in the library."** When a wanted book shows up in Grimmory, its entry is marked by itself
  (only when title and author agree), and the person who asked sees a "Got it" banner next time they
  open Grimmory. The admin can also tick entries off by hand. Arrived entries clear themselves after
  30 days.
- **Who asked.** Everyone sees the whole list, but only the admin and the requester see who asked
  for an entry (removed on the server, never sent to other people). The admin can filter by person.
  Only admins can remove entries.
- **Missing from your series (admin).** For each series in the library, Hardcover's list of its books
  is compared with what you own. The gaps are listed with **Add** and **Not interested**, upcoming
  books show their date, and series you own completely are listed as **Complete**.
- **Release dates.** Upcoming books are marked "Out <date>", and the admin is told when one comes out.
- **Notifications (optional).** Browser notifications through standard Web Push: the admin hears about
  new requests, and each person hears when *their* request arrives. Email to the requester (SMTP) and
  an ntfy topic are also supported. All off until configured.
- **Public library check (optional).** Watches an OverDrive (Libby) library's public catalogue and
  tells the admin when a wanted e-book arrives there or becomes free to borrow.
- Styling uses Grimmory's own theme variables, so light/dark mode and the accent colour follow.

## How it knows who you are

The page forwards your existing Grimmory login token to the sidecar, which asks Grimmory
(`/api/v1/users/me`) who it belongs to and whether they are an admin. There are no separate accounts
or passwords, and a user removed from Grimmory loses access immediately.

Accounts that can see only some libraries cannot see books in the others, so the duplicate check also
uses the admin's last view of the whole library (refreshed whenever the admin opens Grimmory, trusted
for 24 hours). Nobody is shown books they cannot see, but a search can say "In library" for a book
that sits in a library the person has no access to.

## Install

Assuming your compose folder holds Grimmory's `docker-compose.yml` and an nginx proxy with its
`nginx.conf` (the proxy service is called `proxy` below):

1. Copy this folder there as `grimmory-wishlist/`, and copy `docker-compose.override.yml` up next to
   your compose file (merge it by hand if you already have an override).
2. `cp grimmory-wishlist/.env.example grimmory-wishlist/.env` and fill in what you want. Nothing is
   required; `HARDCOVER_TOKEN` (free, from https://hardcover.app/account/api) gives the best series
   suggestions.
3. If your Grimmory service is not called `grimmory` on port 6060, set
   `WISHLIST_GRIMMORY_URL=http://<service>:<port>` in the compose folder's `.env`.
4. `python3 grimmory-wishlist/install-nginx.py ./nginx.conf` (safe to run twice, keeps a `.wishlist.bak`).
   If your `nginx.conf` has no plain `location / {`, add the two `location` blocks and the `sub_filter`
   lines from `install-nginx.py` by hand.
5. `docker compose up -d --build wishlist && docker compose restart proxy`
6. Hard-refresh Grimmory. "Wishlist" appears in the sidebar under Notebook.

## Settings worth knowing

All in `.env`; see `.env.example` for every option.

| Setting | What it does |
|---|---|
| `SEARCH_SOURCES` | `hardcover,google,openlibrary` (any subset). Each source gets 6 seconds; if one is down the others still answer. |
| `HARDCOVER_TOKEN` | Hardcover search and the "Missing from your series" section. Tokens expire; make a new one if Hardcover results vanish. |
| `GOOGLE_BOOKS_KEY` | Only if Google Books starts answering "too many requests". |
| `GRIMMORY_WATCH_USER` / `GRIMMORY_WATCH_PASSWORD` | A normal (non-admin) Grimmory account with access to every library. With it, arrivals are noticed every `LIBRARY_CHECK_MIN` minutes even when nobody has Grimmory open. |
| `PUBLIC_URL` | Your library's address, so clicking a notification opens the wishlist. |
| `NOTIFY_OWNER` | The one user who gets admin notifications (default: every admin). |
| `NOTIFY_ON_WISHLIST` | Tell the owner about new requests (on by default once notifications are switched on). |
| `NOTIFY_ON_LOGIN`, `NOTIFY_ON_VISIT` | Tell the owner when someone signs in or opens Grimmory. Off by default. Detected in the browser, so apps (Kobo, OPDS) never trigger it. |
| `SMTP_*` | Email the requester when their book arrives. Each address comes from their Grimmory account. |
| `LIBRARY_URL`, `LIBRARY_NAME` | An OverDrive library to watch, e.g. `https://yourlibrary.overdrive.com`. Off when empty. Reads only the public search page, one book at a time with a pause. |
| `NOTIFY_KEY` | Lets your own scripts send a notification with `POST /wishlist-api/push/send`, header `X-Notify-Key`, body `{"title": "...", "message": "..."}`. Off when empty. |

## Browser notifications

Open the Wishlist, find **Notifications**, click **Turn on** and allow. Do it once in each browser
that should get them; **Send a test** and **Turn off here** are in the same place. They arrive while
the browser is running, even with Grimmory closed.

The page registers a small service worker (`push-sw.js`, scoped to `/wishlist-static/` so it never
touches Grimmory's own pages) and hands the sidecar the browser's push subscription. Messages are
encrypted end to end to that browser and signed with a key the sidecar makes on first start
(`vapid.pem` in the `wishlist-data` volume; keep it, or every browser must turn notifications on
again). Safari on iPhone/iPad allows this only for a site added to the Home Screen.

## Search sources

- **Hardcover:** best for series and popular fiction; needs a free token.
- **Google Books:** widest catalogue; works without a key.
- **Open Library:** huge open catalogue, good for older books; no key.

Answers are merged (series first, then one entry per book, taking turns between sources) and cached
for 10 minutes. Each person is limited to 40 searches a minute. Covers are only kept from
hardcover.app, books.google.com and covers.openlibrary.org. Goodreads (no public API) and Amazon
(affiliates only) are not options.

## Uninstall

Restore `nginx.conf.wishlist.bak` (or delete the blocks marked `# grimmory-wishlist`),
`docker compose rm -sf wishlist`, restart the proxy. The list lives in the `wishlist-data` volume.

## Development

    python3 -m venv .venv && .venv/bin/pip install pytest ruff cryptography
    .venv/bin/pytest -q && .venv/bin/ruff check .

The tests start fake Grimmory, Hardcover, Google Books, Open Library, OverDrive, ntfy, SMTP and push
servers on localhost; nothing reaches the internet.

## Limits

- It depends on Grimmory's page layout for the sidebar entry ("Wishlist" goes under Notebook). A
  Grimmory UI change can move or hide it until the script is updated.
- English only.
- Written with AI assistance (Claude) and reviewed by a person; see Grimmory's
  [AI policy](https://github.com/grimmory-tools/grimmory/blob/develop/AI_POLICY.md).

## Licence

AGPL-3.0, the same as Grimmory.
