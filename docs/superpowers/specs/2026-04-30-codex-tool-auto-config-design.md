# Codex Tool Auto-Config Design

Date: 2026-04-30

## Background

The original request was to add a user-facing tab that teaches users how to download and configure tools such as Codex and Claude. After discussion, the first version is narrowed to a more useful workflow: a Codex one-click setup page that lets users download a local setup script.

The script should not contain the user's API key. Instead, it should use a short-lived installation token to fetch installation and configuration data from the server at runtime.

## First Version Scope

Build one new user-facing page for Codex setup.

The page should:

- Add a main user navigation entry such as "Tool Setup" or "工具配置".
- Show an internal tab structure that starts with "Codex".
- Let the user download a Codex setup script.
- Keep room for future tools such as Claude, but do not implement them in the first version.

The setup script should:

- Detect whether Codex is already installed locally.
- If Codex is installed, write or update the local Codex configuration.
- If Codex is not installed, install Codex first, then write the configuration.
- Fetch installation and configuration data from the server.
- Verify the installation and configuration at the end when possible.

## API Key Strategy

The API key must be delivered by the server at script runtime, not embedded in the downloaded script.

Server behavior:

- If the user already has an available API key, automatically use the first available key.
- If the user does not have an available API key, automatically create one.
- The user does not need to manually choose a key in the first version.

Recommended generated key name when auto-created:

- `Codex Auto Setup`

## Installation Token Flow

The downloaded script should contain only:

- The server base URL.
- A short-lived one-time installation token.
- The selected tool name, initially `codex`.
- Optional platform metadata.

Recommended flow:

1. User opens the Codex setup page.
2. User clicks "Download setup script".
3. Server creates an installation token bound to the user and tool.
4. Browser downloads a script generated from a platform-specific template.
5. User runs the script locally.
6. Script calls the server with the installation token.
7. Server validates the token.
8. Server finds the user's first available API key or creates one.
9. Server returns an installation manifest containing install and config data.
10. Script installs Codex if needed.
11. Script writes the Codex configuration.
12. Server marks the token as used.

Token constraints:

- Bound to `user_id`.
- Bound to `tool = codex`.
- Short expiration time.
- Single use.
- Not reusable after successful config fetch.
- Invalidated on expiration or use.

## Platform Model

The core backend flow is platform-independent.

Platform-specific differences should live in script templates and local configuration logic:

- Windows: PowerShell script.
- macOS/Linux: shell script.
- Local installation detection command.
- Local install command.
- Local config file path.
- Local environment variable or config-file write behavior.

The page can auto-detect the user's platform and provide the matching script. It should still allow manual platform switching if detection is wrong.

## Installation Manifest

The server should return a manifest rather than hard-coding all behavior into the downloaded script. This keeps future platforms and tools easier to add.

Example shape:

```json
{
  "tool": "codex",
  "platform": "windows",
  "install": {
    "strategy": "official_then_mirror",
    "official_command": "...",
    "mirror_url": "..."
  },
  "config": {
    "base_url": "https://example.com/v1",
    "api_key": "sk-...",
    "default_model": "..."
  },
  "verification": {
    "command": "codex --version"
  }
}
```

The exact Codex config path and file format should be confirmed during implementation against the target Codex client version.

## Install Source Strategy

Preferred strategy:

- Try the official installation method first.
- If the official source fails or is unavailable, fall back to a server-hosted mirror.

This keeps installation current while still giving the service owner a reliability fallback.

## Security Requirements

- Never embed the real API key in the downloaded script.
- Use HTTPS for all script-to-server communication.
- Make installation tokens short-lived and single-use.
- Return API key data only after token validation.
- Do not log full API keys or installation tokens.
- Consider recording token creation, redemption, expiration, and failure reason for audit.
- Avoid exposing another user's API key by ensuring the token is strictly bound to the authenticated user that generated it.

## Suggested Backend Pieces

- Installation token model/table.
- Endpoint to generate/download a setup script.
- Endpoint for the local script to redeem the token and fetch the install manifest.
- Service logic to find the first available API key or create one.
- Audit logging for token lifecycle.

Potential endpoint names:

- `POST /api/tool-setup/codex/script`
- `GET /api/tool-setup/config?token=...`

The final route names should follow existing backend API conventions.

## Suggested Frontend Pieces

- New user route, for example `/tool-setup`.
- Sidebar navigation item, for example "工具配置".
- Page-level tabs, initially only `Codex`.
- Platform selector or platform auto-detection.
- Download button for the setup script.
- Short verification instructions after download.

## Open Questions

- Exact Codex installation method for each platform.
- Exact Codex configuration path and format.
- Token expiration duration.
- Whether token redemption should mark the token as used before or after the script confirms local configuration success.
- Whether server-hosted mirror packages are available in the first version or added later.

