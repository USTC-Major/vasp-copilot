"""Plot preferences are local, explicit, revisioned and independent of AI settings."""
import json

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from backend.toolbox.api import create_toolbox_app
from backend.toolbox.contracts import ToolboxError
from backend.toolbox.postprocessing.preferences import Palette, PlotPreferencesStore, PRESETS, SavePreferences

BASE = '/api/v1/toolbox/postprocessing/preferences'


def body(revision=0, colors=None, preset='custom'):
    return {'expected_revision': revision, 'palette': {'preset': preset, 'colors': colors or ['#0066aa', '#aa5500']}}


def test_missing_preferences_read_is_side_effect_free(tmp_path):
    store = PlotPreferencesStore(tmp_path)
    assert store.read()['palette'] == {'preset': 'scientific', 'colors': PRESETS['scientific']['colors']}
    assert store.read()['revision'] == 0
    assert not store.path.exists()
    first = store.read()
    first['palette']['colors'][0] = '#FFFFFF'
    assert store.read()['palette']['colors'][0] == '#0072B2'


def test_default_persists_order_and_is_independent_of_other_settings(tmp_path):
    (tmp_path / 'toolbox_config.json').write_text('{"llm_model":"unread-test-fixture"}', encoding='utf-8')
    store = PlotPreferencesStore(tmp_path)
    saved = store.save(SavePreferences.model_validate(body(colors=['#AA5500', '#0066AA', '#333333'])))
    assert saved['revision'] == 1
    assert PlotPreferencesStore(tmp_path).read() == saved
    assert saved['palette']['colors'] == ['#AA5500', '#0066AA', '#333333']
    assert (tmp_path / 'toolbox_config.json').read_text(encoding='utf-8') == '{"llm_model":"unread-test-fixture"}'
    assert not list(store.path.parent.glob('.preferences-*.tmp'))


def test_stale_revision_never_overwrites_new_default(tmp_path):
    store = PlotPreferencesStore(tmp_path)
    saved = store.save(SavePreferences.model_validate(body()))
    with pytest.raises(ToolboxError) as error:
        PlotPreferencesStore(tmp_path).save(SavePreferences.model_validate(body(colors=['#111111', '#777777'])))
    assert error.value.status == 409
    assert store.read() == saved


@pytest.mark.parametrize('palette', [
    {'preset': 'custom', 'colors': ['#FFFFFF']},
    {'preset': 'custom', 'colors': ['#111111'] * 17},
    {'preset': 'custom', 'colors': ['red', '#111111']},
    {'preset': 'custom', 'colors': ['#fff', '#111111']},
    {'preset': 'custom', 'colors': ['#GGGGGG', '#111111']},
    {'preset': 'scientific', 'colors': ['#FFFFFF', '#000000']},
    {'preset': 'custom', 'colors': ['#111111', '#777777'], 'background': 'dark'},
])
def test_invalid_palette_rejected(palette):
    with pytest.raises(ValidationError):
        Palette.model_validate(palette)


@pytest.mark.parametrize('preset', PRESETS)
def test_presets_are_canonical(preset):
    assert Palette(preset=preset, colors=PRESETS[preset]['colors']).colors == PRESETS[preset]['colors']


def test_corrupt_preferences_not_silently_replaced(tmp_path):
    store = PlotPreferencesStore(tmp_path)
    store.path.parent.mkdir()
    store.path.write_text('{"schema_version":"old"}', encoding='utf-8')
    with pytest.raises(ToolboxError) as error:
        store.save(SavePreferences.model_validate(body()))
    assert error.value.code == 'PP_PREFERENCES_UNAVAILABLE'
    assert store.path.read_text(encoding='utf-8') == '{"schema_version":"old"}'


def test_http_no_model_required_and_restart_restores_default(tmp_path):
    with TestClient(create_toolbox_app(root=tmp_path, monitor_enabled=False)) as client:
        response = client.get(BASE)
        assert response.status_code == 200
        payload = response.json()
        assert payload['preferences']['revision'] == 0
        assert {preset['id'] for preset in payload['presets']} == set(PRESETS)
        assert not (tmp_path / 'postprocessing' / 'preferences.json').exists()
        assert client.put(BASE, json=body()).status_code == 200
        assert client.put(BASE, json=body()).status_code == 409
        assert client.put(BASE, json={**body(1), 'theme': 'dark'}).status_code == 422
        assert client.put(BASE, json=body(1, colors=['#FFFFFF'])).status_code == 422
    with TestClient(create_toolbox_app(root=tmp_path, monitor_enabled=False)) as client:
        restored = client.get(BASE).json()['preferences']
        assert restored['revision'] == 1
        assert restored['palette']['colors'] == ['#0066AA', '#AA5500']
        assert json.loads((tmp_path / 'postprocessing' / 'preferences.json').read_text(encoding='utf-8')) == restored
