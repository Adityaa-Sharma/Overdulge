# Overdulge — autonomous development bootstrap kit

Spend & food intelligence over Swiggy + Zepto. This repo is built and shipped
by an agent loop: BA → SA → Developer → SA gate → Deploy → QA → sign-off.
Humans own: this kit's files, secrets, escalations, and platform OTP logins.

## Bootstrap checklist (do in order)

### 1. Repo
- [ ] Create the GitHub repo `overdulge` (private recommended), push this kit
      to `main`.
- [ ] Settings → Pages → Source: **GitHub Actions**.
- [ ] Branch protection on `main`: require a pull request + require the CI
      status checks; allow the GitHub App (below) to merge.

### 2. GitHub App (the agents' identity — makes bot→bot triggers work)
- [ ] https://github.com/settings/apps → New GitHub App. Webhook: off.
      Permissions: Contents RW, Issues RW, Pull requests RW, Actions R.
- [ ] Install it on this repo. Generate a private key.
- [ ] Repo secrets: `APP_ID`, `APP_PRIVATE_KEY`.

### 3. Remaining secrets (Settings → Secrets and variables → Actions)
- [ ] `CLAUDE_CODE_OAUTH_TOKEN` — powers all agents (claude-code-action).
      Generate with `claude setup-token` (requires a Claude Pro/Max
      subscription). Agent runs bill against the subscription, not API
      credits. Subscription rate limits apply to the whole loop.
- [ ] `CLOUDFLARE_API_TOKEN` (Workers deploy scope) + `CLOUDFLARE_ACCOUNT_ID`.
- [ ] Azure OpenAI — the product's LLM (used by the Worker, set later as Worker
      secrets too during scaffolding; kept here for CI/QA needs):
      `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `OPENAI_API_VERSION`,
      `AZURE_OPENAI_DEPLOYMENT`.
      Note: this powers the *product*, not the agents. The agent loop runs on
      `ANTHROPIC_API_KEY` — Azure OpenAI cannot drive claude-code-action.
- [ ] Supabase: create free project; note URL + anon key + service-role key.
      These become Worker secrets during scaffolding (task will specify).

### 4. Labels & board
- [ ] `bash scripts/bootstrap_labels.sh <owner>/overdulge`
- [ ] Create a GitHub Project board with columns: Backlog / Architecture /
      Ready / In progress / In review / QA / Done (optional but useful).

### 5. Ignition
- [ ] Actions tab → run **BA Agent** manually (workflow_dispatch). It reads
      docs/BRD.md, creates the sign-off checklist and the first feature
      issues labeled `needs-architecture` → SA fires → tasks appear →
      Developer fires → the loop is alive.
- [ ] Watch the first full cycle end-to-end before leaving it unattended.

## The loop

| Agent | Trigger | Owns |
|---|---|---|
| BA | daily cron + manual | requirements, priorities, direction, sign-off |
| SA (arch) | issue labeled `needs-architecture` | design, task breakdown |
| SA (gate) | PR opened/updated | review, merge, reject |
| Developer | `ready-for-dev` / `ready-for-fix` / changes-requested | one task per run, PRs |
| QA | Deploy success + weekly cron | verification, bugs, regression |
| Sweep | nightly cron | re-fires anything stuck >24h; reports escalations |

Safety rails: `escalation:human` freezes an item after 3 failed attempts
(review or QA); the sweep is dead-letter recovery; CI enforces the read-only
guarantee (NFR-1 denylist of mutating platform tools); `main` is PR-only.

## Human duties while the loop runs
- Check `escalation:human` items (the nightly sweep report lists them).
- Perform Swiggy/Zepto OTP logins when link-flow testing needs a real account.
- Approve any change to docs/BRD.md, agents/*.md, or workflows — agents are
  forbidden from editing these.
