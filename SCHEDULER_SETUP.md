# Grocery Gecko Scheduler Setup

## GitHub Actions - Automated Daily Cache Warmup at 4:00 AM

Price-refresh workflows run daily once these workflow files are deployed to the
default branch (`master`). Manual runs remain available. Product metadata
enrichment is manual-only.

### Current Setup Status

✅ **Workflow File**: `.github/workflows/warmup.yml`
✅ **Schedule**: Daily at 18:00 UTC (4:00 AM AEST / 5:00 AM AEDT)
✅ **Trigger**: Manual trigger available

### Step 1: Add GitHub Secrets

The workflow needs these environment variables. Add them as GitHub Secrets:

1. Go to: **https://github.com/Bscoble/smartbasket/settings/secrets/actions**
2. Click **"New repository secret"** and add:
   - **Name**: `ZENROWS_KEY` | **Value**: your ZenRows API key
   - **Name**: `APIFY_TOKEN` | **Value**: your Apify API token  
   - **Name**: `GCP_SERVICE_ACCOUNT` | **Value**: Your full GCP service account JSON

### Step 2: Verify the Workflow

1. Go to: **https://github.com/Bscoble/smartbasket/actions**
2. Click **"Overnight Cache Warmer"** workflow
3. You'll see runs scheduled and completed

### Step 3: Manual Test (Optional)

Test the workflow manually before waiting for the automatic run:

1. Go to **https://github.com/Bscoble/smartbasket/actions**
2. Select **"Overnight Cache Warmer"** workflow
3. Click **"Run workflow"** → **"Run workflow"** button

---

## Local Testing (Optional)

If you want to test locally before relying on GitHub Actions:

```bash
# Install dependencies
pip install -r requirements.txt

# Run the scheduler locally (keeps running)
python3 scheduler.py

# Or run the cache warmer once
python3 cache_warmer.py
```

---

## How It Works

1. **GitHub Actions** checks the schedule daily
2. At **18:00 UTC** (4:00 AM AEST), the workflow triggers
3. Workflow steps:
   - Checks out your code
   - Sets up Python 3.11
   - Installs dependencies
   - Runs `cache_warmer.py` with environment variables
4. Prices are scraped and cached in Google Sheets
5. Logs available in GitHub Actions dashboard

---

## Monitoring & Logs

### Scheduled Jobs

| UTC | Job | Purpose |
|-----|-----|---------|
| 18:00 | Cache warmer | Refresh common staple prices |
| 18:30 | Category crawl | Discover retailer catalogue products and detail URLs |
| Manual only | Product metadata enrichment | Fetch up to 20 Woolworths ingredient/allergen records |
| 21:00 | Stale price revalidation | Refresh bounded stale-price batches |
| Manual only | Requested Product Discovery | Discover missing shopping-list/search products |
| Manual only | Coles Provider Trial | Test a candidate actor without changing production |

The cache warmer runs only the staple-price refresh; category discovery runs
in its own workflow, avoiding a duplicate crawl. All maintenance workflows share a
concurrency group so their Google Sheets writes do not overlap. Do not dispatch
multiple maintenance workflows at once: GitHub concurrency keeps only one
pending run, and a newer queued run can replace it.

Each job refreshes the `Performance Dashboard` after its source data has been
successfully saved. This behavior lives in the Python job entry points, so it
also applies when a job is run manually rather than through GitHub Actions.
If persistence fails, the job exits with an error and skips the dashboard
refresh instead of presenting a partial snapshot.
The dashboard expands its worksheet grid as history grows. Stale-price
revalidation fails explicitly if targets exist but no prices are refreshed;
a green run must not conceal a zero-refresh batch.

Stale-price revalidation prioritizes catalogue matches for products currently
saved in customers' shopping lists, then fills unused slots with the oldest
stale entries. It uses the same name and package-size matching rules as the app
and respects store-specific product names. Products with a fresh match at a
store do not need a priority refresh there. Customer identifiers are not
included in the priority list or job logs.

The existing per-run limits remain 15 Woolworths, 5 Coles, and 15 Aldi products.
If shopping-list demand exceeds a store's limit, additional manual or scheduled
runs are needed; successful refreshes leave the stale queue. Missing catalogue
products still need discovery, and failed scrapes remain stale. A shopping-list
read failure stops the job rather than silently reverting to oldest-first.
The app continues to exclude prices older than 14 days.

To refresh shopping-list matches after deploying this change, run **Stale Price
Revalidation** from GitHub Actions, wait for completion, then click **Compare
Prices** again in the app. An existing report does not update automatically.

### Recovery from the October 1 pause

The schedules were removed on October 1, 2026. The preceding runs showed:

- Apify: `Monthly usage hard limit exceeded`.
- ZenRows: HTTP 402 `Payment Required`.
- Google Sheets: intermittent HTTP 500/429 errors.
- Dashboard: writes exceeded the worksheet row limit.

Verify provider credits and account limits before running paid scrapers. Start
**Overnight Category Crawl**, wait for it to finish, then run **Stale Price
Revalidation**. The cache warmer refreshes only six common staples, not an
arbitrary shopping list. Confirm products were saved and re-run the app's price
comparison; an existing report does not automatically recompute.

Metadata enrichment remains manual-only because it is not required to refresh
prices and was also hitting the ZenRows billing limit.

Product metadata is written to the `Product Metadata` worksheet. Complete and
partial records are refreshed after 180 days; unavailable pages retry after 14
days. The job requires `ZENROWS_KEY` and `GCP_SERVICE_ACCOUNT`, and uses the
optional `ZENROWS_COST_PER_REQUEST_USD` repository variable for cost reporting.

### View Workflow Runs
https://github.com/Bscoble/smartbasket/actions

### See Details of Each Run
Click on any workflow run to see:
- Start/end time
- Success/failure status
- Full logs and output

### Troubleshooting

| Issue | Solution |
|-------|----------|
| Workflow not running | Check: 1) Secrets are set, 2) Branch is `master`, 3) Workflow file exists |
| Import errors | Make sure all packages are in `requirements.txt` |
| API failures | Check API keys are correct and have credits |
| Google Sheets errors | Verify GCP service account has Sheets access |

---

## Catalogue Expansion and US$20 Daily Budget

### Temporary development-only stale-price fallback

To test the app while price refreshes are being repaired, opt in on a private
development instance:

```sh
DEVELOPMENT_ALLOW_STALE_PRICES=true streamlit run app.py
```

The flag defaults to `false` and accepts only `true` or `false`. Do not enable it
on the public app. Comparison still prefers today's specials, fresh shelf prices,
and fresh cached prices. Only when these are unavailable does it use a matching,
dated, valid older shelf price. Expired specials and expired cache entries remain
excluded. There is no maximum age for this development fallback.

Outdated prices show their last-verified dates, and affected comparisons label
totals, rankings and savings as estimates. The fallback does not update price
timestamps, call paid scrapers, or change the 14-day revalidation cutoff.
Disable the flag (or unset it) to restore normal comparison behavior; development
reports are discarded when the flag is disabled. Generate a new comparison after
scraping to see refreshed prices.

Expanded crawling and higher revalidation throughput are opt-in and manual-only.
Existing scheduled jobs retain their original batch sizes. No new workflow
automatically runs a candidate provider or discovers queued demand.

### Repository Variables

Set these under GitHub **Settings > Secrets and variables > Actions > Variables**:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SCRAPER_DAILY_BUDGET_USD` | `20` | Shared daily reservation budget, in USD |
| `SCRAPER_MAX_REQUESTS_PER_DAY` | `80` | Shared paid-request/run cap, including retries |
| `APIFY_MAX_RUN_COST_USD` | `1` | Reservation and Apify maximum-charge setting per actor run |
| `ZENROWS_MAX_REQUEST_COST_USD` | `1` | Conservative reservation when ZenRows unit cost is unknown |
| `ZENROWS_COST_PER_REQUEST_USD` | unset | Your measured all-in cost for a rendered premium-proxy request |
| `EXPANDED_CATALOG_ENABLED` | unset/false | Set `true` to enable expansion on manual crawl/revalidation runs |

The `Scraper Budget` worksheet is a persistent UTC-day ledger shared by cache
warmup, category crawling, stale-price revalidation, requested discovery,
metadata enrichment, and provider trials. A reservation must be saved before a
paid call. Known actual Apify charges replace reservations; unknown costs and
unfinished calls retain their full reservation. Ledger errors stop paid work.
The local `backfill_standard_prices.py` entry point also uses this ledger.
Retries each consume a reservation and a request slot. Billing/access failures
stop further calls in that process.

**This is a planning guard, not a guaranteed provider billing ceiling.** Apify's
maximum-charge setting depends on the actor's billing model. ZenRows does not
receive a per-request dollar cap: its configured cost or unknown-cost
reservation may differ from the final bill. Set provider-side account spending
limits as well, and verify actual costs before increasing request caps or
reducing reservations. Charges from other apps, manual debugging scripts, or
uncoordinated local jobs are outside this ledger. Do not run local maintenance
alongside GitHub jobs: the GitHub concurrency group serializes cloud jobs, not
arbitrary external processes.

If all costs are unknown, the default $1 reservations permit at most 20 paid
requests that UTC day, even though the request ceiling is 80. Prioritize
shopping-list revalidation/discovery before broad discovery when manually
allocating the budget. A budget stop is logged explicitly; already saved
checkpoints remain available, and a subsequent day can resume.

### Discovery and Refresh Behaviour

- Expanded Coles rotates **25 queries per run**, instead of 10, through broad,
  specific product-family, brand and package-size searches. Its current actor
  still uses the first page only.
- Expanded Woolworths/Aldi rotate **20 search targets per store**, with up to
  three sequential pages per target. Work alternates between stores rather
  than exhausting every Woolworths target before starting Coles/Aldi.
- Expanded revalidation has target ceilings of **200 Woolworths, 100 Coles,
  and 150 Aldi**. These are selection ceilings, not promised refresh counts:
  the daily budget, request cap and provider success rates govern actual work.
- Stale matches for active shopping lists and queued requests take priority.
  Existing fresh matches are skipped; reliable app prices still expire after
  14 days.
- Missing searches and unpriced comparisons enqueue only product descriptions
  and stores, never customer identifiers. The manual **Requested Product
  Discovery** workflow also seeds missing matches from saved shopping lists.
  It processes up to 15 requests, tries at most three times, and cools failed
  requests down for a day. Existing stale matches go to revalidation instead.
- Every successful page is checkpointed before its cursor can be reused in
  later jobs. Catalogue replacement uses a single values write, rather than
  clearing the table first; table grids expand as needed.
- Failed pages do not advance their page cursor. Ambiguous empty results are
  retried and cooled down, not interpreted as proven end-of-results. After
  three empty visits, the query restarts at page one after cooldown, avoiding
  indefinite spending at an unproductive page.
- Identical page signatures trigger a wrap and cooldown. Explicit pagination
  metadata, when supplied by the actor for the requested page, can confirm
  exhaustion and wrap the cursor. Merely receiving fewer than 20 products
  is not treated as proof of exhaustion.

### Growth and Efficiency Reporting

`Catalogue Metrics` records new entries, existing entries refreshed and
duplicate products **after successful persistence**. Duplicates are suppressed
within a run using retailer IDs or product URLs where available; name keys are
the fallback. Multiple jobs on the same day can refresh the same product, so
aggregated refresh counts represent refresh operations, not a distinct daily
product count.

The `Performance Dashboard` now includes catalogue growth/refresh metrics,
logged request/error counts, known/unknown costs, cost per new or refreshed
entry, reservation versus actual spend, freshness by store, and basket
coverage. Cost efficiency prefers the persistent spend ledger where available;
ratios remain blank if any request cost in that group is unknown. Legacy
comparison events without matched-item counts have unknown coverage, not zero.
The crawler's total products returned is no longer labelled as new additions.

### Manual Rollout

1. Deploy the changes to `master`, confirm scraper credits and provider-side
   limits, and configure the variables above.
2. Run **Stale Price Revalidation**, then **Requested Product Discovery**,
   waiting for each to complete before dispatching the next job.
3. Set `EXPANDED_CATALOG_ENABLED=true` and manually run **Overnight Category
   Crawl** when the ledger has budget remaining.
4. Compare new entries, refreshed entries, freshness, basket coverage and
   cost-efficiency over several runs. Re-run **Compare Prices** in the app to
   recompute a report; existing reports do not automatically refresh.
5. Increase throughput only after measuring real provider charges and
   verifying increased coverage. Automatic expanded scheduling is deliberately
   not enabled by this change.

### Alternative Coles Provider Trial

The manual **Coles Provider Trial** accepts an explicit candidate actor ID and
makes two budgeted requests for pages one and two of a biscuits search.
The candidate must accept `urls`/`max_items_per_url` input and return the
supported Coles payload schema. The trial requires product IDs, product URLs,
prices and unit metadata, and at least **80% new product IDs on page two**.
Empty results, missing metadata and repeating pages fail the trial.

The trial never writes to the catalogue or switches the production actor.
A passing result is a prerequisite for review, not proof of broad reliability:
check additional categories, package sizes, price accuracy, retailer terms,
location-specific pricing and real billing before adopting it. No candidate
has been validated live as part of this implementation.

## Alternative: Local Cron Job

If you're running this on your own Linux/Mac server:

```bash
# Edit crontab
crontab -e

# Add this line (runs at 4:00 AM daily)
0 4 * * * cd /workspaces/smartbasket && python3 cache_warmer.py >> /var/log/smartbasket-cache.log 2>&1
```

---

## Next Steps

1. **Add GitHub Secrets** (see Step 1 above)
2. **Verify workflow runs** (see Step 2 above)
3. **Monitor in GitHub Actions** dashboard

Your cache warmer is now **automated and running at 4:00 AM daily!** 🎉
