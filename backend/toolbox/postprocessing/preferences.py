"""Local plotting defaults, isolated from credentials and page appearance."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..contracts import ToolboxError

SCHEMA_VERSION = 'pp.preferences.v1'
PRESETS = {
    'scientific': {'label': '科研通用（色盲友好）', 'colors': ['#0072B2', '#D55E00', '#009E73', '#CC79A7', '#333333', '#8B5C00']},
    'blue_orange': {'label': '蓝橙', 'colors': ['#2166AC', '#B35806', '#4393C3', '#D6604D', '#053061', '#67001F']},
    'muted': {'label': '低饱和', 'colors': ['#4C657A', '#916A4B', '#667B61', '#806C8A', '#8A6164', '#57606A']},
    'print': {'label': '黑白打印', 'colors': ['#111111', '#444444', '#777777', '#222222', '#555555', '#666666']},
}
_guard = threading.RLock()


class Palette(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    preset: Literal['scientific', 'blue_orange', 'muted', 'print', 'custom']
    colors: list[str] = Field(min_length=2, max_length=16)

    @model_validator(mode='after')
    def validate_colors(self):
        if any(not re.fullmatch(r'#[0-9a-fA-F]{6}', color) for color in self.colors):
            raise ValueError('颜色须为六位十六进制值，如 #0072B2')
        self.colors = [color.upper() for color in self.colors]
        if self.preset != 'custom' and self.colors != PRESETS[self.preset]['colors']:
            raise ValueError('修改预设颜色或顺序后须使用 custom 配色')
        return self


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal['pp.preferences.v1'] = SCHEMA_VERSION
    revision: int = Field(ge=0)
    palette: Palette


class SavePreferences(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=0)
    palette: Palette


def defaults() -> dict:
    return Preferences(revision=0, palette=Palette(preset='scientific', colors=list(PRESETS['scientific']['colors']))).model_dump()


class PlotPreferencesStore:
    """Reads have no write side effects; only explicit saves change defaults."""
    def __init__(self, root):
        self.path = Path(root) / 'postprocessing' / 'preferences.json'

    def read(self) -> dict:
        with _guard:
            if not self.path.exists():
                return defaults()
            try:
                return Preferences.model_validate(json.loads(self.path.read_text(encoding='utf-8'))).model_dump()
            except (OSError, ValueError, ValidationError) as exc:
                raise ToolboxError('PP_PREFERENCES_UNAVAILABLE', '绘图默认设置无法读取；请检查本地偏好文件，未覆盖原文件', 503) from exc

    def save(self, body: SavePreferences) -> dict:
        with _guard:
            current = self.read()
            if body.expected_revision != current['revision']:
                raise ToolboxError('PP_PREFERENCES_CONFLICT', '默认配色已在别处更新，请重新读取后保存', 409)
            result = Preferences(revision=current['revision'] + 1, palette=body.palette).model_dump()
            temporary = self.path.with_name(f'.preferences-{uuid.uuid4().hex}.tmp')
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
                os.replace(temporary, self.path)
            except OSError as exc:
                raise ToolboxError('PP_PREFERENCES_SAVE_FAILED', '默认配色保存失败，原设置未改变', 503) from exc
            finally:
                temporary.unlink(missing_ok=True)
            return result
