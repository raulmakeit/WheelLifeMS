# WheelLifeMS Rules (Python / Flask Microservice)

## Technology Stack
- **Framework**: Python with Flask (Web REST API).
- **Core Libraries**: `feedparser` (RSS feeds), `beautifulsoup4` (HTML parsing), `google-generativeai` (Gemini API), `supabase-py` (Client).
- **AI Model**: Google Gemini (`gemini-2.5-flash` using REST transport).

## Development Constraints
- **Authentication**: Ensure all transactional routes (such as `/gather`) validate the client using the `Bearer <PYTHON_API_KEY>` header scheme.
- **Asynchronous Execution**: Long-running scraping tasks must run in background threads to avoid blocking the main Flask thread.
- **Scraper Status**: Always update the `system_settings` -> `scraping_status` table in Supabase to `in_progress` when starting a run, and back to `idle` upon completion or failure.
- **Gemini Cleaning**: AI prompts must strictly enforce markdown output and strip conversational introductory phrases.
