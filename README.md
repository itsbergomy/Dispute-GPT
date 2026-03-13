# DisputeGPT

Autonomous credit repair software. Upload credit reports, analyze negative items with AI, generate dispute letters, and mail them — all from one dashboard.

## Quick Start

```bash
git clone https://github.com/itsbergomy/Dispute-GPT.git
cd Dispute-GPT
pip install -r requirements.txt
cp .env.example .env   # Fill in your API keys
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

## Features

- **AI Credit Report Analysis** — Upload a PDF credit report, GPT extracts and analyzes every negative item
- **Autonomous Dispute Pipeline** — Agent handles strategy selection, letter generation, review, and mailing across multiple rounds
- **Prompt Packs** — Swap dispute strategies per round (Default, Consumer Law, ACDV Response, Arbitration)
- **Custom Letter Templates** — Upload your own letters (PDF/DOCX/TXT), use them as templates with auto-filled client and account data
- **Supervised & Full Auto Modes** — Review every letter before it goes out, or let the agent run end-to-end
- **Round-by-Round Control** — Pipeline pauses between rounds so you can review outcomes and decide next steps
- **DocuPost Integration** — Letters mailed via USPS through the DocuPost API
- **Multi-Client CRM** — Manage multiple clients, track pipelines, store correspondence

## User Roles

| Role | Access |
|------|--------|
| **Free** | Manual dispute flow — upload PDF, generate one letter at a time |
| **Pro** | Full dispute folder, report analyzer, mail letters, correspondence tracking |
| **Business** | Everything in Pro + CRM dashboard, autonomous pipeline, custom letters, team features |

## Architecture

- **Flask** — Web framework with Jinja2 templates
- **SQLite** — Database (via SQLAlchemy + Flask-Migrate)
- **OpenAI GPT** — Credit report analysis and letter generation
- **Huey** — Background task queue (with thread-based fallback for development)
- **DocuPost API** — USPS letter mailing
- **Liquid Glass UI** — Custom CSS design system with frosted glass effects

## Environment Variables

See [`.env.example`](.env.example) for all required configuration.

## Development

All future changes go through feature branches and pull requests:

```bash
git checkout -b feature/your-feature
# make changes
git push -u origin feature/your-feature
# create PR on GitHub
```

## License

MIT License — see LICENSE for details.

## Contributing

Issues and pull requests welcome. See the feature branch workflow above.
