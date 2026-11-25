"""SQLite persistence for agent configuration, task queues, run history and logs."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
from typing import Any, Optional
import uuid

import engine
from agents.ollama_client import DEFAULT_MODEL, DEFAULT_OLLAMA_URL, normalize_ollama_url

ALLOWED_EXECUTION_MODES = ('manual',)
TERMINAL_STATUSES = ('succeeded', 'failed', 'cancelled')
ACTIVE_STATUSES = ('queued', 'running')


class AgentStorage:
    """Thread-safe SQLite repository layered on top of engine.db()."""

    def _connect(self):
        con = engine.db()
        self.init_schema(con)
        return con

    @staticmethod
    def init_schema(con) -> None:
        con.execute(
            '''CREATE TABLE IF NOT EXISTS agent_global_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                ollama_base_url TEXT NOT NULL,
                default_model TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                max_retries INTEGER NOT NULL,
                retry_delay_seconds REAL NOT NULL,
                request_timeout_seconds INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )'''
        )
        con.execute(
            '''CREATE TABLE IF NOT EXISTS agent_configs (
                agent_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                model_override TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )'''
        )
        con.execute(
            '''CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                trigger TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                max_retries INTEGER NOT NULL DEFAULT 0,
                progress INTEGER NOT NULL DEFAULT 0,
                progress_message TEXT NOT NULL DEFAULT '',
                input_json TEXT NOT NULL DEFAULT '{}',
                output_json TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                error_traceback TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                parent_run_id TEXT NOT NULL DEFAULT '',
                retry_of_run_id TEXT NOT NULL DEFAULT '',
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT ''
            )'''
        )
        con.execute(
            '''CREATE TABLE IF NOT EXISTS agent_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )'''
        )
        con.execute('CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_created ON agent_runs(agent_id, created_at DESC)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_agent_logs_run ON agent_logs(run_id, id)')
        con.execute(
            '''CREATE TABLE IF NOT EXISTS catalog_sync_items (
                mode TEXT NOT NULL,
                dedup_key TEXT NOT NULL,
                sku TEXT NOT NULL,
                base_sku TEXT NOT NULL,
                variant TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                product_url TEXT NOT NULL DEFAULT '',
                supplier_source TEXT NOT NULL,
                price TEXT NOT NULL DEFAULT '',
                cost TEXT NOT NULL DEFAULT '',
                stock INTEGER,
                stock_status TEXT NOT NULL DEFAULT 'unknown',
                last_synced_at TEXT NOT NULL,
                stock_verified_at TEXT NOT NULL DEFAULT '',
                image_count INTEGER NOT NULL DEFAULT 0,
                missing_images_count INTEGER NOT NULL DEFAULT 0,
                last_run_id TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (mode, dedup_key)
            )'''
        )
        con.execute(
            '''CREATE TABLE IF NOT EXISTS catalog_staged_batches (
                mode TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                item_count INTEGER NOT NULL DEFAULT 0,
                pages_count INTEGER NOT NULL DEFAULT 0,
                staged_at TEXT NOT NULL
            )'''
        )
        con.execute(
            '''CREATE TABLE IF NOT EXISTS marketing_campaigns (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                campaign_type TEXT NOT NULL,
                language TEXT NOT NULL,
                season_theme TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                selected_skus_json TEXT NOT NULL DEFAULT '[]',
                campaign_json TEXT NOT NULL,
                storyboard_handoff_json TEXT NOT NULL,
                linked_job_ids_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'ready_for_video_agent',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )'''
        )
        con.execute('CREATE INDEX IF NOT EXISTS idx_marketing_mode_updated ON marketing_campaigns(mode, updated_at DESC)')
        con.execute(
            '''CREATE TABLE IF NOT EXISTS agent_handoffs (
                id TEXT PRIMARY KEY,
                source_agent TEXT NOT NULL,
                target_agent TEXT NOT NULL,
                mode TEXT NOT NULL,
                campaign_id TEXT NOT NULL DEFAULT '',
                run_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ready',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )'''
        )
        con.execute('CREATE INDEX IF NOT EXISTS idx_handoffs_target_mode ON agent_handoffs(target_agent, mode, updated_at DESC)')
        con.execute(
            '''CREATE TABLE IF NOT EXISTS video_productions (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                campaign_id TEXT NOT NULL DEFAULT '',
                skus_json TEXT NOT NULL DEFAULT '[]',
                duration_seconds INTEGER NOT NULL DEFAULT 15,
                language TEXT NOT NULL DEFAULT 'te',
                video_path TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                qa_status TEXT NOT NULL DEFAULT 'pending',
                human_review_status TEXT NOT NULL DEFAULT 'pending',
                reviewer_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )'''
        )
        con.execute('CREATE INDEX IF NOT EXISTS idx_video_prod_mode_updated ON video_productions(mode, updated_at DESC)')
        con.execute(
            '''CREATE TABLE IF NOT EXISTS video_qa_reports (
                id TEXT PRIMARY KEY,
                production_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                automated_passed INTEGER NOT NULL DEFAULT 0,
                overall_status TEXT NOT NULL,
                publish_allowed INTEGER NOT NULL DEFAULT 0,
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )'''
        )
        con.execute('CREATE INDEX IF NOT EXISTS idx_qa_prod ON video_qa_reports(production_id, updated_at DESC)')
        con.execute(
            '''CREATE TABLE IF NOT EXISTS social_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                global_publishing_stopped INTEGER NOT NULL DEFAULT 0,
                stop_reason TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )'''
        )
        con.execute(
            '''CREATE TABLE IF NOT EXISTS social_posts (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                platform TEXT NOT NULL,
                sku TEXT NOT NULL,
                skus_json TEXT NOT NULL DEFAULT '[]',
                production_id TEXT NOT NULL DEFAULT '',
                campaign_id TEXT NOT NULL DEFAULT '',
                video_path TEXT NOT NULL DEFAULT '',
                video_sha256 TEXT NOT NULL DEFAULT '',
                price_snapshot REAL NOT NULL DEFAULT 0,
                product_snapshot TEXT NOT NULL DEFAULT '',
                week TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                hashtags_json TEXT NOT NULL DEFAULT '[]',
                product_link TEXT NOT NULL DEFAULT '',
                approval_fingerprint TEXT NOT NULL DEFAULT '',
                approved_at TEXT NOT NULL DEFAULT '',
                scheduled_at TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                published_url TEXT NOT NULL DEFAULT '',
                external_post_id TEXT NOT NULL DEFAULT '',
                publish_confirmed_by TEXT NOT NULL DEFAULT '',
                package_path TEXT NOT NULL DEFAULT '',
                invalidation_reason TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )'''
        )
        con.execute('CREATE INDEX IF NOT EXISTS idx_social_mode_status ON social_posts(mode, status, updated_at DESC)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_social_dedup ON social_posts(mode, platform, sku)')
        con.execute(
            '''CREATE TABLE IF NOT EXISTS bank_settlements (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                reference TEXT NOT NULL,
                settled_at TEXT NOT NULL,
                amount REAL NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(mode, reference)
            )'''
        )

    def ensure_defaults(self, agents: list[Any]) -> None:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT OR IGNORE INTO agent_global_config
                   (id, ollama_base_url, default_model, execution_mode, max_retries, retry_delay_seconds, request_timeout_seconds, updated_at)
                   VALUES (1, ?, ?, 'manual', 2, 1.5, 120, ?)''',
                (DEFAULT_OLLAMA_URL, DEFAULT_MODEL, stamp),
            )
            for agent in agents:
                meta = agent.metadata
                con.execute(
                    '''INSERT OR IGNORE INTO agent_configs (agent_id, enabled, model_override, updated_at)
                       VALUES (?, ?, '', ?)''',
                    (meta.agent_id, 1 if meta.default_enabled else 0, stamp),
                )

    def get_global_config(self) -> dict[str, Any]:
        with engine.LOCK, closing(self._connect()) as con:
            row = con.execute('SELECT * FROM agent_global_config WHERE id = 1').fetchone()
            if not row:
                return {
                    'ollama_base_url': DEFAULT_OLLAMA_URL,
                    'default_model': DEFAULT_MODEL,
                    'execution_mode': 'manual',
                    'max_retries': 2,
                    'retry_delay_seconds': 1.5,
                    'request_timeout_seconds': 120,
                    'allow_publishing': False,
                    'allow_spending': False,
                    'require_factori_api': False,
                    'updated_at': '',
                }
            return {
                'ollama_base_url': row['ollama_base_url'],
                'default_model': row['default_model'],
                'execution_mode': row['execution_mode'],
                'max_retries': int(row['max_retries']),
                'retry_delay_seconds': float(row['retry_delay_seconds']),
                'request_timeout_seconds': int(row['request_timeout_seconds']),
                'allow_publishing': False,
                'allow_spending': False,
                'require_factori_api': False,
                'updated_at': row['updated_at'],
            }

    def update_global_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        current = self.get_global_config()
        ollama_url = normalize_ollama_url(updates.get('ollama_base_url', current['ollama_base_url']))
        default_model = str(updates.get('default_model', current['default_model']) or '').strip()
        if not default_model:
            raise ValueError('Default model cannot be empty.')
        execution_mode = str(updates.get('execution_mode', current['execution_mode']) or 'manual').strip().lower()
        if execution_mode not in ALLOWED_EXECUTION_MODES:
            raise ValueError('Phase 1 supports manual execution mode only.')
        try:
            max_retries = int(updates.get('max_retries', current['max_retries']))
            retry_delay = float(updates.get('retry_delay_seconds', current['retry_delay_seconds']))
            timeout_seconds = int(updates.get('request_timeout_seconds', current['request_timeout_seconds']))
        except (TypeError, ValueError) as exc:
            raise ValueError('Retry and timeout settings must be valid numbers.') from exc
        if not 0 <= max_retries <= 5:
            raise ValueError('Max retries must be between 0 and 5.')
        if not 0.0 <= retry_delay <= 30.0:
            raise ValueError('Retry delay must be between 0 and 30 seconds.')
        if not 5 <= timeout_seconds <= 600:
            raise ValueError('Request timeout must be between 5 and 600 seconds.')

        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT INTO agent_global_config
                   (id, ollama_base_url, default_model, execution_mode, max_retries, retry_delay_seconds, request_timeout_seconds, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     ollama_base_url=excluded.ollama_base_url,
                     default_model=excluded.default_model,
                     execution_mode=excluded.execution_mode,
                     max_retries=excluded.max_retries,
                     retry_delay_seconds=excluded.retry_delay_seconds,
                     request_timeout_seconds=excluded.request_timeout_seconds,
                     updated_at=excluded.updated_at''',
                (ollama_url, default_model, execution_mode, max_retries, retry_delay, timeout_seconds, stamp),
            )
        return self.get_global_config()

    def get_agent_configs(self) -> dict[str, dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            rows = con.execute('SELECT * FROM agent_configs').fetchall()
            return {
                r['agent_id']: {
                    'agent_id': r['agent_id'],
                    'enabled': bool(r['enabled']),
                    'model_override': r['model_override'],
                    'updated_at': r['updated_at'],
                }
                for r in rows
            }

    def set_agent_enabled(self, agent_id: str, enabled: bool) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT INTO agent_configs (agent_id, enabled, model_override, updated_at)
                   VALUES (?, ?, '', ?)
                   ON CONFLICT(agent_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at''',
                (agent_id, 1 if enabled else 0, stamp),
            )
        return self.get_agent_configs()[agent_id]

    def set_agent_model(self, agent_id: str, model_override: str) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        cleaned = (model_override or '').strip()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT INTO agent_configs (agent_id, enabled, model_override, updated_at)
                   VALUES (?, 1, ?, ?)
                   ON CONFLICT(agent_id) DO UPDATE SET model_override=excluded.model_override, updated_at=excluded.updated_at''',
                (agent_id, cleaned, stamp),
            )
        return self.get_agent_configs()[agent_id]

    def create_run(
        self,
        *,
        agent_id: str,
        mode: str,
        trigger: str,
        model: str,
        max_retries: int,
        input_payload: Optional[dict[str, Any]] = None,
        parent_run_id: str = '',
        retry_of_run_id: str = '',
    ) -> dict[str, Any]:
        run_id = uuid.uuid4().hex[:16]
        created_at = engine.now().isoformat()
        input_json = json.dumps(input_payload or {}, ensure_ascii=False)
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT INTO agent_runs
                   (id, agent_id, mode, status, trigger, attempt, max_retries, progress, progress_message,
                    input_json, output_json, error_message, error_traceback, model, parent_run_id, retry_of_run_id,
                    cancel_requested, created_at)
                   VALUES (?, ?, ?, 'queued', ?, 1, ?, 0, 'Queued', ?, '', '', '', ?, ?, ?, 0, ?)''',
                (run_id, agent_id, mode, trigger, max_retries, input_json, model, parent_run_id, retry_of_run_id, created_at),
            )
        return self.get_run(run_id)

    def update_run(self, run_id: str, **fields: Any) -> Optional[dict[str, Any]]:
        if not fields:
            return self.get_run(run_id)
        allowed = {
            'status',
            'attempt',
            'progress',
            'progress_message',
            'output_json',
            'error_message',
            'error_traceback',
            'model',
            'cancel_requested',
            'started_at',
            'finished_at',
        }
        cols = [k for k in fields if k in allowed]
        if not cols:
            return self.get_run(run_id)
        assignments = ', '.join(f'{k}=?' for k in cols)
        values = [fields[k] for k in cols] + [run_id]
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(f'UPDATE agent_runs SET {assignments} WHERE id=?', values)
        return self.get_run(run_id)

    def mark_cancel_requested(self, run_id: str) -> Optional[dict[str, Any]]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            row = con.execute('SELECT status FROM agent_runs WHERE id=?', (run_id,)).fetchone()
            if not row:
                return None
            if row['status'] == 'queued':
                con.execute(
                    '''UPDATE agent_runs
                       SET cancel_requested=1, status='cancelled', progress_message='Cancelled while queued',
                           error_message='Cancelled before execution started.', finished_at=?
                       WHERE id=?''',
                    (stamp, run_id),
                )
            elif row['status'] == 'running':
                con.execute(
                    '''UPDATE agent_runs
                       SET cancel_requested=1, progress_message='Stopping...'
                       WHERE id=?''',
                    (run_id,),
                )
        return self.get_run(run_id)

    def _hydrate_run(self, row) -> dict[str, Any]:
        item = dict(row)
        item['cancel_requested'] = bool(item['cancel_requested'])
        try:
            item['input'] = json.loads(item['input_json']) if item['input_json'] else {}
        except ValueError:
            item['input'] = {}
        try:
            item['output'] = json.loads(item['output_json']) if item['output_json'] else None
        except ValueError:
            item['output'] = None
        return item

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            row = con.execute('SELECT * FROM agent_runs WHERE id=?', (run_id,)).fetchone()
            return self._hydrate_run(row) if row else None

    def list_runs(
        self,
        *,
        agent_id: Optional[str] = None,
        mode: Optional[str] = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if agent_id:
            clauses.append('agent_id=?')
            params.append(agent_id)
        if mode:
            clauses.append('mode=?')
            params.append(mode)
        where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
        params.append(max(1, min(200, int(limit))))
        with engine.LOCK, closing(self._connect()) as con:
            rows = con.execute(
                f'SELECT * FROM agent_runs {where} ORDER BY created_at DESC, rowid DESC LIMIT ?',
                params,
            ).fetchall()
            return [self._hydrate_run(r) for r in rows]

    def get_latest_runs_by_agent(self, mode: Optional[str] = None) -> dict[str, dict[str, Any]]:
        runs = self.list_runs(mode=mode, limit=200)
        latest: dict[str, dict[str, Any]] = {}
        for run in runs:
            if run['agent_id'] not in latest:
                latest[run['agent_id']] = run
        return latest

    def add_log(self, run_id: str, agent_id: str, level: str, message: str, details: str = '') -> int:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            cur = con.execute(
                '''INSERT INTO agent_logs (run_id, agent_id, level, message, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (run_id, agent_id, level.upper(), message[:500], (details or '')[:4000], stamp),
            )
            return int(cur.lastrowid)

    def list_logs(
        self,
        *,
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if run_id:
            clauses.append('run_id=?')
            params.append(run_id)
        if agent_id:
            clauses.append('agent_id=?')
            params.append(agent_id)
        if level:
            clauses.append('level=?')
            params.append(level.upper())
        where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
        params.append(max(1, min(500, int(limit))))
        with engine.LOCK, closing(self._connect()) as con:
            rows = con.execute(
                f'SELECT * FROM agent_logs {where} ORDER BY id DESC LIMIT ?',
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def reconcile_interrupted_runs(self) -> int:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            rows = con.execute(
                "SELECT id, agent_id FROM agent_runs WHERE status IN ('queued', 'running')"
            ).fetchall()
            if not rows:
                return 0
            con.execute(
                '''UPDATE agent_runs
                   SET status='failed',
                       progress_message='Interrupted by application restart',
                       error_message='Task was interrupted because the server stopped before completion.',
                       finished_at=?
                   WHERE status IN ('queued', 'running')''',
                (stamp,),
            )
            for r in rows:
                con.execute(
                    '''INSERT INTO agent_logs (run_id, agent_id, level, message, details, created_at)
                       VALUES (?, ?, 'ERROR', 'Interrupted by application restart', '', ?)''',
                    (r['id'], r['agent_id'], stamp),
                )
            return len(rows)

    def upsert_catalog_sync_item(
        self,
        *,
        dedup_key: str,
        mode: str,
        sku: str,
        base_sku: str,
        variant: str,
        name: str,
        product_url: str,
        supplier_source: str,
        price: str,
        cost: str,
        stock: Optional[int],
        stock_status: str,
        last_synced_at: str,
        stock_verified_at: str,
        image_count: int,
        missing_images_count: int,
        last_run_id: str,
        last_error: str = '',
    ) -> None:
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT INTO catalog_sync_items (
                       mode, dedup_key, sku, base_sku, variant, name, product_url, supplier_source,
                       price, cost, stock, stock_status, last_synced_at, stock_verified_at,
                       image_count, missing_images_count, last_run_id, last_error
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(mode, dedup_key) DO UPDATE SET
                       sku=excluded.sku,
                       base_sku=excluded.base_sku,
                       variant=excluded.variant,
                       name=excluded.name,
                       product_url=excluded.product_url,
                       supplier_source=excluded.supplier_source,
                       price=excluded.price,
                       cost=excluded.cost,
                       stock=excluded.stock,
                       stock_status=excluded.stock_status,
                       last_synced_at=excluded.last_synced_at,
                       stock_verified_at=excluded.stock_verified_at,
                       image_count=excluded.image_count,
                       missing_images_count=excluded.missing_images_count,
                       last_run_id=excluded.last_run_id,
                       last_error=excluded.last_error''',
                (
                    mode, dedup_key, sku, base_sku, variant, name, product_url, supplier_source,
                    price, cost, stock, stock_status, last_synced_at, stock_verified_at,
                    image_count, missing_images_count, last_run_id, last_error,
                ),
            )

    def get_catalog_sync_item(self, dedup_key: str, mode: str = 'store') -> Optional[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            row = con.execute(
                'SELECT * FROM catalog_sync_items WHERE mode=? AND dedup_key=?',
                (mode, dedup_key),
            ).fetchone()
            return dict(row) if row else None

    def list_catalog_sync_items(self, mode: str = 'store') -> list[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            rows = con.execute(
                'SELECT * FROM catalog_sync_items WHERE mode=? ORDER BY last_synced_at DESC, sku ASC',
                (mode,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_catalog_freshness_summary(self, mode: str = 'store', max_age_hours: int = 24) -> dict[str, Any]:
        items = self.list_catalog_sync_items(mode=mode)
        current_dt = engine.now()
        fresh = 0
        stale = 0
        unknown = 0
        price_fresh = 0
        price_stale = 0
        price_missing = 0
        latest_sync = ''
        latest_source = ''
        for item in items:
            if item['last_synced_at'] > latest_sync:
                latest_sync = item['last_synced_at']
                latest_source = item.get('supplier_source') or latest_source
            if not item.get('price'):
                price_missing += 1
            else:
                try:
                    p_dt = datetime.fromisoformat(item['last_synced_at'])
                    if p_dt.tzinfo is None:
                        p_dt = p_dt.replace(tzinfo=engine.IST)
                    if 0 <= (current_dt - p_dt).total_seconds() / 3600.0 <= max_age_hours:
                        price_fresh += 1
                    else:
                        price_stale += 1
                except ValueError:
                    price_stale += 1

            if item['stock_status'] == 'unknown' or not item['stock_verified_at']:
                unknown += 1
                continue
            try:
                verified_dt = datetime.fromisoformat(item['stock_verified_at'])
                if verified_dt.tzinfo is None:
                    verified_dt = verified_dt.replace(tzinfo=engine.IST)
                age_hours = (current_dt - verified_dt).total_seconds() / 3600.0
                if 0 <= age_hours <= max_age_hours:
                    fresh += 1
                else:
                    stale += 1
            except ValueError:
                unknown += 1

        catalogue_items = engine.catalogue(mode)
        with_images = sum(1 for p in catalogue_items.values() if p.get('files'))
        missing_images = max(0, len(catalogue_items) - with_images)

        runs = self.list_runs(agent_id='catalog_agent', mode=mode, limit=20)
        last_success_run = next((r for r in runs if r['status'] == 'succeeded'), None)
        last_successful_import_at = (last_success_run.get('finished_at') or last_success_run.get('created_at')) if last_success_run else latest_sync

        if not latest_source and catalogue_items:
            latest_source = 'local_catalogue'
        source_labels = {
            'extension': 'Factori Bridge extension',
            'csv': 'CSV / Excel import',
            'local_catalogue': 'Manual / Local catalogue',
            'factori_api': 'Factori Official API',
        }
        return {
            'total_tracked': len(items),
            'fresh': fresh,
            'stale': stale,
            'unknown': unknown,
            'last_synced_at': latest_sync,
            'last_successful_import_at': last_successful_import_at,
            'data_source': source_labels.get(latest_source, latest_source or 'Manual / Local catalogue'),
            'price_freshness': {
                'fresh': price_fresh,
                'stale': price_stale,
                'missing': price_missing,
            },
            'image_availability': {
                'total_products': len(catalogue_items),
                'with_images': with_images,
                'missing_images': missing_images,
            },
        }

    def stage_catalog_batch(self, mode: str, source: str, payload: dict[str, Any]) -> dict[str, Any]:
        cards = payload.get('cards') or payload.get('items') or []
        pages = payload.get('pages') or []
        item_count = len(cards) if cards else sum(len(p.get('cards') or p.get('items') or []) for p in pages)
        pages_count = int(payload.get('pages_count') or payload.get('total_pages') or (len(pages) if pages else (1 if item_count else 0)))
        if isinstance(payload.get('pages'), int):
            pages_count = int(payload['pages'])
        staged_at = engine.now().isoformat()
        raw_json = json.dumps(payload, ensure_ascii=False)
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT INTO catalog_staged_batches (mode, source, payload_json, item_count, pages_count, staged_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(mode) DO UPDATE SET
                       source=excluded.source,
                       payload_json=excluded.payload_json,
                       item_count=excluded.item_count,
                       pages_count=excluded.pages_count,
                       staged_at=excluded.staged_at''',
                (mode, source, raw_json, item_count, pages_count, staged_at),
            )
        return {
            'mode': mode,
            'source': source,
            'item_count': item_count,
            'pages_count': pages_count,
            'staged_at': staged_at,
        }

    def get_staged_catalog_batch(self, mode: str) -> Optional[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            row = con.execute('SELECT * FROM catalog_staged_batches WHERE mode=?', (mode,)).fetchone()
            if not row:
                return None
            try:
                payload = json.loads(row['payload_json'])
            except ValueError:
                payload = {}
            return {
                'mode': row['mode'],
                'source': row['source'],
                'item_count': int(row['item_count']),
                'pages_count': int(row['pages_count']),
                'staged_at': row['staged_at'],
                'cards': payload.get('cards') or payload.get('items') or [],
                'pages': payload.get('pages') if isinstance(payload.get('pages'), list) else [],
            }

    def clear_staged_catalog_batch(self, mode: str) -> None:
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute('DELETE FROM catalog_staged_batches WHERE mode=?', (mode,))

    def save_marketing_campaign(
        self,
        *,
        campaign_id: str,
        run_id: str,
        mode: str,
        campaign_type: str,
        language: str,
        season_theme: str,
        category: str,
        selected_skus: list[str],
        campaign_data: dict[str, Any],
        storyboard_handoff: dict[str, Any],
        linked_job_ids: Optional[list[str]] = None,
        status: str = 'ready_for_video_agent',
    ) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            existing = con.execute('SELECT created_at, linked_job_ids_json FROM marketing_campaigns WHERE id=?', (campaign_id,)).fetchone()
            created_at = existing['created_at'] if existing else stamp
            prior_jobs = json.loads(existing['linked_job_ids_json']) if existing else []
            merged_jobs = list(dict.fromkeys([*prior_jobs, *(linked_job_ids or [])]))
            con.execute(
                '''INSERT INTO marketing_campaigns (
                       id, run_id, mode, campaign_type, language, season_theme, category,
                       selected_skus_json, campaign_json, storyboard_handoff_json, linked_job_ids_json,
                       status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       run_id=excluded.run_id,
                       campaign_type=excluded.campaign_type,
                       language=excluded.language,
                       season_theme=excluded.season_theme,
                       category=excluded.category,
                       selected_skus_json=excluded.selected_skus_json,
                       campaign_json=excluded.campaign_json,
                       storyboard_handoff_json=excluded.storyboard_handoff_json,
                       linked_job_ids_json=excluded.linked_job_ids_json,
                       status=excluded.status,
                       updated_at=excluded.updated_at''',
                (
                    campaign_id,
                    run_id,
                    mode,
                    campaign_type,
                    language,
                    season_theme,
                    category,
                    json.dumps(selected_skus, ensure_ascii=False),
                    json.dumps(campaign_data, ensure_ascii=False),
                    json.dumps(storyboard_handoff, ensure_ascii=False),
                    json.dumps(merged_jobs, ensure_ascii=False),
                    status,
                    created_at,
                    stamp,
                ),
            )
        return self.get_marketing_campaign(campaign_id, mode=mode)

    def _hydrate_campaign(self, row) -> dict[str, Any]:
        item = dict(row)
        item['selected_skus'] = json.loads(item['selected_skus_json']) if item['selected_skus_json'] else []
        item['campaign'] = json.loads(item['campaign_json']) if item['campaign_json'] else {}
        item['storyboard_handoff'] = json.loads(item['storyboard_handoff_json']) if item['storyboard_handoff_json'] else {}
        item['linked_job_ids'] = json.loads(item['linked_job_ids_json']) if item['linked_job_ids_json'] else []
        return item

    def get_marketing_campaign(self, campaign_id: str, mode: Optional[str] = None) -> Optional[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            if mode:
                row = con.execute('SELECT * FROM marketing_campaigns WHERE id=? AND mode=?', (campaign_id, mode)).fetchone()
            else:
                row = con.execute('SELECT * FROM marketing_campaigns WHERE id=?', (campaign_id,)).fetchone()
            return self._hydrate_campaign(row) if row else None

    def list_marketing_campaigns(self, mode: str = 'store', limit: int = 15) -> list[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            rows = con.execute(
                'SELECT * FROM marketing_campaigns WHERE mode=? ORDER BY updated_at DESC LIMIT ?',
                (mode, max(1, min(100, int(limit)))),
            ).fetchall()
            return [self._hydrate_campaign(r) for r in rows]

    def link_marketing_campaign_jobs(self, campaign_id: str, mode: str, job_ids: list[str]) -> Optional[dict[str, Any]]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            row = con.execute('SELECT linked_job_ids_json FROM marketing_campaigns WHERE id=? AND mode=?', (campaign_id, mode)).fetchone()
            if not row:
                return None
            prior = json.loads(row['linked_job_ids_json']) if row['linked_job_ids_json'] else []
            merged = list(dict.fromkeys([*prior, *job_ids]))
            con.execute(
                'UPDATE marketing_campaigns SET linked_job_ids_json=?, updated_at=? WHERE id=? AND mode=?',
                (json.dumps(merged, ensure_ascii=False), stamp, campaign_id, mode),
            )
        return self.get_marketing_campaign(campaign_id, mode=mode)

    def save_agent_handoff(
        self,
        *,
        source_agent: str,
        target_agent: str,
        mode: str,
        campaign_id: str,
        run_id: str,
        payload: dict[str, Any],
        status: str = 'ready',
    ) -> dict[str, Any]:
        hid = f'{source_agent}->{target_agent}:{mode}:{campaign_id or run_id}'
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT INTO agent_handoffs (id, source_agent, target_agent, mode, campaign_id, run_id, payload_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       run_id=excluded.run_id,
                       payload_json=excluded.payload_json,
                       status=excluded.status,
                       updated_at=excluded.updated_at''',
                (hid, source_agent, target_agent, mode, campaign_id, run_id, json.dumps(payload, ensure_ascii=False), status, stamp, stamp),
            )
        return self.get_latest_handoff(target_agent, mode=mode)

    def get_latest_handoff(self, target_agent: str, mode: str = 'store') -> Optional[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            row = con.execute(
                'SELECT * FROM agent_handoffs WHERE target_agent=? AND mode=? ORDER BY updated_at DESC LIMIT 1',
                (target_agent, mode),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item['payload'] = json.loads(item['payload_json']) if item['payload_json'] else {}
            return item

    def save_video_production(
        self,
        *,
        production_id: str,
        run_id: str,
        mode: str,
        campaign_id: str,
        skus: list[str],
        duration_seconds: int,
        language: str,
        video_path: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            existing = con.execute('SELECT created_at FROM video_productions WHERE id=?', (production_id,)).fetchone()
            created_at = existing['created_at'] if existing else stamp
            con.execute(
                '''INSERT INTO video_productions (
                       id, run_id, mode, campaign_id, skus_json, duration_seconds, language,
                       video_path, metadata_json, qa_status, human_review_status, reviewer_note,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'pending', '', ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       run_id=excluded.run_id,
                       campaign_id=excluded.campaign_id,
                       skus_json=excluded.skus_json,
                       duration_seconds=excluded.duration_seconds,
                       language=excluded.language,
                       video_path=excluded.video_path,
                       metadata_json=excluded.metadata_json,
                       qa_status='pending',
                       human_review_status='pending',
                       reviewer_note='',
                       updated_at=excluded.updated_at''',
                (
                    production_id,
                    run_id,
                    mode,
                    campaign_id,
                    json.dumps(skus, ensure_ascii=False),
                    int(duration_seconds),
                    language,
                    video_path,
                    json.dumps(metadata, ensure_ascii=False),
                    created_at,
                    stamp,
                ),
            )
        return self.get_video_production(production_id, mode=mode)

    def get_social_settings(self) -> dict[str, Any]:
        with engine.LOCK, closing(self._connect()) as con:
            row = con.execute('SELECT * FROM social_settings WHERE id = 1').fetchone()
            if not row:
                return {'global_publishing_stopped': False, 'stop_reason': '', 'updated_at': ''}
            return {
                'global_publishing_stopped': bool(row['global_publishing_stopped']),
                'stop_reason': row['stop_reason'],
                'updated_at': row['updated_at'],
            }

    def is_global_publishing_stopped(self) -> bool:
        return self.get_social_settings()['global_publishing_stopped']

    def set_global_publishing_stopped(self, stopped: bool, reason: str = '') -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT INTO social_settings (id, global_publishing_stopped, stop_reason, updated_at)
                   VALUES (1, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       global_publishing_stopped=excluded.global_publishing_stopped,
                       stop_reason=excluded.stop_reason,
                       updated_at=excluded.updated_at''',
                (1 if stopped else 0, (reason or '').strip()[:300], stamp),
            )
        return self.get_social_settings()

    def _hydrate_social_post(self, row) -> dict[str, Any]:
        item = dict(row)
        item['skus'] = json.loads(item['skus_json']) if item['skus_json'] else []
        item['hashtags'] = json.loads(item['hashtags_json']) if item['hashtags_json'] else []
        return item

    def create_social_post(
        self,
        *,
        post_id: str,
        run_id: str,
        mode: str,
        platform: str,
        sku: str,
        skus: list[str],
        production_id: str,
        campaign_id: str,
        video_path: str,
        video_sha256: str,
        price_snapshot: float,
        product_snapshot: str,
        week: str,
        status: str,
        title: str,
        caption: str,
        description: str,
        hashtags: list[str],
        product_link: str,
    ) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT INTO social_posts (
                       id, run_id, mode, platform, sku, skus_json, production_id, campaign_id,
                       video_path, video_sha256, price_snapshot, product_snapshot, week, status,
                       title, caption, description, hashtags_json, product_link, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    post_id, run_id, mode, platform, sku, json.dumps(skus, ensure_ascii=False),
                    production_id, campaign_id, video_path, video_sha256, float(price_snapshot),
                    product_snapshot, week, status, title, caption, description,
                    json.dumps(hashtags, ensure_ascii=False), product_link, stamp, stamp,
                ),
            )
        return self.get_social_post(post_id, mode=mode)

    def get_social_post(self, post_id: str, mode: Optional[str] = None) -> Optional[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            if mode:
                row = con.execute('SELECT * FROM social_posts WHERE id=? AND mode=?', (post_id, mode)).fetchone()
            else:
                row = con.execute('SELECT * FROM social_posts WHERE id=?', (post_id,)).fetchone()
            return self._hydrate_social_post(row) if row else None

    def list_social_posts(self, mode: str = 'store', limit: int = 100) -> list[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            rows = con.execute(
                'SELECT * FROM social_posts WHERE mode=? ORDER BY updated_at DESC LIMIT ?',
                (mode, max(1, min(300, int(limit)))),
            ).fetchall()
            return [self._hydrate_social_post(r) for r in rows]

    def find_duplicate_social_post(
        self,
        *,
        mode: str,
        platform: str,
        sku: str,
        production_id: str,
        week: str,
        exclude_post_id: str = '',
        statuses: tuple[str, ...],
    ) -> Optional[dict[str, Any]]:
        placeholders = ','.join('?' for _ in statuses)
        with engine.LOCK, closing(self._connect()) as con:
            row = con.execute(
                f'''SELECT * FROM social_posts
                    WHERE mode=? AND platform=? AND sku=? AND id!=?
                      AND status IN ({placeholders})
                      AND (production_id=? OR (week=? AND status IN ('Approved','Scheduled','Published')))
                    ORDER BY updated_at DESC LIMIT 1''',
                (mode, platform, sku, exclude_post_id, *statuses, production_id, week),
            ).fetchone()
            return self._hydrate_social_post(row) if row else None

    def mark_social_approved(
        self,
        *,
        post_id: str,
        mode: str,
        approval_fingerprint: str,
        price_snapshot: float,
        product_snapshot: str,
        video_sha256: str,
        package_path: str,
        note: str = '',
    ) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET status='Approved', approval_fingerprint=?, approved_at=?,
                       price_snapshot=?, product_snapshot=?, video_sha256=?,
                       package_path=?, invalidation_reason='', error_message=?, updated_at=?
                   WHERE id=? AND mode=?''',
                (
                    approval_fingerprint, stamp, float(price_snapshot), product_snapshot,
                    video_sha256, package_path, (note or '').strip()[:300], stamp, post_id, mode,
                ),
            )
        return self.get_social_post(post_id, mode=mode)

    def mark_social_rejected(self, *, post_id: str, mode: str, reason: str) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET status='Draft', approval_fingerprint='', scheduled_at='',
                       invalidation_reason=?, error_message=?, updated_at=?
                   WHERE id=? AND mode=?''',
                (reason[:300], reason[:300], stamp, post_id, mode),
            )
        return self.get_social_post(post_id, mode=mode)

    def invalidate_social_approval(self, post_id: str, *, mode: str, reason: str) -> Optional[dict[str, Any]]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET status='Awaiting Approval', approval_fingerprint='', scheduled_at='',
                       invalidation_reason=?, updated_at=?
                   WHERE id=? AND mode=? AND status IN ('Approved', 'Scheduled')''',
                (reason[:300], stamp, post_id, mode),
            )
        return self.get_social_post(post_id, mode=mode)

    def update_social_post_content(
        self,
        *,
        post_id: str,
        mode: str,
        updates: dict[str, Any],
        next_status: str,
        invalidate_reason: str = '',
    ) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET title=?, caption=?, description=?, hashtags_json=?, product_link=?,
                       status=?,
                       approval_fingerprint=CASE WHEN ? IN ('Draft','Awaiting Approval') THEN '' ELSE approval_fingerprint END,
                       scheduled_at=CASE WHEN ? IN ('Draft','Awaiting Approval') THEN '' ELSE scheduled_at END,
                       invalidation_reason=?, error_message='', updated_at=?
                   WHERE id=? AND mode=?''',
                (
                    updates['title'], updates['caption'], updates['description'],
                    json.dumps(updates['hashtags'], ensure_ascii=False), updates['product_link'],
                    next_status, next_status, next_status, invalidate_reason[:300], stamp, post_id, mode,
                ),
            )
        return self.get_social_post(post_id, mode=mode)

    def mark_social_scheduled(self, *, post_id: str, mode: str, scheduled_at: str, week: str) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET status='Scheduled', scheduled_at=?, week=?, error_message='', updated_at=?
                   WHERE id=? AND mode=?''',
                (scheduled_at, week, stamp, post_id, mode),
            )
        return self.get_social_post(post_id, mode=mode)

    def update_social_package_path(self, *, post_id: str, mode: str, package_path: str) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                'UPDATE social_posts SET package_path=?, updated_at=? WHERE id=? AND mode=?',
                (package_path, stamp, post_id, mode),
            )
        return self.get_social_post(post_id, mode=mode)

    def mark_social_published(
        self,
        *,
        post_id: str,
        mode: str,
        published_url: str,
        external_post_id: str,
        confirmed_by: str,
    ) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET status='Published', published_at=?, published_url=?,
                       external_post_id=?, publish_confirmed_by=?, error_message='', updated_at=?
                   WHERE id=? AND mode=?''',
                (stamp, published_url, external_post_id, confirmed_by, stamp, post_id, mode),
            )
        return self.get_social_post(post_id, mode=mode)

    def mark_social_failed(self, *, post_id: str, mode: str, error_message: str) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET status='Failed', error_message=?, updated_at=?
                   WHERE id=? AND mode=?''',
                (error_message[:400], stamp, post_id, mode),
            )
        return self.get_social_post(post_id, mode=mode)

    def _hydrate_video_production(self, row) -> dict[str, Any]:
        item = dict(row)
        item['skus'] = json.loads(item['skus_json']) if item['skus_json'] else []
        item['metadata'] = json.loads(item['metadata_json']) if item['metadata_json'] else {}
        return item

    def get_video_production(self, production_id: str, mode: Optional[str] = None) -> Optional[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            if mode:
                row = con.execute('SELECT * FROM video_productions WHERE id=? AND mode=?', (production_id, mode)).fetchone()
            else:
                row = con.execute('SELECT * FROM video_productions WHERE id=?', (production_id,)).fetchone()
            return self._hydrate_video_production(row) if row else None

    def list_video_productions(self, mode: str = 'store', limit: int = 20) -> list[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            rows = con.execute(
                'SELECT * FROM video_productions WHERE mode=? ORDER BY updated_at DESC LIMIT ?',
                (mode, max(1, min(100, int(limit)))),
            ).fetchall()
            return [self._hydrate_video_production(r) for r in rows]

    def update_video_production_qa(
        self,
        production_id: str,
        *,
        qa_status: str,
        human_review_status: str,
        reviewer_note: str = '',
    ) -> Optional[dict[str, Any]]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE video_productions
                   SET qa_status=?, human_review_status=?, reviewer_note=?, updated_at=?
                   WHERE id=?''',
                (qa_status, human_review_status, reviewer_note[:500], stamp, production_id),
            )
        return self.get_video_production(production_id)

    def save_qa_report(
        self,
        *,
        report_id: str,
        production_id: str,
        run_id: str,
        mode: str,
        automated_passed: bool,
        overall_status: str,
        publish_allowed: bool,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT INTO video_qa_reports (
                       id, production_id, run_id, mode, automated_passed, overall_status,
                       publish_allowed, report_json, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    report_id,
                    production_id,
                    run_id,
                    mode,
                    1 if automated_passed else 0,
                    overall_status,
                    1 if publish_allowed else 0,
                    json.dumps(report, ensure_ascii=False),
                    stamp,
                    stamp,
                ),
            )
        return self.get_latest_qa_report(production_id, mode=mode)

    def get_latest_qa_report(self, production_id: str, mode: Optional[str] = None) -> Optional[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            if mode:
                row = con.execute(
                    'SELECT * FROM video_qa_reports WHERE production_id=? AND mode=? ORDER BY updated_at DESC LIMIT 1',
                    (production_id, mode),
                ).fetchone()
            else:
                row = con.execute(
                    'SELECT * FROM video_qa_reports WHERE production_id=? ORDER BY updated_at DESC LIMIT 1',
                    (production_id,),
                ).fetchone()
            if not row:
                return None
            item = dict(row)
            item['automated_passed'] = bool(item['automated_passed'])
            item['publish_allowed'] = bool(item['publish_allowed'])
            item['report'] = json.loads(item['report_json']) if item['report_json'] else {}
            return item

    def list_qa_reports(self, mode: str = 'store', limit: int = 20) -> list[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            rows = con.execute(
                'SELECT * FROM video_qa_reports WHERE mode=? ORDER BY updated_at DESC LIMIT ?',
                (mode, max(1, min(100, int(limit)))),
            ).fetchall()
            out = []
            for row in rows:
                item = dict(row)
                item['automated_passed'] = bool(item['automated_passed'])
                item['publish_allowed'] = bool(item['publish_allowed'])
                item['report'] = json.loads(item['report_json']) if item['report_json'] else {}
                out.append(item)
            return out

    def record_human_qa_review(
        self,
        *,
        production_id: str,
        mode: str,
        decision: str,
        checklist: dict[str, bool],
        note: str = '',
    ) -> dict[str, Any]:
        prod = self.get_video_production(production_id, mode=mode)
        if not prod:
            raise ValueError('Video production not found.')
        if decision not in ('approved', 'rejected'):
            raise ValueError('Decision must be approved or rejected.')
        if decision == 'approved':
            if prod['qa_status'] != 'passed':
                raise ValueError('Cannot approve a video that failed automated QA checks.')
            if not all(checklist.get(k) is True for k in ('visual_quality', 'pronunciation', 'product_accuracy')):
                raise ValueError('Confirm visual quality, pronunciation, and product accuracy before approving.')

        stamp = engine.now().isoformat()
        latest_qa = self.get_latest_qa_report(production_id, mode=mode)
        overall_status = 'approved' if decision == 'approved' else 'rejected'
        publish_allowed = decision == 'approved' and prod['qa_status'] == 'passed'

        self.update_video_production_qa(
            production_id,
            qa_status=prod['qa_status'],
            human_review_status=decision,
            reviewer_note=note,
        )

        if latest_qa:
            rep = dict(latest_qa['report'])
            rep['overall_status'] = overall_status
            rep['publish_allowed'] = publish_allowed
            rep['human_review'] = {
                'required': True,
                'status': decision,
                'checklist': checklist,
                'reviewer_note': note,
                'reviewed_at': stamp,
            }
            with engine.LOCK, closing(self._connect()) as con, con:
                con.execute(
                    '''UPDATE video_qa_reports
                       SET overall_status=?, publish_allowed=?, report_json=?, updated_at=?
                       WHERE id=?''',
                    (overall_status, 1 if publish_allowed else 0, json.dumps(rep, ensure_ascii=False), stamp, latest_qa['id']),
                )
        return self.get_video_production(production_id, mode=mode)

    def get_social_settings(self) -> dict[str, Any]:
        with engine.LOCK, closing(self._connect()) as con:
            row = con.execute('SELECT * FROM social_settings WHERE id = 1').fetchone()
            if not row:
                return {'global_publishing_stopped': False, 'stop_reason': '', 'updated_at': ''}
            return {
                'global_publishing_stopped': bool(row['global_publishing_stopped']),
                'stop_reason': row['stop_reason'],
                'updated_at': row['updated_at'],
            }

    def is_global_publishing_stopped(self) -> bool:
        return self.get_social_settings()['global_publishing_stopped']

    def set_global_publishing_stopped(self, stopped: bool, reason: str = '') -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT INTO social_settings (id, global_publishing_stopped, stop_reason, updated_at)
                   VALUES (1, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       global_publishing_stopped=excluded.global_publishing_stopped,
                       stop_reason=excluded.stop_reason,
                       updated_at=excluded.updated_at''',
                (1 if stopped else 0, (reason or '').strip()[:300], stamp),
            )
        return self.get_social_settings()

    def _hydrate_social_post(self, row) -> dict[str, Any]:
        item = dict(row)
        item['skus'] = json.loads(item['skus_json']) if item['skus_json'] else []
        item['hashtags'] = json.loads(item['hashtags_json']) if item['hashtags_json'] else []
        return item

    def create_social_post(
        self,
        *,
        post_id: str,
        run_id: str,
        mode: str,
        platform: str,
        sku: str,
        skus: list[str],
        production_id: str,
        campaign_id: str,
        video_path: str,
        video_sha256: str,
        price_snapshot: float,
        product_snapshot: str,
        week: str,
        status: str,
        title: str,
        caption: str,
        description: str,
        hashtags: list[str],
        product_link: str,
    ) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''INSERT INTO social_posts (
                       id, run_id, mode, platform, sku, skus_json, production_id, campaign_id,
                       video_path, video_sha256, price_snapshot, product_snapshot, week, status,
                       title, caption, description, hashtags_json, product_link, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    post_id, run_id, mode, platform, sku, json.dumps(skus, ensure_ascii=False),
                    production_id, campaign_id, video_path, video_sha256, float(price_snapshot),
                    product_snapshot, week, status, title, caption, description,
                    json.dumps(hashtags, ensure_ascii=False), product_link, stamp, stamp,
                ),
            )
        return self.get_social_post(post_id, mode=mode)

    def get_social_post(self, post_id: str, mode: Optional[str] = None) -> Optional[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            if mode:
                row = con.execute('SELECT * FROM social_posts WHERE id=? AND mode=?', (post_id, mode)).fetchone()
            else:
                row = con.execute('SELECT * FROM social_posts WHERE id=?', (post_id,)).fetchone()
            return self._hydrate_social_post(row) if row else None

    def list_social_posts(self, mode: str = 'store', limit: int = 100) -> list[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            rows = con.execute(
                'SELECT * FROM social_posts WHERE mode=? ORDER BY updated_at DESC LIMIT ?',
                (mode, max(1, min(300, int(limit)))),
            ).fetchall()
            return [self._hydrate_social_post(r) for r in rows]

    def find_duplicate_social_post(
        self,
        *,
        mode: str,
        platform: str,
        sku: str,
        production_id: str,
        week: str,
        exclude_post_id: str = '',
        statuses: tuple[str, ...],
    ) -> Optional[dict[str, Any]]:
        placeholders = ','.join('?' for _ in statuses)
        with engine.LOCK, closing(self._connect()) as con:
            row = con.execute(
                f'''SELECT * FROM social_posts
                    WHERE mode=? AND platform=? AND sku=? AND id!=?
                      AND status IN ({placeholders})
                      AND (production_id=? OR (week=? AND status IN ('Approved','Scheduled','Published')))
                    ORDER BY updated_at DESC LIMIT 1''',
                (mode, platform, sku, exclude_post_id, *statuses, production_id, week),
            ).fetchone()
            return self._hydrate_social_post(row) if row else None

    def mark_social_approved(
        self,
        *,
        post_id: str,
        mode: str,
        approval_fingerprint: str,
        price_snapshot: float,
        product_snapshot: str,
        video_sha256: str,
        package_path: str,
        note: str = '',
    ) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET status='Approved', approval_fingerprint=?, approved_at=?,
                       price_snapshot=?, product_snapshot=?, video_sha256=?,
                       package_path=?, invalidation_reason='', error_message=?, updated_at=?
                   WHERE id=? AND mode=?''',
                (
                    approval_fingerprint, stamp, float(price_snapshot), product_snapshot,
                    video_sha256, package_path, (note or '').strip()[:300], stamp, post_id, mode,
                ),
            )
        return self.get_social_post(post_id, mode=mode)

    def mark_social_rejected(self, *, post_id: str, mode: str, reason: str) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET status='Draft', approval_fingerprint='', scheduled_at='',
                       invalidation_reason=?, error_message=?, updated_at=?
                   WHERE id=? AND mode=?''',
                (reason[:300], reason[:300], stamp, post_id, mode),
            )
        return self.get_social_post(post_id, mode=mode)

    def invalidate_social_approval(self, post_id: str, *, mode: str, reason: str) -> Optional[dict[str, Any]]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET status='Awaiting Approval', approval_fingerprint='', scheduled_at='',
                       invalidation_reason=?, updated_at=?
                   WHERE id=? AND mode=? AND status IN ('Approved', 'Scheduled')''',
                (reason[:300], stamp, post_id, mode),
            )
        return self.get_social_post(post_id, mode=mode)

    def update_social_post_content(
        self,
        *,
        post_id: str,
        mode: str,
        updates: dict[str, Any],
        next_status: str,
        invalidate_reason: str = '',
    ) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET title=?, caption=?, description=?, hashtags_json=?, product_link=?,
                       status=?,
                       approval_fingerprint=CASE WHEN ? IN ('Draft','Awaiting Approval') THEN '' ELSE approval_fingerprint END,
                       scheduled_at=CASE WHEN ? IN ('Draft','Awaiting Approval') THEN '' ELSE scheduled_at END,
                       invalidation_reason=?, error_message='', updated_at=?
                   WHERE id=? AND mode=?''',
                (
                    updates['title'], updates['caption'], updates['description'],
                    json.dumps(updates['hashtags'], ensure_ascii=False), updates['product_link'],
                    next_status, next_status, next_status, invalidate_reason[:300], stamp, post_id, mode,
                ),
            )
        return self.get_social_post(post_id, mode=mode)

    def mark_social_scheduled(self, *, post_id: str, mode: str, scheduled_at: str, week: str) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET status='Scheduled', scheduled_at=?, week=?, error_message='', updated_at=?
                   WHERE id=? AND mode=?''',
                (scheduled_at, week, stamp, post_id, mode),
            )
        return self.get_social_post(post_id, mode=mode)

    def update_social_package_path(self, *, post_id: str, mode: str, package_path: str) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                'UPDATE social_posts SET package_path=?, updated_at=? WHERE id=? AND mode=?',
                (package_path, stamp, post_id, mode),
            )
        return self.get_social_post(post_id, mode=mode)

    def mark_social_published(
        self,
        *,
        post_id: str,
        mode: str,
        published_url: str,
        external_post_id: str,
        confirmed_by: str,
    ) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET status='Published', published_at=?, published_url=?,
                       external_post_id=?, publish_confirmed_by=?, error_message='', updated_at=?
                   WHERE id=? AND mode=?''',
                (stamp, published_url, external_post_id, confirmed_by, stamp, post_id, mode),
            )
        return self.get_social_post(post_id, mode=mode)

    def mark_social_failed(self, *, post_id: str, mode: str, error_message: str) -> dict[str, Any]:
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            con.execute(
                '''UPDATE social_posts
                   SET status='Failed', error_message=?, updated_at=?
                   WHERE id=? AND mode=?''',
                (error_message[:400], stamp, post_id, mode),
            )
        return self.get_social_post(post_id, mode=mode)

    def add_bank_settlement(
        self,
        *,
        mode: str,
        reference: str,
        settled_at: str,
        amount: float,
        notes: str = '',
    ) -> dict[str, Any]:
        ref = str(reference or '').strip()
        if not ref:
            raise ValueError('Settlement reference is required.')
        val = float(amount)
        if val < 0:
            raise ValueError('Settlement amount must be nonnegative.')
        sid = uuid.uuid4().hex[:12]
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(self._connect()) as con, con:
            dup = con.execute('SELECT id FROM bank_settlements WHERE mode=? AND reference=?', (mode, ref)).fetchone()
            if dup:
                raise ValueError(f'Settlement reference "{ref}" already recorded.')
            con.execute(
                '''INSERT INTO bank_settlements(id, mode, reference, settled_at, amount, notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (sid, mode, ref, settled_at or stamp, val, str(notes or '')[:300], stamp),
            )
            row = con.execute('SELECT * FROM bank_settlements WHERE id=?', (sid,)).fetchone()
        return dict(row)

    def list_bank_settlements(self, *, mode: str, limit: int = 200) -> list[dict[str, Any]]:
        with engine.LOCK, closing(self._connect()) as con:
            rows = con.execute(
                'SELECT * FROM bank_settlements WHERE mode=? ORDER BY settled_at DESC, created_at DESC LIMIT ?',
                (mode, limit),
            ).fetchall()
        return [dict(r) for r in rows]
