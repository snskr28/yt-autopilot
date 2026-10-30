# yt-autopilot

A local Python pipeline that writes, voices, edits and uploads videos for a faceless science YouTube channel: **one Short a day and two 8–10 minute videos a week**, using only free and open-source tools. The only AI text engine is [Claude Code](https://docs.anthropic.com/en/docs/claude-code) running headless (`claude -p`) on a Claude Pro login, so there are no API bills.

A human stays in the loop on purpose: nothing is produced until you have read the script and approved it.

> **Status:** built and tested end to end on Windows 11. Shorts on space and nature topics come out well; long videos on abstract or everyday-human topics are limited by what free, keyless stock footage exists (see [Known limitations](#known-limitations)).

---

## What it does

| Step | Tool | What happens |
|---|---|---|
| Ideas | Claude Code (`claude -p`) | Keeps a backlog of 30+ video ideas, each with a narrow subtopic, avoiding repeats of past titles |
| Script | Claude Code | Shorts: 100–130 words in 4–6 scenes. Long: outline → one call per section → title, description, tags, thumbnail text |
| **Review gate** | **You** | The script waits in `jobs/<job>/` until you create an empty `APPROVED` file |
| Voice | [Kokoro TTS](https://github.com/hexgrad/kokoro) | Natural narration scene by scene, short pauses between scenes, loudness normalised to −16 LUFS |
| Captions | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Word-level timings, grouped into 2–4 word caption chunks |
| Visuals | Wikimedia Commons, NASA (Pexels/Pixabay optional) | Searches stock video and photos per scene; Claude picks the best matches from their descriptions |
| Edit | MoviePy + FFmpeg | 9:16 or 16:9 timeline, animated captions, key-fact cards, music ducked ~20 dB under the voice |
| Thumbnail | Pillow | Long videos: most striking frame + 3–5 word headline, 1280×720 |
| Upload | YouTube Data API v3 | Uploaded as private, scheduled for the publish slot, with source credits and AI-use disclosure |
| Schedule | Windows Task Scheduler | Generates scripts in the morning, produces and uploads twice a day, writes a weekly summary |

## How it works

```
                 generate (09:00)                          produce (14:00, 21:00)
 ideas.json ──► script.json ──► [ you review ] ──► APPROVED ──► voice ─► captions ─► visuals ─► assemble ─► thumbnail ─► upload
   (backlog)    + status.json      edit freely,                 Kokoro   Whisper    Commons/   MoviePy     (long only)  private +
                in jobs/<job>/     or delete the                                    NASA +                              scheduled
                                   folder to reject                                 Claude picks
```

* **Two commands, split by the review gate.** `generate` writes a script into a new job folder. `produce` takes every approved job and runs the remaining steps. Each step skips work it has already done, so a run that fails or is interrupted picks up where it stopped.
* **Publish slots.** Every job gets a date: Shorts one per day, long videos on Tuesday and Friday, all at 18:00 IST by default. Uploads are private with `publishAt` set to that slot.
* **Failure isolation.** If a step fails, that job is marked `failed` with the step and error, the run moves on to the next job, and the job is retried on later runs (up to 3 attempts). Hitting the Claude Pro usage limit pauses the run cleanly instead of counting as a failure.
* **One run at a time.** An OS-level lock file means two scheduled runs never overlap.

### Choosing visuals

Free stock archives rarely match a sentence exactly, so visuals go through several stages:

1. For each scene, search for a few videos and photos (Commons, NASA, plus Pexels/Pixabay if you add keys). Only public domain, CC0 and CC BY files are kept.
2. One `claude -p` call per 8 scenes picks the candidates that actually fit the narration, judging from titles, descriptions and categories. It rejects text overlays, logos, news footage and keyword-only matches.
3. For scenes still short of approved clips, Claude suggests different searches and picks again.
4. Any slot still empty becomes a generated **motion card** (a drifting gradient with the scene's key phrase). Unapproved footage is never used.

Each scene is cut into shots of about 4 seconds (long videos) or up to 6 seconds (Shorts). Footage whose shape doesn't match the frame is shown large over a blurred copy of itself instead of being cropped hard.

## Policy guardrails

These are built into the code, not just documented. YouTube's inauthentic-content policy targets channels that look mass-produced, and the pipeline is designed to avoid that look.

* **Human review gate.** Nothing is produced or uploaded without the `APPROVED` file.
* **Pace cap.** At most 7 Shorts and 2 long videos per week. The pipeline refuses to queue more than 7 days ahead, so you can't bulk-upload a backlog.
* **Variety.** Caption styles, music tracks and thumbnail layouts rotate (least recently used first); no stock clip is reused within 30 videos; the same subtopic can't recur within 14 days.
* **Disclosure.** The altered/synthetic content flag is sent on every upload. The API doesn't report it back, so the weekly summary reminds you to confirm **AI use = Yes** in YouTube Studio.
* **Credits.** Every stock source's author, licence and link goes in the video description.
* **Accuracy.** The prompts forbid uncertain claims; the review step is where you fact-check.

## Requirements

* Windows 10/11 (macOS/Linux should work but are untested; use cron instead of Task Scheduler)
* **Python 3.12.** Kokoro 0.9.4 supports Python 3.10–3.12 only.
* [FFmpeg](https://ffmpeg.org/) on `PATH`
* [espeak-ng](https://github.com/espeak-ng/espeak-ng/releases) (recommended fallback for Kokoro)
* [Claude Code](https://docs.anthropic.com/en/docs/claude-code), logged in with a Claude Pro (or higher) account
* A Google account with a YouTube channel
* Roughly 3 GB of disk for models and dependencies; a long video takes ~40 minutes to render on a typical laptop CPU

## Setup

### 1. Install

```powershell
git clone https://github.com/snskr28/yt-autopilot.git
cd yt-autopilot
winget install Python.Python.3.12 Gyan.FFmpeg eSpeak-NG.eSpeak-NG
npm install -g @anthropic-ai/claude-code
claude            # log in with your Claude Pro account, then exit
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> **Never set `ANTHROPIC_API_KEY`.** With it set, Claude Code bills API credits instead of using your Pro plan. The pipeline also strips it from the environment it gives `claude -p`.

### 2. Configure

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Why |
|---|---|---|
| `CONTACT_EMAIL` | **yes** | Wikimedia's User-Agent policy requires contact details; without it, Commons downloads are rate-limited |
| `PEXELS_API_KEY` | optional | Much better footage of people and everyday scenes |
| `PIXABAY_API_KEY` | optional | Extra fallback for video |

Then edit [`config.yaml`](config.yaml): niche, voice, publish times, time zone and the pace and variety limits.

Add 10–20 background music tracks to `assets/music/`. The [YouTube Audio Library](https://studio.youtube.com/) is the safest source because it never triggers copyright claims. Fonts (OFL licensed) are already in `assets/fonts/`.

### 3. Check the setup

```powershell
.\.venv\Scripts\python.exe scripts\check_setup.py
```

This confirms that `claude -p` answers, FFmpeg runs and Kokoro can speak.

### 4. Connect YouTube (one time)

1. In [Google Cloud Console](https://console.cloud.google.com), create a project and enable **YouTube Data API v3**.
2. Under **OAuth consent screen / Google Auth Platform**, set the audience to **External** and add the channel's Google account as a **test user**.
3. Create an **OAuth client ID** of type **Desktop app**, download it and save it as `client_secret.json` in the project folder.
4. Log in:

   ```powershell
   .\.venv\Scripts\python.exe -m src.upload --login
   ```

   Pick the channel you want to upload to. This saves `token.json`.

> * While the consent screen is in *Testing*, the login expires after about 7 days. Run `--login` again weekly; the summary warns you.
> * New API projects are usually locked to private uploads until they pass the [YouTube API Services audit](https://support.google.com/youtube/contact/yt_api_form). Until then, scheduled videos stay private and you click **Publish** in Studio yourself.

### 5. Schedule it

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
```

| Task | When | Runs |
|---|---|---|
| generate-short | daily 09:00 | `generate --type short` |
| generate-long | Mon + Thu 09:00 | `generate --type long` |
| produce-1400, produce-2100 | daily 14:00 and 21:00 | `produce` |
| weekly-stats | Sun 20:00 | `stats` |

Tasks run as your user while you are logged in. A run missed because the PC was off starts as soon as it is back on. Remove them all with `install_schedule.ps1 -Remove`.

## Daily use

1. Each morning a new script appears in `jobs/<date>_<type>_<slug>/script.json`.
2. Read it against the review checklist: facts correct, a hook that holds, not a near-copy of a recent video, an honest title.
3. Edit it if you like (the `narration` fields are what gets spoken), then create an empty `APPROVED` file in that folder. To reject it, delete the folder and the idea goes back to the backlog.
4. The next `produce` run makes and uploads it as private and scheduled.
5. Glance at it in YouTube Studio before its publish time, and confirm **AI use = Yes**.
6. On Sundays, read `logs/weekly_<year>-W<week>.md`. It lists views, anything that needs you, failed jobs and open slots.

Expect about 1.5–2 hours a week of review.

### Commands

```powershell
.\.venv\Scripts\python.exe -m src.run generate --type short   # or --type long
.\.venv\Scripts\python.exe -m src.run produce                  # add --no-upload to stop before uploading
.\.venv\Scripts\python.exe -m src.run stats
.\.venv\Scripts\python.exe -m src.ideas                        # list the backlog (--refill, --relabel)
```

Every module also runs on its own against one job folder, so you can redo a single step. For example, to swap a bad clip, edit `visuals.json`, delete `final.mp4`, and run:

```powershell
.\.venv\Scripts\python.exe -m src.assemble jobs\<job>
.\.venv\Scripts\python.exe -m src.upload --verify jobs\<job>
```

## Project layout

```
yt-autopilot/
├── config.yaml            niche, voice, schedule, pace cap, variety rules
├── .env                   keys and contact email (git-ignored)
├── client_secret.json     Google OAuth client (git-ignored)
├── token.json             YouTube login (git-ignored)
├── prompts/               every Claude prompt, as editable templates
├── assets/                fonts (included), music (yours), brand
├── data/                  ideas.json (backlog), history.json (everything uploaded)
├── jobs/                  one folder per video: script, status, voice, clips, final.mp4 (git-ignored)
├── logs/                  one log per day, weekly summaries, run lock (git-ignored)
├── scripts/               setup and acceptance checks, Task Scheduler setup
└── src/
    ├── run.py             orchestrator: generate / produce / stats
    ├── claude_cli.py      wrapper around `claude -p`
    ├── ideas.py  script.py  voice.py  captions.py
    ├── visuals.py  assemble.py  thumbnail.py  upload.py
    ├── jobs.py            publish slots and pace cap
    └── stats.py           weekly summary
```

## Known limitations

* **Stock footage for abstract topics.** Wikimedia Commons and NASA are excellent for space, earth, nature and microscopy, but thin on everyday shots of people, hands and eyes. Long videos on such topics end up with more photos and motion cards. A Pexels key fixes most of this.
* **Coverr is not used.** Coverr's API terms exclude commercial use of the videos, even though its website licence allows it, so the pipeline doesn't call that API.
* **Openverse is not used.** It was unreliable during development (repeated timeouts).
* **AI-use flag.** The API accepts `containsSyntheticMedia` but never reports it back, so it has to be confirmed in Studio.
* **Render time.** Everything runs on CPU: about 4 minutes for a Short and about 40 minutes for a 9-minute video.
* **Claude Pro limits.** Script generation shares your Pro usage window. A long video uses about 25–30 `claude -p` calls (about 11 for the script, the rest for clip picks); a Short about 2–3. Hitting the limit just postpones work to the next run.

## Costs

None beyond a Claude Pro subscription. Kokoro, faster-whisper, FFmpeg, MoviePy and Pillow run locally. Wikimedia Commons, NASA, the YouTube Data API (default 10,000 units/day; an upload costs 100) and Task Scheduler are free.

## Licence

Code: [MIT](LICENSE). Bundled fonts (Anton, Bebas Neue, Montserrat) are under the SIL Open Font License; see `assets/fonts/OFL-*.txt`. Stock media fetched at run time keeps its own licence and is credited in each video's description.
