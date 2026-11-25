# ProdXMart Promotion Studio — UI Design System

## Color Tokens (`css/tokens.css`)
| Token | Light Theme | Dark Theme | Usage |
| --- | --- | --- | --- |
| `--brand-primary` | `#123E3B` | `#0E2D2B` | Sidebar background & brand headers |
| `--brand-action` | `#145F53` | `#10B981` | Primary CTAs & active highlights |
| `--brand-accent` | `#10B981` | `#34D399` | Positive trends & active indicators |
| `--bg-app` | `#F6F7F3` | `#0F1716` | Application canvas |
| `--bg-surface` | `#FFFFFF` | `#162220` | Cards, panels, tables, sticky header |
| `--text-primary` | `#173B37` | `#ECFDF5` | Headings and primary copy |
| `--text-secondary` | `#64748B` | `#94A3B8` | Labels, metadata, captions |
| `--border` | `#E2E8E4` | `#253835` | Card and table dividers |
| `--success` | `#16A34A` | `#22C55E` | Ready / Published / QA Passed |
| `--warning` | `#F59E0B` | `#FBBF24` | Pending Approval / Low Stock |
| `--error` | `#DC2626` | `#F87171` | Failed / Blocked / Stop Active |
| `--info` | `#2563EB` | `#60A5FA` | Scheduled / Running / Queued |

## Typography & Spacing
- Font stack: `'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif`
- Scale: `12px` (meta/badges), `13px` (table/controls), `14px` (body), `16px` (card titles), `20px` (section headings), `26px–30px` (KPI values & page titles).
- Spacing: 4px / 8px grid (`4, 8, 12, 16, 20, 24, 32`).
- Radius: `6px` (inputs/buttons), `8px–12px` (cards/dialogs), `999px` (pills).
- Icons: Lucide (`assets/vendor/lucide.min.js`) — no emoji navigation icons.
- Charts: Chart.js (`assets/vendor/chart.umd.min.js`).
