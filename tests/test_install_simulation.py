"""Fail-closed firmware-install simulation tests."""

from polaris_harness.install_simulation import InstallSimulator, InstallState


def valid_stage(simulator: InstallSimulator):
    return simulator.stage(
        extracted_tree=True,
        hash_match=True,
        manifest_match=True,
        camera_idle=True,
        free_space=True,
        rollback_available=True,
    )


def test_zip_only_stage_is_not_deployable():
    simulator = InstallSimulator()
    result = simulator.stage(
        extracted_tree=False,
        hash_match=True,
        manifest_match=True,
        camera_idle=True,
        free_space=True,
        rollback_available=True,
    )
    assert result.code == "extracted_tree_required"
    assert simulator.state == InstallState.STAGED_UNVERIFIED


def test_busy_camera_low_space_or_missing_rollback_blocks_staging():
    for field in ("camera_idle", "free_space", "rollback_available"):
        simulator = InstallSimulator()
        values = {
            "extracted_tree": True,
            "hash_match": True,
            "manifest_match": True,
            "camera_idle": True,
            "free_space": True,
            "rollback_available": True,
        }
        values[field] = False
        result = simulator.stage(**values)
        assert result.code in {
            "camera_busy",
            "insufficient_space",
            "rollback_missing",
        }
        assert simulator.state == InstallState.STAGED_UNVERIFIED


def test_ap_flap_without_uptime_reset_is_uncertain():
    simulator = InstallSimulator()
    assert valid_stage(simulator).state_after == InstallState.STAGED_VERIFIED
    result = simulator.reboot(
        ap_flap=True,
        uptime_reset=False,
        build_identity_match=True,
        hashes_match=True,
    )
    assert result.code == "no_uptime_reset"
    assert simulator.state == InstallState.REBOOT_UNCERTAIN


def test_identity_or_hash_mismatch_never_becomes_installed():
    for identity, hashes in ((False, True), (True, False)):
        simulator = InstallSimulator()
        valid_stage(simulator)
        result = simulator.reboot(
            ap_flap=True,
            uptime_reset=True,
            build_identity_match=identity,
            hashes_match=hashes,
        )
        assert result.code in {"build_identity_mismatch", "installed_hash_mismatch"}
        assert simulator.state == InstallState.REBOOT_UNCERTAIN


def test_verified_install_is_only_success_terminal_state():
    simulator = InstallSimulator()
    valid_stage(simulator)
    result = simulator.reboot(
        ap_flap=True,
        uptime_reset=True,
        build_identity_match=True,
        hashes_match=True,
    )
    assert result.code == "installed_verified"
    assert simulator.state == InstallState.INSTALLED_VERIFIED


def test_uncertain_install_can_be_marked_rollback_ready_without_device_mutation():
    simulator = InstallSimulator()
    valid_stage(simulator)
    simulator.reboot(
        ap_flap=True,
        uptime_reset=False,
        build_identity_match=True,
        hashes_match=True,
    )
    result = simulator.rollback(available=True)
    assert result.code == "rollback_ready"
    assert simulator.state == InstallState.ROLLED_BACK
    assert len(simulator.history) == 3
