# Job Search Agent

A personal agent that reads job posts from WhatsApp groups, ranks them against my profile and writes a short digest of the jobs worth applying to, with links.

It only reads. It never applies, logs in, or sends anything in my name. I decide and apply myself.

It runs when I send `/run` to my Telegram bot (on GitHub Actions) or from my laptop, costs nothing (Gemini free tier, free tiers of Cloudflare and GitHub), and is built from reusable skills in [ai-agent-skills](https://github.com/EyalHefer/ai-agent-skills).

## How a run works

```
Telegram inbox               posts, chat exports and feedback I sent the bot (worker/)
WhatsApp exports
  → whatsapp-export-reader   job posts with links, deduplicated by link
  → skip jobs seen before    memory between runs
  → rule-filter              drop senior, wrong field, far locations (keywords, no LLM)
  → profile-matcher          score every job from the post text (Gemini, batches of 25)
  → job-page-reader          open the top jobs, drop the closed ones, read the full description
  → profile-matcher          score them again from the full description, with my past decisions
  → digest                   the best jobs, why they fit and what I am missing
  → Telegram                 one message per job, with feedback buttons
```

Two rounds keep it fast and free: the cheap first round looks at hundreds of posts, and only about 20 job pages are opened in a browser. On real data, 4 of my top 7 jobs were already closed, which is why the page check exists.

## Feedback

With `telegram: true`, each digest job arrives as its own Telegram message with "✅ הגשתי / ❌ לא הגשתי"
buttons. Replying to a job's message adds my own match and reason (`60 דורש 3 שנות ניסיון`). The next
run collects both and merges them into one entry per job.

From the command line:

```
python agent.py feedback 3 --applied yes --match 85
python agent.py feedback 5 --applied no --reason "requires GPA 87+"
```

The next run gives these decisions to the model, so jobs like the ones I applied to score higher and my reasons for skipping a job apply to similar jobs.

## Setup

```
git clone https://github.com/EyalHefer/ai-agent-skills
git clone https://github.com/EyalHefer/job-search-agent
pip install -r ai-agent-skills/requirements.txt -r job-search-agent/requirements.txt
python -m playwright install chromium
```

1. Copy `config.example.yaml` to `config.yaml`.
2. Write your profile, preferences and scoring instructions. Start from [examples/](examples/).
3. Put a free Gemini API key in the `.env` file: `GEMINI_API_KEY=...`
4. Export your job groups from WhatsApp (group → More → Export chat → without media) into the exports folder.
5. Optional, for groups that block exporting: share job posts by hand to a private Telegram bot.
   Set it up as in [worker/README.md](worker/README.md), then set `inbox_dir` in `config.yaml` and
   `INBOX_URL` and `INBOX_TOKEN` in `.env`. Each run collects the shared posts first; if the Worker
   cannot be reached, the run goes on with the exports and what was collected before.

## Run

```
python agent.py run
```

The digest is printed, saved under `data/digests/`, and sent to Telegram when `telegram: true`.

### From the phone

`/run` in the bot starts `.github/workflows/run.yml` in a private repo that holds my profile and the
agent's memory (`data/`, `inbox/`). The workflow checks out this repo and the skills, runs the agent,
and commits what it learned back to the private repo. Chat exports are never committed; each run
downloads the ones shared to the bot. Secrets live in the private repo's Actions secrets. A private
repo keeps the run's logs private too.

## Privacy

- Personal files (profile, exports, memory, API key) stay out of git.
- The profile sent to the model has no name, phone number or ID.
- Exports contain other people's phone numbers. Only the job text and links are used.
- Groups that block exporting are respected, not worked around.
- The cloud run's data lives in a private repo; exports there exist only on the runner during a run.

## Tests

```
pytest
```

The tests run the whole pipeline with invented jobs and fake skills: no network, LLM or browser.
