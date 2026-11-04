"""Wait for the next GitHub Actions run, then check the betting pilot went live.
Run: python scripts/verify_betting.py   (polls once a minute for up to 75 minutes)
"""
import time
from datetime import datetime, timezone

import requests

API = "https://api.github.com/repos/MatevzKopac98/luka-watch"
SITE = "https://matevzkopac98.github.io/luka-watch/betting.html"
started = datetime.now(timezone.utc)

for _ in range(75):
    runs = requests.get(f"{API}/actions/runs", params={"per_page": 5}, timeout=20).json().get("workflow_runs", [])
    new = [r for r in runs if datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) > started]
    done = [r for r in new if r["status"] == "completed"]
    if done:
        r = done[-1]
        print(f"Run #{r['run_number']} {r['name']} ({r['event']}): {r['conclusion']}")
        tree = requests.get(f"{API}/git/trees/data", params={"recursive": 1}, timeout=20).json().get("tree", [])
        files = {x["path"] for x in tree}
        print("encrypted betting database on data branch:", "betting.sqlite.enc" in files)
        print("unencrypted database on data branch (must be False):", "betting.sqlite" in files)
        time.sleep(60)                                       # GitHub Pages takes a moment to publish
        page = requests.get(SITE, timeout=30).text
        print("live betting page locked with password:", 'id="payload"' in page,
              "| still 'not configured':", "Not configured yet" in page)
        break
    time.sleep(60)
else:
    print("No new run finished within 75 minutes")
