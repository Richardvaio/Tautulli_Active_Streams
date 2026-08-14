# Tautulli Active Streams card API v1

This document defines the private contract between the Home Assistant integration
and the separately released dashboard card. The browser communicates only with
Home Assistant. It never connects directly to Tautulli or Plex and never receives
their credentials or raw payloads.

## Compatibility

- Every response includes `schema_version`.
- The card must reject unsupported schema versions with a clear editor/card error.
- `capabilities` controls which card modes and actions are available.
- New optional fields may be added within schema v1. Existing field meanings must
  not change until a new schema version is introduced.

## Privacy boundary

The integration serializes an explicit allowlist. The API must never return API
keys, Plex tokens, raw upstream image paths, email addresses, IP addresses,
machine identifiers, user agents, shared-library permissions, filesystem paths,
or complete upstream objects. Display privacy is enforced by the backend before
the payload is sent; frontend hiding is not a security control.

## Common envelope

```json
{
  "schema_version": 1,
  "entry_id": "Home Assistant config entry ID",
  "server": {"id": "Plex server ID", "name": "Display name"},
  "generated_at": "2026-08-13T22:00:00+00:00",
  "stale": false,
  "capabilities": {},
  "items": []
}
```

## Commands

### `tautulli_active_streams/get_entries`

Returns loaded entries and their capabilities. This is used by the card editor's
server selector and compatibility checks.

### `tautulli_active_streams/subscribe_active_streams`

Input: `entry_id`.

Sends an initial event followed by coordinator-driven updates. Each active item
contains stable identity, display-safe user/media/client/quality fields, numeric
playback timing, and signed same-origin image URLs.

Additional schema-v1 commands:

- `get_recently_added`
- `get_home_stats`
- `get_users`
- `get_libraries`
- `get_history`
- `get_user_stats`
- `terminate_session`

All list commands are entry-scoped, bounded, and paginated where appropriate.
Large lists are not placed in entity state attributes or Home Assistant Recorder.
History and termination require an administrator. Integration options can disable
history, names, client details, and card termination at the serialization boundary.
Termination is disabled by default and always targets one entry and session.

## Stable identifiers

- Server: Plex `pms_identifier`.
- Active stream: server ID plus `session_id`, falling back to `session_key`.
- User: server ID plus Plex `user_id`.
- Media: server ID plus Plex `rating_key`; GUID/external IDs may supplement it.
- Library: server ID plus Plex `section_id`.
- History: server ID plus ungrouped Tautulli history row ID.

Display names and titles are never identifiers.
