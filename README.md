# IronPulse Fitness — Simple Gym Website

A responsive gym website with a Python server that saves visit requests to a local SQLite database and provides a password-protected admin page.

## Run locally

The forms and chatbot need the Python server. For the chatbot, set `CHAT_PROVIDER=gemini` and add your Google Gemini API key to `GEMINI_API_KEY` in `.env`. The default model is `gemini-3.6-flash`; change `GEMINI_MODEL` if needed. Also set `ADMIN_PASSWORD` to a private password of at least 12 characters. `.env.example` shows the variable names. Keep `.env` private; it is ignored by Git.

```powershell
python server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) to view the site. Visit [http://127.0.0.1:8000/admin](http://127.0.0.1:8000/admin) and sign in with username `admin` and the password you set. The admin dashboard sidebar links to the overview, contact requests, homepage banner, and memberships. Edit the banner text, button, and optional HTTPS image URL, plus membership names, prices, and features; save to publish changes. You can override the username with the `ADMIN_USERNAME` environment variable.

Membership prices are shown in Indian rupees (INR, ₹); the amount values remain editable in the admin panel.

The homepage chat assistant uses the Gemini API by default. To use OpenAI instead, set `CHAT_PROVIDER=openai` and configure `OPENAI_API_KEY` and `OPENAI_MODEL`. Restart `python server.py` after changing `.env`. The assistant answers questions about current membership plans, class schedule, general diet topics, and age policies. Edit `chatbot_knowledge.json` to update gym-specific facts; membership details are read from the admin settings. The assistant will direct visitors to staff when a policy is not listed and will not give personalized medical or diet prescriptions.

New form submissions are saved in `visits.sqlite3` in this folder. Keep this file backed up if you need to retain requests. The server listens on your own computer only; public hosting needs HTTPS and suitable production hosting configuration.

## Structure

- `index.html` — page content
- `css/styles.css` — styles
- `js/main.js` — mobile menu, footer year, and form confirmation
- `js/chatbot.js` — chat widget behavior
- `chatbot_knowledge.json` — class schedule, gym facts, age and nutrition guidance
- `.env.example` — required local environment settings template
- `server.py` — website server, form handler, SQLite storage, and admin page

Customize the gym name, prices, and contact details in `index.html`.
