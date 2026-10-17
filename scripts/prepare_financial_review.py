#!/usr/bin/env python3
"""Resolve and validate the exact PR targeted by a financial-review event."""

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    try:
        actor = os.environ["GITHUB_ACTOR"]
        owner, repo = os.environ["GITHUB_REPOSITORY"].split("/", 1)
        if actor != owner:
            raise ValueError("only the repository owner can authorize a financial review")
        event = json.loads(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8").read())
        number = event.get("pull_request", {}).get("number") or event.get("inputs", {}).get("pr_number")
        number = int(number)
        request = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}",
            headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                     "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            pr = json.load(response)
        labels = {item["name"] for item in pr.get("labels", [])}
        if pr.get("state") != "open" or pr.get("draft") or pr["base"]["ref"] != "main":
            raise ValueError("PR must be open, non-draft, and target main")
        if pr["head"]["repo"]["full_name"] != f"{owner}/{repo}":
            raise ValueError("fork PR code cannot run in the privileged financial-review job")
        if "needs-financial-review" not in labels:
            raise ValueError("PR must retain the needs-financial-review label")
        output = os.environ["GITHUB_OUTPUT"]
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"pr_number={number}\nhead_sha={pr['head']['sha']}\nbase_sha={pr['base']['sha']}\n")
    except (KeyError, TypeError, ValueError, OSError, urllib.error.HTTPError) as error:
        print(f"Financial Review target rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
