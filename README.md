# reactor-feed

Builds a one-page dashboard from two public NRC postings:

- **Power Reactor Status, last 365 days:** daily power % for every operating US unit.
- **Event Notification Report:** the NRC Operations Center's daily event list.

The dashboard shows units online, which units changed since the day before, offline streaks, a one-year trend line per unit, and the day's events. Each source file gets a SHA-256 hash, so a posting can later be anchored as a proof-of-publication receipt.

## Run

nrc.gov often refuses scripted downloads. If the script says it was refused, open each link in your browser, press Ctrl+S, and pass the saved files:

- Status: https://www.nrc.gov/documents-reports/document-collections/events-reports-associated-with/power-reactor-status-reports/PowerReactorStatusForLast365Days.txt
- Events: https://www.nrc.gov/documents-reports/document-collections/events-reports-associated-with/event-notification-reports/en

```powershell
python nrc_feed.py --status-file "$HOME\Downloads\PowerReactorStatusForLast365Days.txt" --events-file "$HOME\Downloads\en.html" --out nrc-feed.html --json nrc-feed.json
```

Use `--no-events` or `--no-status` to build only one half.

Tests: `python -m unittest test_nrc_feed`
