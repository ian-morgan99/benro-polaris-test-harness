"""
ARM user-space emulation package for Stage-2 lifecycle tests.
Copyright (C) 2026  Ian Morgan

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

Provides Unicorn-based ARM (Thumb) CPU emulation for exact, provenance-identified
ARM binaries or extracted code regions. This is an independent harness component
and does not depend on the Polaris protocol scenario engine or runtime verifier.

Public API:
- ARMEmulator: Core Unicorn-based ARM emulator
- EmulationConfig: Configuration for an emulation run
- EmulationResult: Emulation outcome enumeration
- EmulationResultRecord: Machine-readable result record
- StubRegistry: Central registry of stubs with call recording
- StubProfile: Named collection of stub behaviors
- FixtureManager: Manages ARM fixture binaries with integrity verification
- FixtureMetadata: Metadata for an ARM fixture binary
- ArmEmulationContract: Machine-readable invocation/result contract
- ContractVersion: Contract schema version
- LifecycleCase: The 9 #251 lifecycle test cases
- LifecycleScenario: Base class for lifecycle scenarios
- run_all_lifecycle_cases: Run all 9 lifecycle test cases
- Case1NormalBulbCompletion through Case9ManualNonBControl: Individual scenarios
"""

from .contract import (
    ArmEmulationContract,
    ContractVersion,
    load_contract,
)
from .emulator import (
    ARMEmulator,
    EmulationConfig,
    EmulationResult,
    EmulationResultRecord,
    StubCallRecord,
)
from .fixtures import FixtureManager, FixtureMetadata
from .lifecycle import (
    Case1NormalBulbCompletion,
    Case2MessageStopVsContextCancel,
    Case3CancelResultSurvivesStop,
    Case4StopFailurePreservesPrimaryError,
    Case5DeferredTimedCaptureCancelled,
    Case6NormalTimerExpiry,
    Case7CallbackOrderingRegisterPreservation,
    Case8OutputPublicationCleanup,
    Case9ManualNonBControl,
    LifecycleCase,
    LifecycleScenario,
    LifecycleTestResult,
    run_all_lifecycle_cases,
)
from .stubs import (
    StubProfile,
    StubRegistry,
    create_default_profile,
    create_strict_profile,
    default_camera_stub,
    default_callback_stub,
    default_libgphoto_stub,
    default_os_stub,
    default_timer_stub,
)

__all__ = [
    "ARMEmulator",
    "EmulationConfig",
    "EmulationResult",
    "EmulationResultRecord",
    "StubCallRecord",
    "StubRegistry",
    "StubProfile",
    "create_default_profile",
    "create_strict_profile",
    "default_camera_stub",
    "default_callback_stub",
    "default_libgphoto_stub",
    "default_os_stub",
    "default_timer_stub",
    "FixtureManager",
    "FixtureMetadata",
    "ArmEmulationContract",
    "ContractVersion",
    "load_contract",
    "LifecycleCase",
    "LifecycleScenario",
    "LifecycleTestResult",
    "Case1NormalBulbCompletion",
    "Case2MessageStopVsContextCancel",
    "Case3CancelResultSurvivesStop",
    "Case4StopFailurePreservesPrimaryError",
    "Case5DeferredTimedCaptureCancelled",
    "Case6NormalTimerExpiry",
    "Case7CallbackOrderingRegisterPreservation",
    "Case8OutputPublicationCleanup",
    "Case9ManualNonBControl",
    "run_all_lifecycle_cases",
]
