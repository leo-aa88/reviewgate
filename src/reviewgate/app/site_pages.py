"""Minimal hosted HTML pages (``docs/DESIGN.md`` §5.1, §8.1; issue #38)."""

from __future__ import annotations

import html
from typing import Final

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from reviewgate.app.settings import AppSettings

router = APIRouter(tags=["public"])

# Verbatim short positioning from ``docs/DESIGN.md`` §3 (issue #38).
_TAGLINE: Final[str] = (
    "Make pull requests reviewable before humans waste time on them."
)
_SHORT_PITCH: Final[str] = (
    "ReviewGate is a PR intake gate for engineering teams. It flags oversized, "
    "unclear, risky, or mixed-scope pull requests before they reach human reviewers."
)

_ONBOARDING_DOC_URL: Final[str] = (
    "https://github.com/leo-aa88/reviewgate/blob/main/docs/ONBOARDING.md"
)


def _install_section(settings: AppSettings) -> str:
    url = settings.github_app_install_url
    if url and url.strip():
        safe = html.escape(url.strip(), quote=True)
        return (
            f'<p><a class="cta" href="{safe}">Install ReviewGate</a> '
            "(GitHub App)</p>"
        )
    return (
        "<p><em>Install URL is not configured on this deployment.</em> "
        "Operators should set <code>REVIEWGATE_GITHUB_APP_INSTALL_URL</code> "
        "to the public GitHub App installation link.</p>"
    )


def _landing_html(settings: AppSettings) -> str:
    install_block = _install_section(settings)
    onboarding = html.escape(_ONBOARDING_DOC_URL, quote=True)
    tagline = html.escape(_TAGLINE, quote=True)
    pitch = html.escape(_SHORT_PITCH, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ReviewGate</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 42rem; margin: 2rem auto;
      padding: 0 1rem; line-height: 1.5; color: #111; }}
    .cta {{ display: inline-block; margin: 0.5rem 0.5rem 0.5rem 0; padding: 0.5rem 1rem;
      background: #1f6feb; color: #fff; text-decoration: none; border-radius: 6px; }}
    footer {{ margin-top: 2rem; font-size: 0.9rem; color: #444; }}
    label {{ display: block; margin-top: 0.75rem; }}
    input, select {{ width: 100%; max-width: 28rem; padding: 0.35rem; }}
  </style>
</head>
<body>
  <main>
    <h1>ReviewGate</h1>
    <p><strong>{tagline}</strong></p>
    <p>{pitch}</p>
    <p><cite>Source: docs/DESIGN.md §3 Positioning (tagline and short pitch)</cite></p>
    <h2>Get started</h2>
    {install_block}
    <h2>Join the private beta</h2>
    <p>Request access by email. Fields match <code>docs/DESIGN.md</code> §17.3.</p>
    <form id="beta-form">
      <label>Email (required) <input name="email" type="email" required></label>
      <label>Name <input name="name" type="text"></label>
      <label>Company <input name="company" type="text"></label>
      <label>Role <input name="role" type="text"></label>
      <label>GitHub org <input name="github_org" type="text"></label>
      <label>Team size
        <select name="team_size">
          <option value="">—</option>
          <option>1-9</option>
          <option>10-50</option>
          <option>51-200</option>
          <option>201+</option>
        </select>
      </label>
      <input type="hidden" name="source" value="landing">
      <p><button class="cta" type="submit">Submit beta request</button></p>
      <p id="beta-status" role="status"></p>
    </form>
    <script>
    document.getElementById("beta-form").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const fd = new FormData(e.target);
      const body = {{}};
      for (const [k, v] of fd.entries()) {{
        if (v === "") continue;
        body[k] = v;
      }}
      const statusEl = document.getElementById("beta-status");
      statusEl.textContent = "Submitting…";
      try {{
        const res = await fetch("/api/beta-leads", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(body),
        }});
        const data = await res.json().catch(() => ({{}}));
        if (res.ok && data.ok) statusEl.textContent = "Thanks — we received your request.";
        else statusEl.textContent = "Request failed (" + res.status + ").";
      }} catch (err) {{
        statusEl.textContent = "Network error.";
      }}
    }});
    </script>
    <footer>
      <a href="/privacy">Privacy</a>
      · <a href="/feedback">Beta feedback</a>
      · <a href="{onboarding}">Beta onboarding doc</a>
    </footer>
  </main>
</body>
</html>
"""


def _installation_success_html() -> str:
    onboarding = html.escape(_ONBOARDING_DOC_URL, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ReviewGate — Installed</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 42rem; margin: 2rem auto;
      padding: 0 1rem; line-height: 1.5; }}
    a {{ color: #1f6feb; }}
  </style>
</head>
<body>
  <main>
    <h1>ReviewGate is installed</h1>
    <p>Next steps for your team:</p>
    <ol>
      <li>Open the <a href="{onboarding}">private beta onboarding guide</a>
        (issue #27; same content as <code>docs/ONBOARDING.md</code> in the repo).</li>
      <li>(Optional) Add <code>.reviewgate.yml</code> to a repository.</li>
      <li>Open or update a pull request to receive the first analysis.</li>
    </ol>
    <p><a href="/feedback">Send beta feedback</a></p>
    <p><a href="/">Back to landing</a> · <a href="/privacy">Privacy</a></p>
  </main>
</body>
</html>
"""


def _feedback_html() -> str:
    onboarding = html.escape(_ONBOARDING_DOC_URL, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ReviewGate — Beta feedback</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 42rem; margin: 2rem auto;
      padding: 0 1rem; line-height: 1.5; }}
    .cta {{ display: inline-block; margin: 0.5rem 0.5rem 0.5rem 0; padding: 0.5rem 1rem;
      background: #1f6feb; color: #fff; text-decoration: none; border-radius: 6px;
      border: none; font-size: 1rem; cursor: pointer; }}
    label {{ display: block; margin-top: 0.75rem; }}
    textarea, input {{ width: 100%; max-width: 28rem; padding: 0.35rem; box-sizing: border-box; }}
    textarea {{ min-height: 8rem; }}
    footer {{ margin-top: 2rem; font-size: 0.9rem; color: #444; }}
  </style>
</head>
<body>
  <main>
    <h1>Beta feedback</h1>
    <p>Share what is working, what is confusing, or what you need next. Contact is optional.</p>
    <form id="feedback-form">
      <label>Feedback (required)
        <textarea name="message" required maxlength="8000" placeholder="Your notes…"></textarea>
      </label>
      <label>Contact (optional — email or GitHub handle)
        <input name="contact" type="text" maxlength="500" autocomplete="email"
          placeholder="you@example.com">
      </label>
      <p><button class="cta" type="submit">Submit feedback</button></p>
      <p id="feedback-status" role="status"></p>
    </form>
    <script>
    document.getElementById("feedback-form").addEventListener("submit", async (e) => {{
      e.preventDefault();
      const fd = new FormData(e.target);
      const body = {{}};
      for (const [k, v] of fd.entries()) {{
        if (v === "") continue;
        body[k] = v;
      }}
      const statusEl = document.getElementById("feedback-status");
      statusEl.textContent = "Submitting…";
      try {{
        const res = await fetch("/api/beta-feedback", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(body),
        }});
        const data = await res.json().catch(() => ({{}}));
        if (res.ok && data.ok) statusEl.textContent = "Thanks — we received your feedback.";
        else statusEl.textContent = "Request failed (" + res.status + ").";
      }} catch (err) {{
        statusEl.textContent = "Network error.";
      }}
    }});
    </script>
    <footer>
      <a href="/">Home</a>
      · <a href="/privacy">Privacy</a>
      · <a href="{onboarding}">Beta onboarding doc</a>
    </footer>
  </main>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
def landing_page() -> HTMLResponse:
    """Serve the §5.1 landing page with install and beta CTAs."""

    return HTMLResponse(content=_landing_html(AppSettings()))


@router.get("/installation-success", response_class=HTMLResponse)
def installation_success_page() -> HTMLResponse:
    """Post-install page per ``docs/DESIGN.md`` §8.1."""

    return HTMLResponse(content=_installation_success_html())


@router.get("/feedback", response_class=HTMLResponse)
def beta_feedback_page() -> HTMLResponse:
    """Hosted beta feedback form (issue #55)."""

    return HTMLResponse(content=_feedback_html())
