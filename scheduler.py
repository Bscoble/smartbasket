"""
Scheduler for running the cache warmer at 4:00 AM daily.
Can be deployed to cloud platforms or run locally with cron.
"""

import schedule
import time
import sys
from datetime import datetime
from cache_warmer import warm_the_cache

def scheduled_warm_cache():
    """Wrapper function for scheduled execution"""
    print(f"\n{'='*60}")
    print(f"🌙 Starting scheduled cache warmup at {datetime.now()}")
    print(f"{'='*60}\n")
    try:
        warm_the_cache()
        print(f"\n✅ Cache warmup completed at {datetime.now()}\n")
    except Exception as e:
        print(f"\n❌ Error during cache warmup: {e}\n")
        sys.exit(1)

def start_scheduler():
    """Start the scheduler"""
    # Schedule the job to run every day at 4:00 AM
    schedule.every().day.at("04:00").do(scheduled_warm_cache)
    
    print("📅 Scheduler initialized:")
    print(f"   ⏰ Running daily at 04:00 (4:00 AM)")
    print(f"   📍 Current time: {datetime.now()}")
    print(f"   🔄 Status: RUNNING\n")
    
    # Keep the scheduler running
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute if a job needs to run

if __name__ == "__main__":
    try:
        start_scheduler()
    except KeyboardInterrupt:
        print("\n⏹️  Scheduler stopped by user")
        sys.exit(0)
