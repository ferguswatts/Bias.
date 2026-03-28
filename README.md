# Byline Card — NZ Journalist Transparency

A Chrome extension that shows you the political lean and connections of the journalist writing the article you're reading. NZ-only. Open source.

## How it works

Hover over a journalist's byline on any NZ news site. A card appears showing:

- **Coverage distribution** — what percentage of their articles score Left, Centre-Left, Centre, Centre-Right, or Right (based on AI analysis)
- **Documented connections** — family ties to politicians, marriages to other journalists, employer relationships. Every connection links to its source.
- **Confidence level** — how many articles were analyzed

## Supported sites

NZ Herald, Stuff, RNZ, 1News, Newsroom, The Spinoff, interest.co.nz

## Install (developer mode)

1. Clone this repo
2. `cd extension && npm install && npm run build`
3. Open Chrome → `chrome://extensions` → Enable "Developer mode"
4. Click "Load unpacked" → select `extension/dist/chrome-mv3`

## Pipeline (for contributors)

The scoring pipeline runs daily via GitHub Actions. To run locally:

```bash
cd pipeline
pip install -r requirements.txt
playwright install chromium
export ANTHROPIC_API_KEY=your_key
python -m pipeline.run --journalists 5 --dry-run
```

## Architecture

```
Extension (WXT + React) ← data.json ← Pipeline (Python + SQLite)
```

Two moving parts. No API server. No database server. No hosting costs.

- Extension fetches `data.json` from GitHub on startup, caches locally
- Pipeline scrapes articles, scores them via LLM, exports JSON
- Per-site adapters handle each NZ news site's HTML structure

## Methodology

Articles are scored by AI (Claude API) on a -1.0 to +1.0 scale across five dimensions: framing, source selection, language, topic emphasis, and omission. Scores are bucketed into Left / Centre-Left / Centre / Centre-Right / Right. The journalist's card shows the distribution of their articles across these buckets.

Every connection on the card links to a public source. No unsourced claims.

## Contributing

- **Add a journalist:** Edit `data/journalists.csv`
- **Add a connection:** Edit `data/connections.csv` (must include source URL)
- **Add a site adapter:** Create `pipeline/sites/newsite.py` implementing `SiteAdapter`

All contributions are reviewed by a maintainer before merging.

## License

MIT
