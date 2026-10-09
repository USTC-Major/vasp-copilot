"""Independent Toolbox API for global plotting/export defaults."""
from fastapi import APIRouter, Request

from .preferences import PRESETS, PlotPreferencesStore, SavePreferences

router = APIRouter(prefix='/postprocessing/preferences', tags=['Plot preferences'])


@router.get('')
def read_preferences(request: Request):
    return {'mode': 'toolbox', 'preferences': PlotPreferencesStore(request.app.state.toolbox.root).read(),
            'presets': [{'id': ident, **value} for ident, value in PRESETS.items()]}


@router.put('')
def save_preferences(request: Request, body: SavePreferences):
    return {'mode': 'toolbox', 'preferences': PlotPreferencesStore(request.app.state.toolbox.root).save(body)}
