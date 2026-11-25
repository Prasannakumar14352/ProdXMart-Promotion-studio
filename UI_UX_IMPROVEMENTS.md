# ProdXMart Promotion Studio — UI/UX & Functional Improvements Log

1. **Rendered Overview Fixes (`js/overview.js`, `css/components.css`, `css/layout.css`, `style.css`)**:
   - Fixed the `el('strong', '...')` argument-order bug so empty-state headings and guidance text display properly in `Campaign performance`, `Recommended products`, `AI agent activity`, `Products requiring attention`, and `Upcoming promotions`.
   - Balanced the Overview KPI strip to a symmetric **6-card layout** (6×1 on `≥1320px`, 3×2 on tablet/compact laptops, 2×3 on mobile) with zero ragged rows.
   - Wrapped Overview `Recommended products` table in `.table-scroll` and capped long product titles cleanly with `title` hover tooltips so `Price`, `Margin`, and `Promote` always stay unclipped.
   - Fixed `.global-search input.global-search-input` padding specificity so the search icon never overlaps `"Search products..."`.
   - Hid `#mobile-menu-btn` above `820px` so desktop topbars never show a redundant menu button alongside the sidebar toggle.

2. **Global Button Standardization (`css/components.css`, `index.html`, `style.css`)**:
   - Standardized heights (`36px` regular / `30px` `.btn-sm` inside tables & cards), border radius (`6px`), flex alignment, and action-link buttons (`a.btn`, `.cardactions a`, `.agent-table-actions a`).
   - Added distinct `.btn-warning-outline` (`Request Changes`) and `.btn-danger-outline` (`Reject`) styles alongside `.btn-primary` (`Approve`).
   - Hid raw file input controls inside `.picker` buttons (`Import .xlsx / .csv` in Analytics and `Choose photos` in Product Editor).
   - Fixed `.agent-panel-head` wrapping so top-right action bars remain pinned top-right on desktop (`>900px`) and wrap cleanly below on smaller widths.

3. **Navigation, Heading, Route Persistence & Editor Safety (`ui.js`, `js/ui_core.js`, `editor.js`)**:
   - Preserved exact sub-route page titles during `refresh()` and scoped `#add` (`Add product`) and `#generate` (`Generate weekly batch`) to relevant screens only.
   - Added URL `?route=...&mode=...` synchronization so browser refresh preserves the active screen and workspace (`Demo Catalogue` vs `My Store`).
   - Triggered `refreshAgentViews` when switching workspaces so `Integrations` and `Settings` reflect workspace changes immediately.
   - Added `formDirty` unsaved-changes confirmation guard in `#editor` plus margin `%` visibility in the Catalogue table.

4. **Workflow Enhancements (`agents/social_agent.py`, `studio.py`, `agents.js`, `js/phase4_studio.js`, `agents/sales_analytics.py`, `js/phase6_analytics.js`)**:
   - Replaced native `window.prompt` in Approval Center with an accessible styled `#reason-dialog` modal.
   - Implemented distinct `Request Changes` action (`POST /agents/social/request-changes`) moving posts back to `Draft` with mandatory reviewer instructions.
   - Added `title` tooltips explaining disabled states (`Run`, `Stop`, `Retry`, `Approve Review`, `Approve`, `Schedule`) and duplicate-click locks on QA Approve/Schedule.
   - Added **Duplicate Campaign** quick action in Campaign Management (`js/phase4_studio.js`).
   - Added exact mathematical **Max CPA & Break-even ROAS** (`Price / Unit Margin`) per SKU and quick **Orders / Expenses CSV** exports in Sales Analytics (`agents/sales_analytics.py`, `js/phase6_analytics.js`).
