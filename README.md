# London job search: runs five times a day

A scheduled GitHub Action searches Reed, Adzuna and a list of careers pages, then rebuilds the tracker page at `docs/index.html`. Industry tabs include All industries, Food & drink, Hotels & luxury, Culture & arts, Fashion & retail, Wellness and Other creative. The page includes posting-date filters, an Extend Search shortcut and targeted public Instagram-indexed search links.

## Setup (about 15 minutes)

1. Create a GitHub repository and upload everything in this folder (keep the `.github` folder).
2. Get free API keys: Reed (reed.co.uk/developers) and Adzuna (developer.adzuna.com). Optional: an Anthropic API key for scoring.
3. In the repository go to Settings, Secrets and variables, Actions, and add: `REED_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, and optionally `ANTHROPIC_API_KEY`.
4. In Settings, Pages, choose "Deploy from a branch", branch `main`, folder `/docs`.
5. In the Actions tab, open "search" and click "Run workflow" once to test. Choose `extensive` to run the wider set of role keywords across Reed and Adzuna, or `quick` for the normal run. The page’s Extend Search button opens this workflow. After setup it also runs at 06:00, 09:00, 12:00, 15:00 and 18:00 UTC (one hour later in UK summer time).
6. Open your Pages link. Statuses and notes are saved in your own browser (localStorage), so they are per device.

## How it behaves

- New board listings are always **leads**, never "confirmed open". The tracker checks seeded listing URLs for clear closure signals and drops them when detected. The current API results and repeat sightings reduce stale listings, but do not guarantee that an employer has not closed a role. Check the employer’s own application page before applying; listings not found for two successful runs are hidden. The page deliberately labels board leads as unverified.
- A job is hidden after it is missing from two runs in a row, and only when every source answered. Failed sources never close jobs.
- Disclosed salaries below the floor in `config.json` (39,000) are dropped. Estimated or missing salaries are kept and labelled.
- Non-food roles only appear if the advert mentions creative terms (video, photography, storytelling and so on), and, when scoring is on, only if the score is at least 60.
- Careers pages in `config.json` under `watch` are checked each run and flagged when their content changes. Pages that change on every load can give false alerts.
- `data/seed.json` holds the curated vacancies, employers and speculative ideas from the first session. Those are not re-verified automatically: delete stale entries by hand. Roles with an `x` date hide themselves after it.

## Limits

- Not tested against live Reed or Adzuna responses (no network where it was built). Check the first run's log in the Actions tab.
- A public repository makes the page and job data public. GitHub Pages on a private repository needs a paid plan.
- GitHub can pause scheduled runs on inactive repositories and may start runs a few minutes late. If updates stop, check the Actions tab.
- Instagram: the button opens targeted Google searches for publicly indexed Instagram posts. It does not scrape Instagram directly; Instagram restricts automated access and reliable post dates/statuses may not be available. Open each post and verify the vacancy.
- Posting dates are shown from source metadata where available; unknown or ambiguous dates cannot be reliably filtered except under “Any date”.
- Reed and Adzuna have their own terms and rate limits. Do not add LinkedIn or Indeed scraping.
