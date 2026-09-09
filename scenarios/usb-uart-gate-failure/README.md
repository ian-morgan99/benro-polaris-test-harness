# USB-UART Gate Failure Scenario

## What was physically observed?

From SSH evidence (`probes-20260904-122940/01-first-look.txt`):

- Device has Broadcom WiFi chip (ID 1a40:0101), not USB-UART adapter
- `/dev/ttyUSB*` devices are absent
- `usb_f_acm.ko` and `usb_f_uvc.ko` are in `/app/komod/` but not loaded
- `SP_TtyUsbUartInit` gate likely fails because no USB serial device is present

This is the root cause of why the firmware install flow never triggers.

## On exactly what camera/firmware/runtime/source SHA?

- **Camera**: Pentax K-3 III (ID 25fb:0189)
- **Firmware**: 4.0.0.32 (2025.05.09)
- **Polaris**: 4.0.0.32
- **FwPkt.zip**: MD5 `92da888387b14dc02976b5fa22b94067`
- **Source**: SSH evidence from 2026-09-04 post-reboot capture

## At which layer was it observed?

**Polaris-runtime layer** - The failure occurs in `polestar_app` during the firmware upgrade state machine, specifically at the `SP_TtyUsbUartInit` gate check before MD5 comparison.

## What does the harness reproduce?

The harness reproduces the exact USB-UART gate failure:

1. Client sends `SP_TtyUsbUartInit` request
2. Harness responds with USB-UART device not found error
3. Client proceeds to `SP_UpgradeCheckFw`
4. Harness responds that USB-UART gate failed, aborting upgrade
5. Harness emits upgrade_aborted marker

## What does the harness deliberately not claim?

- This scenario does NOT prove physical camera compatibility
- It does NOT prove libgphoto2 correctly implements the camera
- It does NOT prove Stage-2 runtime actually loads intended libraries
- It does NOT prove firmware image is safe to flash
- It does NOT prove OpenPolaris works end-to-end with a real device

## Which owning issue records the root cause/fix?

**Owning issue**: `ian-morgan99/benro-polaris-firmware-patcher#37` (USB-UART gate investigation)

## What should a consumer do when this scenario occurs?

1. **Verify USB-UART device presence**: Check if `/dev/ttyUSB*` devices exist
2. **Inspect kernel modules**: Ensure `usb_f_acm.ko` is loaded
3. **Check device tree**: Verify USB device tree includes UART adapter
4. **Alternative upgrade path**: Consider using OmsPkt.zip path if available
5. **Manual intervention**: If USB-UART cannot be provided, manually extract FwPkt.zip to `/app/sd/FwPkt/` directory

## Scenario Details

### Technical Details

- **Evidence ID**: ssh-evidence-probes-20260904-122940
- **Observed Date**: 2026-09-04
- **Uptime**: 20 minutes at capture time
- **Process**: `polestar_app` PID 248
- **Key Finding**: No USB-UART gate, install flow never triggers

### Why This Matters

The USB-UART gate is a critical pre-condition for firmware upgrades. Without it:

1. **Automatic upgrades fail**: `SP_UpgradeCheckFw` bails out before MD5 comparison
2. **Manual upgrades require intervention**: Must extract FwPkt.zip to `/app/sd/FwPkt/`
3. **Root cause confusion**: Easy to misattribute failures to other layers

### Test Harness Value

This scenario provides a deterministic regression test for:

- USB-UART gate detection and failure handling
- Consumer behavior when upgrade pre-conditions are not met
- Error message consistency across different camera models
- Alternative upgrade path availability

### Evidence References

- SSH evidence: `PrivateResearch/openpolaris-research/Benro-Firmware/SSH-evidence/probes-20260904-122940/`
- Pre-probe state: `PrivateResearch/openpolaris-research/Benro-Firmware/01-pre-probe-state.txt`
- Post-update probes: `PrivateResearch/openpolaris-research/Benro-Firmware/post-update-probes-20260901-112631/`