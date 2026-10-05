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

The cache warmer runs only the staple-price refresh; category discovery runs
in its own workflow, avoiding a duplicate crawl. All four workflows share a
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
