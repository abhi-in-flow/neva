# Operations Readiness — Exploration Agent

## Goal

Produce a no-surprises Windows operations checklist for phone access and audio
processing. This is read-only exploration.

## Inspect

- current Docker and PowerShell assumptions;
- availability and invocation of `cloudflared`;
- named-tunnel requirements and safe fallback behavior;
- availability of `ffmpeg` and FLAC conversion support;
- Windows Firewall and bind-address implications;
- hotspot fallback and stable QR URL requirements.

## Constraints

Do not create tunnels, mutate DNS, open firewall rules, install software, read
secrets, or edit repository files. Distinguish repository work from manual
account/device actions.

## Deliverable

Return:

1. detected capabilities and missing prerequisites;
2. exact PowerShell verification commands;
3. the shortest local-to-phone test sequence;
4. fallback sequence if venue Wi-Fi fails;
5. any action requiring explicit user approval.
