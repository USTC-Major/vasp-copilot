"""M10 报告子包：提取 + 报告渲染 + 清理建议（只读，绝不自动删）。"""
from .cleanup import CleanupSuggestion, cleanup_text, suggest_cleanup
from .extract import OutcarSummary, OszicarSummary, parse_outcar, parse_osziacar, summarize_run
from .render import JobResultItem, RefineFn, ReportReport, render_report

__all__ = [
    "CleanupSuggestion", "cleanup_text", "suggest_cleanup",
    "OutcarSummary", "OszicarSummary", "parse_outcar", "parse_osziacar",
    "summarize_run", "JobResultItem", "RefineFn", "ReportReport", "render_report",
]
