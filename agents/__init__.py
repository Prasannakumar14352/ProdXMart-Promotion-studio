"""Modular AI Agent Center package for ProdXMart Promotion Studio."""

from agents.base import (
    AgentCancelledError,
    AgentConfigurationError,
    AgentContext,
    AgentExecutionError,
    AgentMetadata,
    AgentResult,
    BaseAgent,
)
from agents.catalog_adapter import (
    CatalogPage,
    CsvSupplierAdapter,
    ExtensionBatchAdapter,
    FactoriApiAdapter,
    LocalStoreCatalogAdapter,
    SupplierCatalogAdapter,
    SupplierProductRecord,
    build_supplier_adapter,
)
from agents.catalog_agent import CatalogAgent
from agents.marketing_agent import MarketingAgent, create_campaign_drafts_for_items
from agents.marketing_schema import (
    SUPPORTED_CAMPAIGN_TYPES,
    SUPPORTED_LANGUAGES,
    build_fallback_product_marketing,
    build_video_handoff_payload,
    validate_product_marketing_item,
)
from agents.ollama_client import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    OllamaClient,
    OllamaConfig,
    OllamaError,
    normalize_ollama_url,
)
from agents.orchestrator import MasterOrchestrator, build_default_orchestrator
from agents.qa_agent import QualityAssuranceAgent, inspect_video_with_ffprobe, verify_job_for_publishing
from agents.credentials import SecureCredentialStore
from agents.social_adapters import (
    ManualPackageAdapter,
    MetaReelsApiAdapter,
    PublishOutcome,
    SUPPORTED_PLATFORMS,
    SocialPublisherAdapter,
    YouTubeShortsApiAdapter,
    build_manual_publishing_package,
    build_social_adapter,
)
from agents.social_agent import (
    SOCIAL_STATUSES,
    SocialMediaAgent,
    approve_social_post,
    confirm_social_post_published,
    edit_social_post,
    publish_or_prepare_social_post,
    reconcile_social_approvals,
    reject_social_post,
    schedule_social_post,
)
from agents.registry import (
    CatalogueAuditorAgent,
    PerformanceAnalystAgent,
    ReelCopyStrategistAgent,
    build_default_agents,
)
from agents.storage import AgentStorage
from agents.tts_provider import (
    HttpTtsProvider,
    RecordedAudioProvider,
    VoiceoverProvider,
    WindowsSapiTtsProvider,
    build_narration_track,
)
from agents.video_agent import SUPPORTED_DURATIONS, VideoProductionAgent

__all__ = [
    'AgentCancelledError',
    'AgentConfigurationError',
    'AgentContext',
    'AgentExecutionError',
    'AgentMetadata',
    'AgentResult',
    'AgentStorage',
    'BaseAgent',
    'CatalogAgent',
    'CatalogPage',
    'CatalogueAuditorAgent',
    'CsvSupplierAdapter',
    'DEFAULT_MODEL',
    'DEFAULT_OLLAMA_URL',
    'ExtensionBatchAdapter',
    'FactoriApiAdapter',
    'HttpTtsProvider',
    'LocalStoreCatalogAdapter',
    'MarketingAgent',
    'MasterOrchestrator',
    'OllamaClient',
    'OllamaConfig',
    'OllamaError',
    'PerformanceAnalystAgent',
    'QualityAssuranceAgent',
    'RecordedAudioProvider',
    'ReelCopyStrategistAgent',
    'SUPPORTED_CAMPAIGN_TYPES',
    'SUPPORTED_DURATIONS',
    'SUPPORTED_LANGUAGES',
    'SUPPORTED_PLATFORMS',
    'SOCIAL_STATUSES',
    'SecureCredentialStore',
    'SocialMediaAgent',
    'SocialPublisherAdapter',
    'ManualPackageAdapter',
    'MetaReelsApiAdapter',
    'PublishOutcome',
    'YouTubeShortsApiAdapter',
    'approve_social_post',
    'confirm_social_post_published',
    'edit_social_post',
    'publish_or_prepare_social_post',
    'reconcile_social_approvals',
    'reject_social_post',
    'schedule_social_post',
    'build_manual_publishing_package',
    'build_social_adapter',
    'SupplierCatalogAdapter',
    'SupplierProductRecord',
    'VideoProductionAgent',
    'VoiceoverProvider',
    'WindowsSapiTtsProvider',
    'build_default_agents',
    'build_default_orchestrator',
    'build_fallback_product_marketing',
    'build_narration_track',
    'build_supplier_adapter',
    'build_video_handoff_payload',
    'create_campaign_drafts_for_items',
    'inspect_video_with_ffprobe',
    'normalize_ollama_url',
    'validate_product_marketing_item',
    'verify_job_for_publishing',
]
