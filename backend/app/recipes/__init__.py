"""BE-A recipes 包：Recipe Pack 加载、选择、组合、派生、覆盖与 provenance。

依赖方向：recipes → schemas；不得反向依赖 workflow/generators。
"""

from backend.app.recipes.composer import ComposeRequest, RecipeComposer
from backend.app.recipes.derived import DerivedParameterResolver
from backend.app.recipes.errors import BeAError
from backend.app.recipes.loader import RecipePackLoader
from backend.app.recipes.overrides import OverrideValidator
from backend.app.recipes.registry import RecipeRegistry, default_registry
from backend.app.recipes.selector import RecipeSelector

__all__ = [
    "BeAError",
    "ComposeRequest",
    "DerivedParameterResolver",
    "OverrideValidator",
    "RecipeComposer",
    "RecipePackLoader",
    "RecipeRegistry",
    "RecipeSelector",
    "default_registry",
]
