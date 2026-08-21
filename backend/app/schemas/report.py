from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class ReportMetadata(BaseModel):
    """MVP 7.8（Markdown 报告）。"""

    model_config = ConfigDict(extra="ignore")

    report_id: str
    diagnosis_id: str
    format: str = "markdown"
    language: str = "zh"
    title: str = ""
    generated_at: str = ""
    size_bytes: int = 0
    sha256: Optional[str] = None
    sections: list[str] = []
    download_url: Optional[str] = None
    generator_version: str = "0.1.0"