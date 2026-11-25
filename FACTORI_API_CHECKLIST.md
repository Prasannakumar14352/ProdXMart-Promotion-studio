# Factori Official API Integration Checklist

Status: **Pending** (Official Factori API access/credentials are not required and not yet supplied; Factori Bridge Chrome extension + CSV import remain active).

- [ ] API documentation received and reviewed
- [ ] Authentication & token refresh flow verified
- [ ] Product catalogue endpoint mapped
- [ ] Pagination cursor/page parameters verified (no hardcoded page limits)
- [ ] Authorized product images array mapped (preserving order)
- [ ] Stock availability field verified (distinguishing `0` vs unknown)
- [ ] Supplier cost & tax-inclusive pricing semantics verified
- [ ] Selling price field verified
- [ ] Orders endpoint mapped
- [ ] Returns & refunds endpoint mapped
- [ ] Bank settlements endpoint mapped (reconciled separately from revenue)
- [ ] Webhooks / change notifications configured
- [ ] Rate limits & backoff policy confirmed
- [ ] Commercial media & promotion usage permissions confirmed
