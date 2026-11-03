"""Wait for the latest GitHub Actions run to finish and print each step's result.
Run: python scripts/watch_run.py"""
import time
import requests

API = "https://api.github.com/repos/MatevzKopac98/luka-watch/actions"

for _ in range(60):
    runs = requests.get(f"{API}/runs?per_page=1", timeout=20).json().get("workflow_runs", [])
    if runs and runs[0]["status"] == "completed":
        run = runs[0]
        print(f"Run #{run['run_number']} ({run['event']}): {run['conclusion']}  {run['html_url']}")
        for job in requests.get(run["jobs_url"], timeout=20).json()["jobs"]:
            for s in job["steps"]:
                print(f"  {s['conclusion'] or s['status']:<10} {s['name']}")
        break
    time.sleep(15)
else:
    print("Timed out waiting for the run")
