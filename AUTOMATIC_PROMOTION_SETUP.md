# Automatic product promotion — first implementation

This branch implements automatic preparation, not live multi-platform publishing.

1. In My Store, add a product with verified stock, positive margin, authorized photos, and valid price/cost.
2. Leave Automatically prepare a promotional video after saving selected. Choose English or Telugu, 15/30/60 seconds, and channels. New products default to English, 30s and all three channels. On edits, explicitly check the option to queue preparation. Existing matching jobs are returned instead of enqueued again.
3. In AI Agent Center, monitor Master Orchestrator. The specific SKU is checked again at each stage; marketing uses grounded templates if Ollama fails. Explicit campaign and production IDs connect marketing, video, QA and platform drafts. Narration is required; configure a licensed/approved TTS provider if unavailable. Failed QA stops the chain. Human QA and social approval are still required.
4. For new bulk-imported products, choose Settings > AI & Ollama > Bulk import automation > Automatically prepare newly imported eligible products. Bulk imports are otherwise manual. Ineligible products are logged and held. Edit and save with promotion selected once corrected.
5. Settings > Social Media Accounts contains the existing encrypted credential form. Integrations and Approval Center link there. Credential presence is not verified connectivity. Use Meta Developers and Google Cloud to obtain authorized credentials; no account passwords.
6. In Settings > AI & Ollama set a 300-second request timeout if cold startup is slow, and test the local model. The client now uses the configured socket timeout instead of 1.5 seconds. Cancellation is checked between chunks; a blocked read can take up to the configured timeout.

## Pending
- Implement and test official live upload transports, OAuth refresh, account identity/permission checks, account-specific approval binding, durable publication claims and scheduled publishing with safe unknown-outcome reconciliation. The agent approval flow still exports manual packages. Legacy Instagram publishing is unchanged.
- Run full regression and Windows browser/render tests with a real eligible product, approved TTS and installed Ollama. Test recovery after restart; an interrupted job is marked failed by existing storage and can be retried. A retry may create a replacement draft/video; it never publishes. No Factori API needed.
- No actual accounts have been connected, no live posts sent, and no advertising spend enabled.

Suggested local tests: python -m unittest test_product_promotion_workflow test_agents test_catalog_agent test_marketing_agent test_video_qa test_social_approval
