"""ParameterProvenance 构建器（设计文档 7.19 节）。

每个最终参数恰有一条 provenance；重复记录视为构建错误。
source_type 枚举：recipe | derived_function | user_patch | rule_fix | scheduler_profile。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.app.schemas.generation import ParameterProvenance, ProvenanceSourceType


class ProvenanceError(Exception):
    pass


class ProvenanceBuilder:
    """provenance 累加器。

    同一参数被后续层覆盖时，先调用 ``remember`` 登记旧来源，再用 set_* 写入新来源；
    最终 ``build()`` 保证每个参数恰有一条 provenance（字典键唯一性 + 断言）。
    """

    def __init__(self, composition_id: str, revision: int = 1) -> None:
        self._composition_id = composition_id
        self._revision = revision
        self._entries: Dict[str, ParameterProvenance] = {}
        self._previous: Dict[str, Dict[str, Any]] = {}

    @property
    def _source_revision(self) -> str:
        return f"{self._composition_id}@{self._revision}"

    def _record(self, parameter: str, entry: ParameterProvenance) -> None:
        # 中间层覆盖允许替换；最终唯一性由 build() 断言保证。
        self._entries[parameter] = entry

    def _take_previous(self, parameter: str) -> Optional[Dict[str, Any]]:
        previous = self._previous.get(parameter)
        if previous is not None:
            self._previous.pop(parameter)
        return previous

    def remember(self, parameter: str, value: Any, source_type: str, source_id: str) -> None:
        """登记被覆盖前的来源，供下一条 provenance 的 overrode 字段使用。"""

        self._previous[parameter] = {
            "source_type": source_type,
            "source_id": source_id,
            "value": value,
        }

    def set_recipe(
        self, parameter: str, value: Any, recipe_key: str, confirmed: bool = True
    ) -> None:
        self._record(
            parameter,
            ParameterProvenance(
                parameter=parameter,
                value=value,
                source_type=ProvenanceSourceType.RECIPE,
                source_id=recipe_key,
                source_revision=self._source_revision,
                overrode=self._take_previous(parameter),
                confirmed=confirmed,
            ),
        )

    def set_derived(
        self,
        parameter: str,
        value: Any,
        function: str,
        inputs_ref: str,
        requires_confirmation: bool = False,
        confirmed: bool = False,
    ) -> None:
        self._record(
            parameter,
            ParameterProvenance(
                parameter=parameter,
                value=value,
                source_type=ProvenanceSourceType.DERIVED_FUNCTION,
                source_id=function,
                source_revision=self._source_revision,
                overrode=self._take_previous(parameter),
                derived_by=f"{function}({inputs_ref})",
                requires_confirmation=requires_confirmation,
                confirmed=confirmed,
            ),
        )

    def set_patch(self, parameter: str, value: Any, patch_id: str, confirmed: bool) -> None:
        self._record(
            parameter,
            ParameterProvenance(
                parameter=parameter,
                value=value,
                source_type=ProvenanceSourceType.USER_PATCH,
                source_id=patch_id,
                source_revision=self._source_revision,
                overrode=self._take_previous(parameter),
                confirmed=confirmed,
            ),
        )

    def set_scheduler_profile(self, parameter: str, value: Any, profile_id: str) -> None:
        self._record(
            parameter,
            ParameterProvenance(
                parameter=parameter,
                value=value,
                source_type=ProvenanceSourceType.SCHEDULER_PROFILE,
                source_id=profile_id,
                source_revision=self._source_revision,
                overrode=self._take_previous(parameter),
                confirmed=True,
            ),
        )

    def build(self) -> List[ParameterProvenance]:
        parameters = sorted(self._entries)
        if len(set(parameters)) != len(parameters):
            raise ProvenanceError("duplicate provenance detected")
        return [self._entries[name] for name in parameters]

    def has(self, parameter: str) -> bool:
        return parameter in self._entries
