# Privacy policy for yt-autopilot

Last updated: 9 November 2026

yt-autopilot is a personal, open-source tool. It runs on its owner's own computer to prepare videos and upload them to the owner's own YouTube channel. It is not a hosted service and has no other users.

## What it accesses

When the owner signs in with Google, the tool requests these YouTube permissions:

- **Upload videos** (`youtube.upload`).
- **Manage the YouTube account** (`youtube`), used to read back uploaded videos, their settings and their public statistics, to set video details, and to remove a video when the owner chooses to replace it.

It does not access any other Google data, and it does not access data about viewers or about other people's accounts.

## How the data is used and stored

- The Google sign-in token is stored only in a local file on the owner's computer. It is never uploaded, published or shared.
- Video details, upload history and statistics are used only to run the tool and to produce the owner's weekly summary. They stay on the owner's computer, except where the owner chooses to publish the upload history in this repository.
- There is no server, database, analytics or advertising, and no data from Google is sold or shared with anyone.

Data from Google APIs is used only to provide the tool's functions to its owner, in line with the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including its Limited Use requirements.

The tool uses YouTube API Services. See the [YouTube Terms of Service](https://www.youtube.com/t/terms) and the [Google Privacy Policy](https://policies.google.com/privacy).

## Revoking access

Access can be removed at any time at [Google Account permissions](https://myaccount.google.com/permissions). Deleting the local token file also removes the tool's stored access.

## Contact

Questions can be raised through this repository's [issues page](https://github.com/snskr28/yt-autopilot/issues).
