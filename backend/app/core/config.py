from __future__ import annotations

import os
from dataclasses import dataclass, field

_DEFAULT_CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


@dataclass
class FeatureFlagConfig:
    '''功能开关（与 MVP 7.23 对齐）。'''

    band_feature: bool = False
    potcar_concat: bool = False
    llm_enabled: bool = False
    local_fake_hpc: bool = True


@dataclass
class MaterialsProjectConfig:
    '''Materials Project REST API 配置（MP_API_KEY 门控）。'''
    enabled: bool = False
    api_key: str = ''
    base_url: str = 'https://api.materialsproject.org'
    timeout_seconds: float = 40.0

@dataclass
class LlmConfig:
    '''LLM provider 配置（MVP 5.4：超时/重试/降级，无二进制访问）。'''

    enabled: bool = False
    base_url: str = 'https://api.openai.com/v1'
    api_key: str = ''
    model: str = 'gpt-4o-mini'
    timeout_seconds: float = 30.0
    max_retries: int = 1
    max_tokens: int = 1024
    temperature: float = 0.2

    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.api_key.strip())


@dataclass
class Settings:
    app_name: str = 'vasp-doctor'
    api_prefix: str = '/api/v1'
    data_dir: str = 'data'
    ttl_seconds: int = 24 * 3600
    orphan_run_cleanup: bool = False

    max_upload_bytes: int = 200 * 1024 * 1024
    max_uncompressed_bytes: int = 1_500 * 1024 * 1024
    max_file_count: int = 2000
    max_uncompression_ratio: float = 100.0

    outcar_preview_lines: int = 500
    max_preview_bytes: int = 512 * 1024
    max_preview_lines: int = 1000
    max_evidence_excerpt: int = 512

    cors_origins: list[str] = field(default_factory=lambda: list(_DEFAULT_CORS_ORIGINS))
    feature_flags: FeatureFlagConfig = field(default_factory=FeatureFlagConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    materials_project: MaterialsProjectConfig = field(default_factory=MaterialsProjectConfig)

    @classmethod
    def from_env(cls) -> 'Settings':
        cors = [o.strip() for o in os.getenv('CORS_ORIGINS', '').split(',') if o.strip()]
        return cls(
            data_dir=os.getenv('DATA_DIR', 'data'),
            max_upload_bytes=int(os.getenv('MAX_UPLOAD_BYTES', 200 * 1024 * 1024)),
            max_uncompressed_bytes=int(
                os.getenv('MAX_UNCOMPRESSED_BYTES', 1_500 * 1024 * 1024)
            ),
            max_file_count=int(os.getenv('MAX_FILE_COUNT', 2000)),
            max_uncompression_ratio=float(
                os.getenv('MAX_UNCOMPRESSION_RATIO', 100.0)
            ),
            ttl_seconds=int(os.getenv('TTL_SECONDS', 24 * 3600)),
            orphan_run_cleanup=(
                os.getenv('ORPHAN_RUN_CLEANUP', 'false').lower() == 'true'
            ),
            outcar_preview_lines=int(os.getenv('MAX_OUTCAR_PREVIEW_LINES',
                             os.getenv('OUTCAR_PREVIEW_LINES', '500'))),
            max_preview_bytes=int(os.getenv('MAX_PREVIEW_BYTES', 512 * 1024)),
            max_preview_lines=int(os.getenv('MAX_PREVIEW_LINES', 1000)),
            max_evidence_excerpt=int(os.getenv('MAX_EVIDENCE_EXCERPT', 512)),
            cors_origins=cors or list(_DEFAULT_CORS_ORIGINS),
            feature_flags=FeatureFlagConfig(
                band_feature=os.getenv(
                    'ENABLE_BAND_WORKFLOW', os.getenv('ENABLE_BAND', 'false')
                ).lower() == 'true',
                potcar_concat=os.getenv(
                    'ENABLE_POTCAR_ASSEMBLY', os.getenv('ENABLE_POTCAR_CONCAT', 'false')
                ).lower() == 'true',
                llm_enabled=os.getenv('ENABLE_LLM', 'false').lower() == 'true',
                local_fake_hpc=os.getenv('ENABLE_LOCAL_FAKE_HPC', 'true')
                                 .lower() == 'true',
            ),
            materials_project=MaterialsProjectConfig(
                api_key=os.getenv('MP_API_KEY', ''),
                enabled=os.getenv('ENABLE_MATERIALS_PROJECT', 'false') == 'true',
            ),
            llm=LlmConfig(
                enabled=os.getenv('ENABLE_LLM', 'false').lower() == 'true',
                base_url=os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1'),
                api_key=os.getenv('LLM_API_KEY', ''),
                model=os.getenv('LLM_MODEL', 'gpt-4o-mini'),
                timeout_seconds=float(os.getenv('LLM_TIMEOUT_SECONDS', '30')),
                max_retries=int(os.getenv('LLM_MAX_RETRIES', '1')),
                max_tokens=int(os.getenv('LLM_MAX_TOKENS', '1024')),
                temperature=float(os.getenv('LLM_TEMPERATURE', '0.2')),
            ),
        )