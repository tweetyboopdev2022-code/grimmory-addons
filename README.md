# Grimmory add-ons

Two self-hosted add-ons for [Grimmory](https://github.com/grimmory-tools/grimmory). Each folder has
its own README with install steps.

| Folder | What |
|---|---|
| [`grimmory-wishlist/`](grimmory-wishlist/) | A shared Wishlist tab: anyone signed in can ask for a book or a series, with suggestions from Hardcover, Google Books and Open Library, duplicate checks, "now in the library" when it arrives, and the gaps in series you own. A small sidecar container; Grimmory's code and database are untouched. |
| [`grimmory-kobo-setup/`](grimmory-kobo-setup/) | A "Set up my Kobo" button in Settings → Devices: plug in a Kobo and it is connected to the user's own account through Grimmory's built-in Kobo sync, with a backup and undo. Mac and Windows double-click files for other browsers. |

Both hook into Grimmory's web page through nginx, so a Grimmory UI change can break the entry points
until they are updated. They were written with AI assistance (Claude) and are used on a real library.

Licence: AGPL-3.0, the same as Grimmory.
