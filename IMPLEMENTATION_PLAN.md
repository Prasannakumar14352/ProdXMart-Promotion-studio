# ProdXMart Promotion Studio — Implementation Plan

Branch: `feature/premium-ui-redesign` (branched from `feature/ai-agent-center` commit `1a6410f`, preserving `main` untouched).

## Baseline Inventory
- **Backend**: `studio.py` (ThreadingHTTPServer on `127.0.0.1:8765`), `engine.py` (CSV + SQLite `data/studio.sqlite`), `product_editor.py`, `creative.py`, `soundtrack.py`, `publisher.py`.
- **AI Agents (`agents/`)**: `MasterOrchestrator`, `CatalogAgent`, `CatalogueAuditorAgent`, `ReelCopyStrategistAgent`, `PerformanceAnalystAgent`, `MarketingAgent`, `VideoProductionAgent`, `QualityAssuranceAgent`, `SocialMediaAgent`, `SecureCredentialStore`, `OllamaClient`.
- **Browser Extension (`factori-bridge/`)**: Multi-page Factori product scanner and storefront gallery reader (`1.4.0`).
- **Frontend**: `index.html`, `style.css`, `ui.js`, `editor.js`, `agents.js` + vendored MIT `assets/vendor/lucide.min.js` and `assets/vendor/chart.umd.min.js`.
- **Baseline Tests**: 35 Python tests + 11 Node Factori tests passing prior to redesign.

## Sequential Phases
1. **Phase 1 — Layout, Design System & Business Overview Dashboard**: Design tokens, dark/light theme support, collapsible sidebar (12 routes), sticky header (breadcrumb, global search, workspace switcher, refresh, notifications, theme, settings), reusable UI components (toasts, confirm dialogs, skeletons, empty states), `/api/overview` endpoint, and Business Overview Dashboard.
2. **Phase 2 — Product Catalogue**: Summary KPI strip, multi-criteria filtering (search, category, stock, readiness, margin), grid/table toggle, sorting, pagination, bulk selection/promotion, and upgraded Product Editor drawer (multi-photo reorder, cover selection, gallery management).
3. **Phase 3 — AI Agent Command Center, Integrations, Execution History & Settings**: Visual workflow pipeline, 8 agent cards (including honest Not Configured / Fallback states), Integrations Center with Factori API checklist, dedicated Execution History & Logs drawer/export, and organized 7-tab Settings screen.
4. **Phase 4 — Campaign Management & Professional Video Promotion Studio**: 6-step Campaign Builder wizard, campaign cards & strict state machine, 3-panel + timeline Video Studio (media/storyboard, 9:16 preview player, voiceover/music/ducking/template config, real timeline tracks).
5. **Phase 5 — Approval Center & Publishing Calendar**: Dedicated human review workspace (price/stock/QA validation, rejection reasons, invalidation warnings) and Monthly/Weekly/Daily IST Publishing Calendar with manual packages and global pause control.
6. **Phase 6 — Sales Analytics & Expenses/Profit Management**: Multi-filter analytics dashboard with Chart.js visualizations, SKU profitability table, Expenses & Profit ledger (avoiding double-counting, excluding bank settlements from revenue), and Excel/CSV import & export.
7. **Phase 7 — End-to-End Integration, Polish & Full Regression**: Cross-screen verification in live Chrome browser across `1440x900` and `1920x1080`, Demo vs My Store isolation checks, and complete automated test suite pass.
