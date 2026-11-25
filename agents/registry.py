"""Built-in Phase 1 specialist agents for ProdXMart Promotion Studio."""

from __future__ import annotations

from collections import Counter
from contextlib import closing
import json
from typing import Any

import engine
from agents.base import (
    AgentContext,
    AgentMetadata,
    AgentResult,
    BaseAgent,
)
from agents.catalog_agent import CatalogAgent
from agents.marketing_agent import MarketingAgent
from agents.qa_agent import QualityAssuranceAgent
from agents.social_agent import SocialMediaAgent
from agents.video_agent import VideoProductionAgent


class CatalogueAuditorAgent(BaseAgent):
    """Audits local store or demo catalogue readiness using real CSV/SQLite state."""

    metadata = AgentMetadata(
        agent_id='catalogue_auditor',
        name='Catalogue Auditor',
        role='specialist',
        description='Inspects stock, unit margins, photo completeness, media permissions and catalogue freshness, then asks local Ollama for grounded remediation priorities.',
        capabilities=('read_catalogue', 'eligibility_audit', 'ollama_analysis'),
        default_enabled=True,
        requires_ollama=True,
        can_publish=False,
        can_spend=False,
    )

    def execute(self, ctx: AgentContext) -> AgentResult:
        ctx.progress(15, f'Reading {ctx.mode} catalogue...')
        cfg = engine.config()
        items = engine.catalogue(ctx.mode)
        ctx.check_cancelled()

        ready = [p for p in items.values() if not p['issues']]
        blocked = [p for p in items.values() if p['issues']]
        issue_counts = Counter(issue for p in blocked for issue in p['issues'])

        ready_sorted = sorted(ready, key=lambda p: (-p['margin'], -p['stock'], p['sku']))
        top_ready = [
            {
                'sku': p['sku'],
                'name': p['name'],
                'category': p['category'],
                'price': float(p['price']),
                'margin': float(p['margin']),
                'stock': p['stock'],
                'photo_count': len(p['files']),
            }
            for p in ready_sorted[:8]
        ]
        blocked_items = [
            {
                'sku': p['sku'],
                'name': p['name'],
                'stock': p['stock'],
                'margin': float(p['margin']),
                'issues': list(p['issues']),
            }
            for p in blocked[:12]
        ]

        metrics = {
            'mode': ctx.mode,
            'total_products': len(items),
            'ready_count': len(ready),
            'needs_attention_count': len(blocked),
            'weekly_slots': cfg['weekly_slots'],
            'minimum_stock': cfg['minimum_stock'],
            'issues_breakdown': dict(issue_counts),
        }
        ctx.log('INFO', f'Audited {len(items)} products ({len(ready)} ready, {len(blocked)} needing attention).')
        ctx.progress(45, f'Analyzing readiness with {ctx.model}...')

        prompt = (
            'You are the ProdXMart Catalogue Auditor. Analyze ONLY the real local catalogue snapshot below. '
            'Do not invent products, prices, or metrics. Give concise, prioritized actions to make blocked SKUs Reel-ready '
            'and identify the strongest ready SKUs for the upcoming weekly batch.\n\n'
            f'Snapshot JSON:\n{json.dumps({"metrics": metrics, "top_ready": top_ready, "blocked_sample": blocked_items}, ensure_ascii=False, indent=2)}'
        )
        llm = ctx.ollama.generate(
            prompt,
            system='You are a concise ecommerce merchandising analyst. Stay strictly grounded in the provided JSON.',
            model=ctx.model,
            cancel_event=ctx.cancel_event,
        )
        ctx.progress(95, 'Finalizing audit report...')

        return AgentResult(
            summary=f'Audited {len(items)} products: {len(ready)} ready, {len(blocked)} needing attention.',
            data={
                'metrics': metrics,
                'top_ready_skus': top_ready,
                'blocked_sample': blocked_items,
                'ai_recommendations': llm['response'],
            },
            model_used=llm.get('model', ctx.model),
        )


class ReelCopyStrategistAgent(BaseAgent):
    """Generates advisory Telugu + English Reel hooks from real catalogue items."""

    metadata = AgentMetadata(
        agent_id='reel_copy_strategist',
        name='Telugu Copy Strategist',
        role='specialist',
        description='Drafts advisory Telugu feature lines and Reel opening hooks for eligible catalogue items using local Ollama. Never modifies CSVs or publishes posts.',
        capabilities=('read_catalogue', 'telugu_copy_ideation', 'ollama_generation'),
        default_enabled=True,
        requires_ollama=True,
        can_publish=False,
        can_spend=False,
    )

    def execute(self, ctx: AgentContext) -> AgentResult:
        ctx.progress(15, f'Selecting {ctx.mode} products for Reel copy...')
        items = engine.catalogue(ctx.mode)
        ctx.check_cancelled()

        with closing(engine.db()) as con:
            recent = dict(con.execute('SELECT sku, MAX(created) FROM jobs WHERE mode=? GROUP BY sku', (ctx.mode,)).fetchall())

        eligible = [p for p in items.values() if not p['issues']]
        pool = eligible if eligible else list(items.values())
        ranked = sorted(pool, key=lambda p: (recent.get(p['sku'], ''), -p['margin'], -p['stock'], p['sku']))

        target_sku = str(ctx.payload.get('sku', '')).strip()
        if target_sku:
            chosen = [p for p in items.values() if p['sku'] == target_sku]
        else:
            chosen = ranked[:3]

        if not chosen:
            return AgentResult(
                summary=f'No products found in {ctx.mode} catalogue to draft Reel copy.',
                data={'products_analyzed': [], 'ai_copy_ideas': ''},
                model_used=ctx.model,
                warnings=['Add at least one product before requesting Reel copy ideas.'],
            )

        selected_summary = [
            {
                'sku': p['sku'],
                'name': p['name'],
                'category': p['category'],
                'price_inr': float(p['price']),
                'current_feature_te': p['feature_te'],
                'ready_to_promote': len(p['issues']) == 0,
                'issues': list(p['issues']),
            }
            for p in chosen
        ]
        ctx.log('INFO', f'Prepared {len(selected_summary)} product(s) for copy ideation.')
        ctx.progress(45, f'Drafting Telugu hooks with {ctx.model}...')

        prompt = (
            'Create advisory 15-second Instagram Reel copy ideas for these real ProdXMart products. '
            'For each SKU provide: (1) a short Telugu opening hook (<= 8 words), '
            '(2) a concise Telugu feature_te line (<= 240 chars) suitable for product reels, and '
            '(3) one visual framing tip using existing product photos. '
            'Do not invent product claims not supported by the name or category.\n\n'
            f'Products JSON:\n{json.dumps(selected_summary, ensure_ascii=False, indent=2)}'
        )
        llm = ctx.ollama.generate(
            prompt,
            system='You are a bilingual Telugu and English ecommerce Reel copywriter.',
            model=ctx.model,
            cancel_event=ctx.cancel_event,
        )
        ctx.progress(95, 'Formatting copy suggestions...')

        return AgentResult(
            summary=f'Prepared advisory Telugu Reel copy ideas for {len(selected_summary)} product(s).',
            data={
                'products_analyzed': selected_summary,
                'ai_copy_ideas': llm['response'],
            },
            model_used=llm.get('model', ctx.model),
        )


class PerformanceAnalystAgent(BaseAgent):
    """Analyzes real local orders, expenses, traffic and Reel queue outcomes."""

    metadata = AgentMetadata(
        agent_id='performance_analyst',
        name='Sales & Campaign Analyst',
        role='specialist',
        description='Reads local orders, expenses, campaign traffic and Reel queue counts, then summarizes real performance trends with local Ollama.',
        capabilities=('read_earnings', 'read_traffic', 'read_queue', 'ollama_analysis'),
        default_enabled=True,
        requires_ollama=True,
        can_publish=False,
        can_spend=False,
    )

    def execute(self, ctx: AgentContext) -> AgentResult:
        ctx.progress(15, f'Loading {ctx.mode} sales, traffic and Reel queue...')
        base = engine.directory(ctx.mode)
        earnings = engine.earnings(ctx.mode)

        traffic: dict[str, int] = {}
        for row in engine.records(base / 'traffic.csv', ['campaign', 'sessions']):
            key = row['campaign']
            value = int(row['sessions'])
            if key and value >= 0:
                traffic[key] = value

        with closing(engine.db()) as con:
            rows = con.execute('SELECT status, COUNT(*) AS cnt FROM jobs WHERE mode=? GROUP BY status', (ctx.mode,)).fetchall()
        queue_counts = {r['status']: int(r['cnt']) for r in rows}
        ctx.check_cancelled()

        snapshot = {
            'mode': ctx.mode,
            'earnings': earnings,
            'traffic_sessions': traffic,
            'reel_queue_counts': queue_counts,
        }
        ctx.log('INFO', f"Loaded earnings snapshot: {earnings['orders']} orders, {earnings['units']} units.")
        ctx.progress(45, f'Analyzing performance with {ctx.model}...')

        prompt = (
            'Analyze ONLY this real local ProdXMart performance snapshot. '
            'Summarize delivered orders, profit after costs/expenses, campaign attribution, and queue coverage. '
            'If orders or traffic are zero, state that plainly without guessing or inventing numbers.\n\n'
            f'Snapshot JSON:\n{json.dumps(snapshot, ensure_ascii=False, indent=2)}'
        )
        llm = ctx.ollama.generate(
            prompt,
            system='You are a factual ecommerce operations analyst. Never fabricate metrics.',
            model=ctx.model,
            cancel_event=ctx.cancel_event,
        )
        ctx.progress(95, 'Finalizing performance summary...')

        return AgentResult(
            summary=f"Analyzed {earnings['orders']} order(s), {earnings['units']} unit(s), profit ₹{earnings['profit']:,.2f}.",
            data={
                'snapshot': snapshot,
                'ai_analysis': llm['response'],
            },
            model_used=llm.get('model', ctx.model),
        )


def build_default_agents(storage_provider=None) -> list[BaseAgent]:
    """Return the default specialist agents in display order."""
    return [
        CatalogAgent(storage_provider=storage_provider),
        CatalogueAuditorAgent(),
        ReelCopyStrategistAgent(),
        PerformanceAnalystAgent(),
        MarketingAgent(storage_provider=storage_provider),
        VideoProductionAgent(storage_provider=storage_provider),
        QualityAssuranceAgent(storage_provider=storage_provider),
        SocialMediaAgent(storage_provider=storage_provider),
    ]
