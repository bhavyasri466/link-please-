#!/usr/bin/env python3
"""
LinkPlease CLI Helper Toolkit
Utilities to interact with the Mock Pseudogram API:
- Apply for developer access
- Generate API key
- Start 500-event stress simulation
- Fetch simulation ground truth and compare against local /stats
- Submit final assignment
"""

import argparse
import json
import sys
import time
import httpx

DEFAULT_BASE_URL = "https://pseudogram-api.onrender.com"

def apply_for_key(base_url: str, name: str, email: str, phone: str, linkedin_url: str, whatsapp: str = None):
    url = f"{base_url.rstrip('/')}/v1/apply"
    payload = {
        "name": name,
        "email": email,
        "phone": phone,
        "whatsapp": whatsapp or phone,
        "linkedin_url": linkedin_url
    }
    print(f"[*] Applying for developer access at {url}...")
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(url, json=payload)
        print(f"[*] Response ({resp.status_code}): {resp.text}")
        if resp.status_code in (200, 201):
            print("[+] Application submitted successfully! Proceed to keygen.")
            return True
        else:
            print(f"[-] Application failed: {resp.text}")
            return False

def get_api_key(base_url: str, email: str):
    url = f"{base_url.rstrip('/')}/v1/keygen"
    payload = {"email": email}
    print(f"[*] Requesting API key from {url}...")
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(url, json=payload)
        print(f"[*] Response ({resp.status_code}): {resp.text}")
        if resp.status_code == 200:
            data = resp.json()
            api_key = data.get("api_key")
            print(f"\n[+] SUCCESS! Your API Key is: {api_key}")
            print(f"[*] Add this to your .env file as: PSEUDOGRAM_API_KEY={api_key}")
            return api_key
        else:
            print(f"[-] Keygen failed: {resp.text}")
            return None

def start_simulation(base_url: str, api_key: str, webhook_url: str, count: int = 500, duration_seconds: int = 10):
    url = f"{base_url.rstrip('/')}/v1/simulate/start"
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    payload = {
        "webhook_url": webhook_url,
        "count": count,
        "duration_seconds": duration_seconds
    }
    print(f"[*] Firing {count} events over {duration_seconds}s to {webhook_url}...")
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            run_id = data.get("run_id")
            print(f"[+] Simulation started! Run ID: {run_id}")
            print(f"[*] Monitor live stats at your dashboard or run:")
            print(f"    python tools/api_helper.py --truth {run_id} --api-key {api_key}")
            return run_id
        else:
            print(f"[-] Failed to start simulation ({resp.status_code}): {resp.text}")
            return None

def fetch_truth_and_compare(base_url: str, api_key: str, run_id: str, local_url: str = "http://localhost:8000"):
    truth_url = f"{base_url.rstrip('/')}/v1/simulate/{run_id}/truth"
    headers = {"X-API-Key": api_key}
    
    print(f"[*] Fetching simulation ground truth from {truth_url}...")
    with httpx.Client(timeout=15.0) as client:
        t_resp = client.get(truth_url, headers=headers)
        if t_resp.status_code != 200:
            print(f"[-] Failed to fetch truth ({t_resp.status_code}): {t_resp.text}")
            return
            
        truth_data = t_resp.json()
        print("\n=== Mock API Ground Truth ===")
        print(json.dumps(truth_data, indent=2))
        
        # Try fetching local stats
        try:
            l_resp = client.get(f"{local_url.rstrip('/')}/stats")
            if l_resp.status_code == 200:
                local_stats = l_resp.json()
                print("\n=== Local Service Live /stats ===")
                print(json.dumps(local_stats, indent=2))
            else:
                print(f"[-] Could not fetch local stats: {l_resp.status_code}")
        except Exception as e:
            print(f"[!] Note: Local server at {local_url} not reachable ({e})")

def submit_assignment(
    base_url: str,
    email: str,
    github_repo: str,
    working_url: str,
    loom_url: str,
    parts_completed: str = "A+B+C",
    start_date: str = "2026-08-25"
):
    url = f"{base_url.rstrip('/')}/v1/submit"
    payload = {
        "email": email,
        "github_repo": github_repo,
        "working_url": working_url,
        "loom_url": loom_url,
        "parts_completed": parts_completed,
        "start_date": start_date
    }
    print(f"[*] Submitting assignment to {url}...")
    print(json.dumps(payload, indent=2))
    
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, json=payload)
        print(f"[*] Response ({resp.status_code}): {resp.text}")
        if resp.status_code in (200, 201):
            print("[+] ASSIGNMENT SUBMITTED SUCCESSFULLY! 🎉")
        else:
            print(f"[-] Submission failed: {resp.text}")

def main():
    parser = argparse.ArgumentParser(description="LinkPlease Tech Intern Assignment Helper")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base Mock API URL")
    parser.add_argument("--api-key", default="", help="Pseudogram API Key")
    
    # Actions
    parser.add_argument("--apply", action="store_true", help="Apply for developer access")
    parser.add_argument("--keygen", action="store_true", help="Generate API key")
    parser.add_argument("--simulate", action="store_true", help="Trigger simulation")
    parser.add_argument("--truth", type=str, help="Fetch simulation truth by run_id")
    parser.add_argument("--submit", action="store_true", help="Submit final assignment")
    
    # Arguments
    parser.add_argument("--name", help="Full name")
    parser.add_argument("--email", help="Email address")
    parser.add_argument("--phone", help="Phone number with country code")
    parser.add_argument("--whatsapp", help="WhatsApp number (optional)")
    parser.add_argument("--linkedin", help="LinkedIn profile URL")
    parser.add_argument("--webhook-url", help="Your deployed/ngrok webhook URL")
    parser.add_argument("--github-repo", help="Public GitHub repository URL")
    parser.add_argument("--working-url", help="Your live deployed URL")
    parser.add_argument("--loom-url", help="Your 3-minute Loom video URL")
    parser.add_argument("--parts", default="A+B+C", help="Parts completed: A, A+B, or A+B+C")
    parser.add_argument("--start-date", default="2026-08-25", help="Honest start date (YYYY-MM-DD)")
    parser.add_argument("--count", type=int, default=500, help="Event count for simulation")
    parser.add_argument("--duration", type=int, default=10, help="Duration in seconds for simulation")

    args = parser.parse_args()

    if args.apply:
        if not (args.name and args.email and args.phone and args.linkedin):
            print("[-] Error: --name, --email, --phone, and --linkedin are required for --apply.")
            sys.exit(1)
        apply_for_key(args.base_url, args.name, args.email, args.phone, args.linkedin, args.whatsapp)

    elif args.keygen:
        if not args.email:
            print("[-] Error: --email is required for --keygen.")
            sys.exit(1)
        get_api_key(args.base_url, args.email)

    elif args.simulate:
        if not (args.api_key and args.webhook_url):
            print("[-] Error: --api-key and --webhook-url are required for --simulate.")
            sys.exit(1)
        start_simulation(args.base_url, args.api_key, args.webhook_url, args.count, args.duration)

    elif args.truth:
        if not args.api_key:
            print("[-] Error: --api-key is required to check truth.")
            sys.exit(1)
        fetch_truth_and_compare(args.base_url, args.api_key, args.truth)

    elif args.submit:
        if not (args.email and args.github_repo and args.working_url and args.loom_url):
            print("[-] Error: --email, --github-repo, --working-url, and --loom-url are required for --submit.")
            sys.exit(1)
        submit_assignment(
            args.base_url,
            args.email,
            args.github_repo,
            args.working_url,
            args.loom_url,
            args.parts,
            args.start_date
        )
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
