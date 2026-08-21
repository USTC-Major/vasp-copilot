"""验收 1/2：Recipe schema fail closed（未知字段、表达式、URL、eval 一律拒绝）。"""

import textwrap

import pytest

from backend.app.recipes.errors import RecipeSchemaInvalid
from backend.app.recipes.loader import RecipePackLoader
from backend.app.schemas.recipe import RecipeManifest


def _write_recipe(tmp_path, body: str):
    path = tmp_path / "evil.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


BASE_RECIPE = """
schema_version: "1.0"
recipe_id: task.test
version: 1.0.0
kind: task
recipe_status: draft
scope:
  tasks: [relax]
parameters:
  NSW: 10
"""


class TestSchemaFailClosed:
    def test_unknown_field_rejected(self):
        with pytest.raises(Exception):
            RecipeManifest.model_validate(
                {
                    "recipe_id": "x",
                    "version": "1.0.0",
                    "kind": "task",
                    "not_a_field": True,
                }
            )

    def test_template_expression_rejected(self, tmp_path):
        path = _write_recipe(
            tmp_path,
            BASE_RECIPE
            + "description: \"value ${injected}\"\n",
        )
        with pytest.raises(RecipeSchemaInvalid):
            RecipePackLoader().load_recipe(path)

    def test_jinja_expression_rejected(self, tmp_path):
        path = _write_recipe(
            tmp_path,
            BASE_RECIPE + "description: \"{{ lookup }}\"\n",
        )
        with pytest.raises(RecipeSchemaInvalid):
            RecipePackLoader().load_recipe(path)

    def test_url_rejected(self, tmp_path):
        path = _write_recipe(
            tmp_path,
            BASE_RECIPE + "description: \"see http://example.com/incar\"\n",
        )
        with pytest.raises(RecipeSchemaInvalid):
            RecipePackLoader().load_recipe(path)

    def test_eval_rejected(self, tmp_path):
        path = _write_recipe(
            tmp_path,
            BASE_RECIPE + "description: \"eval(something)\"\n",
        )
        with pytest.raises(RecipeSchemaInvalid):
            RecipePackLoader().load_recipe(path)

    def test_import_rejected(self, tmp_path):
        path = _write_recipe(
            tmp_path,
            BASE_RECIPE + "description: \"import os\"\n",
        )
        with pytest.raises(RecipeSchemaInvalid):
            RecipePackLoader().load_recipe(path)

    def test_dunder_rejected(self, tmp_path):
        path = _write_recipe(
            tmp_path,
            BASE_RECIPE + "description: \"__globals__\"\n",
        )
        with pytest.raises(RecipeSchemaInvalid):
            RecipePackLoader().load_recipe(path)

    def test_shell_substitution_rejected(self, tmp_path):
        path = _write_recipe(
            tmp_path,
            BASE_RECIPE + "description: \"$(cat /etc/passwd)\"\n",
        )
        with pytest.raises(RecipeSchemaInvalid):
            RecipePackLoader().load_recipe(path)

    def test_valid_recipe_passes(self, tmp_path):
        path = _write_recipe(tmp_path, BASE_RECIPE)
        manifest = RecipePackLoader().load_recipe(path)
        assert manifest.recipe_id == "task.test"
        assert manifest.sha256

    def test_published_requires_tests_passed(self):
        with pytest.raises(Exception):
            RecipeManifest.model_validate(
                {
                    "recipe_id": "x",
                    "version": "1.0.0",
                    "kind": "task",
                    "recipe_status": "published",
                    "tests": {"test_status": "not_run"},
                }
            )

    def test_derived_function_must_be_identifier(self):
        with pytest.raises(Exception):
            RecipeManifest.model_validate(
                {
                    "recipe_id": "x",
                    "version": "1.0.0",
                    "kind": "task",
                    "derived_parameters": [
                        {"function": "os.system('ls')", "parameter": "X"}
                    ],
                }
            )

    def test_registry_immutable_revision(self, registry, tmp_path):
        from backend.app.recipes.errors import RecipeSchemaInvalid as RSI

        manifest = registry.get_by_id("base.vasp")
        tampered = manifest.model_copy(deep=True)
        tampered.sha256 = "f" * 64
        with pytest.raises(RSI):
            registry.register_recipe(tampered)
