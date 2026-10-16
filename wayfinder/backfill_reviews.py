import argparse
from .db import db_manager
from .github.client import GitHubClient
from .derived.expertise_backfill import backfill

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Fetch file-specific reviews for already imported PRs; GitHub API only.')
    parser.add_argument('--limit',type=int,default=10)
    args=parser.parse_args()
    client=GitHubClient()
    try:
        if not client.token:
            raise SystemExit('Set GITHUB_TOKEN first')
        print(backfill(db_manager,client,args.limit),flush=True)
    finally:
        client.close()
