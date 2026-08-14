![Tautulli Active Streams](https://github.com/user-attachments/assets/c3fc7c90-b1a4-4c4a-bfa0-e3542f68286f)

# Tautulli Active Streams

A Home Assistant custom integration for monitoring active Plex streams and viewing Tautulli watch-history statistics. It supports multiple Tautulli servers, optional Plex metadata enrichment, privacy controls, administrator-protected stream actions, and the companion Tautulli Active Streams Card.

## Features

- Live Plex session entities with playback state, progress, media, client and quality information.
- Movie, television, live-video and music stream support.
- Stable per-user statistics based on Plex user IDs.
- Rolling, calendar-month and custom monthly reporting periods.
- Optional Plex metadata enrichment for summaries, ratings, genres, credits and external IDs.
- Optional IP geolocation with explicit privacy controls.
- Entry-scoped stream termination actions with structured results.
- Secure signed artwork proxy; Tautulli and Plex credentials are never exposed to dashboards.
- Versioned, authenticated card API for active streams, recently added media, rankings, user activity and watch history.
- Multiple Tautulli instances supported.

## Installation

### HACS

1. Open **HACS** in Home Assistant.
2. Open the menu in the top-right corner and choose **Custom repositories**.
3. Add `https://github.com/Richardvaio/Tautulli_Active_Streams` as an **Integration** repository.
4. Download **Tautulli Active Streams**.
5. Restart Home Assistant.
6. Open **Settings → Devices & services → Add integration** and search for **Tautulli Active Streams**.

### Manual installation

Copy `custom_components/tautulli_active_streams` into the matching directory under your Home Assistant configuration folder, restart Home Assistant, and add the integration from **Settings → Devices & services**.

## Initial setup

Enter the Tautulli URL and API key. The integration tests the connection before saving it, then presents only the optional setup pages required by the features you enable.

The API key is a sensitive Tautulli administrator credential. Keep it private and connect over a trusted local network or HTTPS. It is stored in the Home Assistant config entry and is never sent to dashboard clients.

### Optional Plex enrichment

Plex access is optional. When enabled, the Plex URL and token are validated before being saved. Existing tokens are never shown in configuration forms. Disabling Plex enrichment requires confirmation and removes the saved Plex credentials.

## Configuration

Open **Settings → Devices & services → Tautulli Active Streams** and select **Configure**.

- **General settings** controls active-stream and statistics polling.
- **Watch-history statistics** controls reporting periods and user statistics.
- **Location and privacy** controls geolocation and exposed location detail.
- **Dashboard card access** controls names, client details, history and card termination permissions.
- **Plex metadata enrichment** manages the optional Plex connection.

Use **Reconfigure** to change the friendly name, Tautulli URL or SSL verification. If Tautulli rejects the saved API key, Home Assistant starts a reauthentication flow for a replacement key.

## Statistics periods

- **Rolling period** includes the previous 1–365 days.
- **Calendar month** starts on the first day of the current month.
- **Custom monthly cycle** starts on a selected day from 1–31; shorter months safely use their final day.

Changing the period does not delete or reset Tautulli history. Users with no activity in the selected current period remain available and report zero values.

## Privacy and recorder behaviour

Detailed IP addresses, postal codes and coordinates are disabled by default. The Tautulli geolocation provider uses Tautulli's local GeoLite2 database. Selecting `ip-api.com` sends public stream IP addresses to that external service.

Card responses use explicit server-side allowlists and never include API keys, Plex tokens, raw upstream image paths, email addresses, IP addresses, machine identifiers, user agents or filesystem paths. Card history and stream termination are administrator-only and can also be disabled in the integration options.

High-churn stream attributes and derived statistics attributes are excluded from Recorder. Their current values remain available to dashboards and automations without filling the database with frequent attribute snapshots.

## Tautulli Active Streams Card

The native companion card replaces the legacy multi-card YAML stack with one responsive card and a guided visual editor. It supports active streams, recently added media, popular titles, user activity and watch history.

Install it separately through HACS as a **Dashboard** repository:

`https://github.com/Richardvaio/tautulli-active-streams-card`

The card requires integration version **2.7.0 or newer** and card API schema `1`. The integration must be installed and restarted before adding the card.

Legacy movie/TV and music YAML examples remain in this repository for existing dashboards. They require `auto-entities`, `button-card`, `bar-card` and `card-mod`.

## Stream actions

Actions are scoped to a selected Tautulli config entry. User actions prefer the stable Plex `user_id`; display-name matching remains available as a compatibility fallback. User-triggered actions require a Home Assistant administrator.

### Terminate all active streams

```yaml
action: tautulli_active_streams.kill_all_streams
data:
  config_entry_id: "YOUR_CONFIG_ENTRY_ID"
  message: "Your message here"
```

### Terminate streams for one Plex user

```yaml
action: tautulli_active_streams.kill_user_streams
data:
  config_entry_id: "YOUR_CONFIG_ENTRY_ID"
  user_id: "12345678"
  message: "Your message here"
```

### Terminate one session

```yaml
action: tautulli_active_streams.kill_session_stream
data:
  config_entry_id: "YOUR_CONFIG_ENTRY_ID"
  session_id: "SESSION_ID"
  message: "Your message here"
```

## Troubleshooting

- Confirm the Tautulli URL is reachable from Home Assistant.
- Use **Reconfigure** when the address or SSL setting changes.
- Complete the **Reauthenticate** repair when the API key is rejected.
- Restart Home Assistant after installing or upgrading through HACS.
- Check **Settings → System → Logs** for `tautulli_active_streams` messages.
- Open an issue with privacy-safe diagnostics if the problem continues.

## Support and contributing

- [Issue tracker](https://github.com/Richardvaio/Tautulli_Active_Streams/issues)
- [Home Assistant community discussion](https://community.home-assistant.io/t/custom-component-tautulli-active-streams)

Pull requests and issue reports are welcome.

## License

This project is licensed under the [MIT License](LICENSE).
