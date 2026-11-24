# ProdXMart Promotion Studio

A local Python app for managing a ProdXMart catalogue, preparing Telugu product Reels, scheduling approved posts and tracking sales.

## Start on Windows

1. Install Python 3.11 or 3.12, then clone or download and extract this repository.
2. Double-click **Start_Windows.bat**. The launcher installs dependencies on its first run and opens the local dashboard.
3. In **My store → Products**, choose **Add product**. Paste the product page link and enter its name, SKU, category, price, unit cost, fees and stock.
4. Upload photos or paste direct HTTPS image links, set the promotion permission, and save. Review the readiness column, then choose **Generate weekly batch**.
5. In **Reel queue**, preview or save the MP4s. Approval queues the Reels for the times shown in India time. Live publishing requires your own Meta configuration.

Select **Demo catalogue** to try it with fictional stationery and sample MP4s. Generating drafts, managing products and testing the demo require no paid AI API or Factori connection.

## Start from a terminal

On macOS or Linux:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python studio.py
```

The dashboard opens at http://127.0.0.1:8765. Keep the process running for scheduled work. This local owner dashboard is designed for one computer.

## Data and integration

The first run prepares private store files from the headers in `templates/`. Product edits persist locally in `store/catalogue.csv`, photographs in `media/`, and the Reel queue and generated media in `data/`. These folders are excluded from Git. Moving an existing installation preserves your data if you copy those folders and your settings. Existing catalogue imports remain supported.

Stock, cost and media permission come from your entries or exports. Saving a product confirms its stock and price as of that edit. Stock and pricing are checked against local data before posting. The sales report reads snapshots for orders, expenses and traffic. Confirm supplier media rights and available export formats with Factori.

[Full setup, catalogue fields, scheduling and publishing](START_HERE.md)
