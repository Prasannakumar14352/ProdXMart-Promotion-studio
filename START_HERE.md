# ProdXMart Promotion Studio

## Open the demo on Windows

1. Extract the complete ZIP into a writable folder on your computer. Install 64-bit Python 3.11 or 3.12 if needed.
2. Double-click **Start_Windows.bat**. The first run downloads Pillow and imageio-ffmpeg into a project-specific environment. It uses system FFmpeg when available, otherwise the packaged executable. Keep the terminal open while using the studio.
3. Open **Demo catalogue → Generate weekly batch**. Play a Reel, download its MP4, or skip it and generate again to see the next eligible product. Three drafts fit the default weekly batch. The fourth sample is a replacement. One additional sample is intentionally low on stock.
4. Choose the first posting time in India time and press **Approve all drafts**. The batch is spaced two days apart. Use **Refresh data** to see status changes. When a demo slot arrives, it becomes Simulated. You can also play the included preview/D02.mp4 without installing Python.

The studio opens in My store. Use the workspace selector to try the demo. Demo product illustrations and prices are fictional. Store orders, sales and queues are kept separate. Videos use on-screen Telugu text and have no audio track.

## Add products inside the studio

Open **My store → Products → Add product**. Paste the product page link, enter the name, SKU, category, selling price, cost, fees and current stock. Upload JPG, PNG or WebP photos or paste direct public HTTPS image URLs. Photos are cached locally for future Reels. Use up to six photos of up to 5 MB each. Some supplier hosts require a signed-in session; use the upload option for those images. Only tick the promotion permission box when you can use the photos. The Telugu feature is optional.

**Save product** adds the item to your catalogue. The readiness column explains which products meet the stock, margin and media requirements. Use **Edit** to change details, remove photos, or reconfirm a product’s price and stock. Saving a product refreshes only its own verification time. Products can be saved before their photos are ready. Use **Generate weekly batch** once at least one product is eligible. Existing scheduled items check the current product before publishing.

## Optional CSV import

Use **My store** in the workspace selector. The empty CSV templates are in **store/**. Save all CSVs as UTF-8. Use decimal amounts without currency signs or thousands separators. Use a current, complete snapshot each time. Replace the old file instead of appending an overlapping export. A malformed snapshot is reported in the dashboard. No Factori password or paid AI API key is required. Store data and credentials stay local except for media and captions explicitly published using the Instagram integration.

Put the images you can use for promotion in **media/**. In store/catalogue.csv supply one row per SKU or independently stocked variant. Relative paths can include subfolders. This release takes local JPG, PNG and other Pillow-supported image files. It does not download remote supplier images. Copy approved assets into media once and reuse them.

| Column | Value |
| --- | --- |
| sku, name, category | Unique SKU, customer-facing name, category |
| price | Current storefront price, used on the Reel |
| cost, fees | Per-unit supplier cost and other direct costs. For a price including tax, include any net tax liability for that unit in fees so the margin uses a consistent basis. |
| stock | Current quantity available |
| feature_te | Optional verified feature in Telugu. A blank value uses a generic invitation to view the product page. |
| product_url | HTTPS product URL on prodxmart.com or www.prodxmart.com |
| photos | Photo filenames separated by a vertical bar, for example front.jpg&#124;inside.jpg |
| media_ok | Set to yes for images you are allowed to use in social promotions |

**settings.json** controls minimum stock (3), minimum margin (₹100), max catalogue age (24 hours), number of drafts per ISO week (3), and optional category. These are editable starting values. Catalogue header names can be mapped to actual export headers, e.g. `"catalogue_columns":{"sku":"Product Code","price":"Retail Price"}`. Other columns retain the template names. Inspect cost, tax and quantity definitions when mapping a new export.

Stock rules use the CSV snapshot. For form edits, the app checks the product verification time when scheduling or publishing. Rows from an external export without a verification timestamp use the snapshot modification time. Import a genuinely refreshed export after a stock change. Touching or copying old data gives it a misleading timestamp. There is no live Factori feed in this release. Price, feature, photo or URL changes require a fresh draft. Falling below stock or margin thresholds, removed rows or withdrawn media permission hold the post. For a held row, correct the catalogue and generate the replacement batch. The selection rule prefers eligible products featured least recently and breaks ties by unit margin and stock. Net margin per unit here excludes promotion and overhead.

## Track sales and link traffic

- **orders.csv** contains one row per delivered order line. Use a stable order ID + line ID; update it for returns. Enter sales after discounts excluding sales tax, refunds on the same basis, total product cost after supplier credits, and total direct costs for that line. Allocate order-wide fees once across lines. The campaign field is optional and should be the tracked utm_campaign if present in the order data.
- **expenses.csv** records separate advertising, platform fees and other operating expenses. One unique reference per expense. Enter costs on only one sheet. Estimated profit equals sales after refunds less net product cost, direct costs and recorded expenses. Totals cover all imported history, before income tax. Bank payouts can be reconciled in the separate sales workbook.
- **traffic.csv** optionally accepts one cumulative session total per campaign from your analytics report. Missing traffic displays as a dash. The report never infers a sale from a visit. Each Reel has a product link with a unique campaign ID.
- After approving, choose **Export shop links** for a portable shop-the-Reels HTML page. Upload it to a public location you control and put that URL in your Instagram bio, or copy its links to an existing bio page. Re-export and replace the page after changes. The localhost dashboard is private and unsuitable as a bio link.

## Run weekly with minimal attention

On Windows use Task Scheduler → Create Basic Task → Weekly. Set the program to the extracted folder’s **.venv/Scripts/python.exe**, arguments to **studio.py --generate store**, and “Start in” to the extracted folder. This fills missing drafts for the week. Approve the batch in the studio. While the studio is open it checks due items every 30 seconds. Alternatively schedule **studio.py --due** every few minutes under the same Windows user. The PC must be awake for local scheduled work. Late items are checked against the current snapshot before being processed. Keep data/studio.sqlite and data/reels together when backing up. For Linux or macOS, use a Python virtual environment, install requirements.txt and run python studio.py. The browser will open the local dashboard on port 8765. Override with --port when needed.

## Optional Instagram publishing

This adapter uses the Instagram API with **Facebook Login for Business** for a linked professional Instagram account and Facebook Page. Create the appropriate Meta app and obtain the relevant permissions and a Page token according to Meta’s current requirements. Supply your own supported Graph version. Configure these environment variables for the Windows user/process that runs the worker, then restart it.

| Variable | Meaning |
| --- | --- |
| PRODX_IG_ID | Numeric Instagram professional account ID |
| PRODX_GRAPH_VERSION | Supported Graph version for your Meta app, in vN.N format |
| PRODX_IG_TOKEN | Authorized Page access token. Keep this out of CSVs and source control. |
| PRODX_LIVE | Set to 1 only when you want approved real posts to publish. Leaving it unset keeps the real queue offline. |

The worker sends the local MP4 directly to Meta’s resumable upload endpoint, checks processing status, rechecks the catalogue, and submits the container once. Demo posts always simulate. On an ambiguous publish or interrupted process, inspect the account and the container before taking action. Stop other workers first. Run **python studio.py --resolve JOB_ID --outcome published --media-id NUMERIC_ID** to record a confirmed publication, or **python studio.py --resolve JOB_ID --outcome unpublished** to return a confirmed unpublished item to Draft. The job ID is the downloaded MP4 filename without the extension. A delayed queue may become due together; review schedules before enabling live mode.

Meta’s official guide: https://developers.facebook.com/documentation/instagram-platform/content-publishing

## Implementation

engine.py handles catalogue mapping, eligibility, batch scheduling and reporting. creative.py renders 15-second vertical MP4s. publisher.py handles due jobs and Meta uploads. studio.py serves the review dashboard on the loopback interface. Enter catalogue products in the app or use a CSV. Order, expense and analytics snapshots currently enter through CSV. No external account was connected during development. The local workflow and a simulated publisher were tested; the live Meta account path must be verified with your own first post. For supplier assets, wait for the applicable media permission before marking media_ok. The app does not place ads or purchase products.

## Pull prices from Factori My Products

1. Start Studio on its default port (8765). In Chrome on the same PC, open `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and choose the repository's **factori-bridge** folder. This is a one-time setup.
2. Sign in at [Factori My Products](https://retailing.factori.com/dashboard/products) and refresh the page. Click **Import to ProdXMart Studio** on the product you want. The extension reads that card from the signed-in page and opens a local Studio tab.
3. Review **Customer pays**, **Factori price** (including GST when shown on the card), and **Profit per sale** when available. Select the matching Studio product or **Add as a new product**. Click **Use these prices**, review the editor and **Save product**. Existing products keep their fees and stock values unless you edit them. For a new item, verify the product name, link, SKU, category, fees, stock and photo permission.
4. Repeat the import when a supplier price changes. The importer only reads a product you select. Factori's catalogue MRP and displayed profit do not populate unit cost or direct fees.
