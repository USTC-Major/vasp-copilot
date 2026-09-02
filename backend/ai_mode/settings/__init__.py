"""M11 设置子包：全局设置 + 项目级额外设置（后端支撑）。"""
from .global_api import (
    SETTABLE_FIELDS,
    SECRET_FIELDS,
    mask_config,
    persist,
    check_connection,
    update_from_patch,
    store_ssh_password,
    get_ssh_password,
    secret_status,
    writable_fields,
)
from .project import (
    PROJECTS_DIRNAME,
    ProjectSettingsError,
    ProjectSettingsStore,
    sanitize_project_id,
    normalize_accuracy,
    render_accuracy_text,
    validate_accuracy,
    require_valid_accuracy,
    project_settings_path,
)

__all__ = [
    "SETTABLE_FIELDS", "SECRET_FIELDS", "mask_config", "persist",
    "check_connection", "update_from_patch",
    "store_ssh_password", "get_ssh_password", "secret_status",
    "writable_fields",
    "PROJECTS_DIRNAME", "ProjectSettingsError", "ProjectSettingsStore",
    "sanitize_project_id", "normalize_accuracy", "render_accuracy_text",
    "validate_accuracy", "require_valid_accuracy", "project_settings_path",
]