from __future__ import annotations

from .core_errors import (
    BrmixSeriousProblemRule,
    DavOrEdddavErrorRule,
    TooFewBandsRule,
    ZhegvLapackFailureRule,
)
from .files import ElementOrderRule, FileMissingRule, PotcarPoscarMismatchRule
from .ionic import IonicReachedNswRule
from .kpoints import KpointsLineModeWithoutStaticRule
from .magnetic import LocalMomentCollapseRule, MagmomSignFlipRule
from .outcar import OutcarTruncatedRule
from .parameters import (
    EdiffgSignSemanticsRule,
    Icharg11ChgcarMissingRule,
    IonicControlConflictRule,
    IspinMagmomConflictRule,
    IsmearTetraForMetalRiskRule,
    LdauArrayLengthRule,
    LmaxmixTooLowForDftuRule,
    MagmomCountMismatchRule,
)
from .scheduler import (
    JobOomRule,
    JobTimeLimitRule,
    ModuleNotFoundRule,
    ParallelConfigRiskRule,
    PathOrFileNotFoundRule,
)
from .scf import ScfEnergyOscillationRule, ScfReachedNelmRule
from ..engine import Rule


def all_rules() -> list[Rule]:
    return [
        FileMissingRule(),
        ElementOrderRule(),
        PotcarPoscarMismatchRule(),
        LdauArrayLengthRule(),
        MagmomCountMismatchRule(),
        IspinMagmomConflictRule(),
        IonicControlConflictRule(),
        EdiffgSignSemanticsRule(),
        ScfReachedNelmRule(),
        ScfEnergyOscillationRule(),
        IonicReachedNswRule(),
        MagmomSignFlipRule(),
        LocalMomentCollapseRule(),
        JobOomRule(),
        JobTimeLimitRule(),
        ModuleNotFoundRule(),
        PathOrFileNotFoundRule(),
        ParallelConfigRiskRule(),
        BrmixSeriousProblemRule(),
        ZhegvLapackFailureRule(),
        TooFewBandsRule(),
        DavOrEdddavErrorRule(),
        Icharg11ChgcarMissingRule(),
        LmaxmixTooLowForDftuRule(),
        IsmearTetraForMetalRiskRule(),
        KpointsLineModeWithoutStaticRule(),
        OutcarTruncatedRule(),
    ]