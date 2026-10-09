"""Disabled local scheduler for the external cache-warming job."""

def start_scheduler():
    """Report that automated external scraping is disabled."""
    print("External scraping jobs are disabled; no local schedule was started.")

if __name__ == "__main__":
    start_scheduler()
