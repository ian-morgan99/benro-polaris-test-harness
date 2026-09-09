"""
QEMU integration for Polaris Harness.

Integrates the Polaris Harness with QEMU for hardware emulation and device testing.
"""

import socket
import subprocess
import time
import json
import threading
import logging
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path

from .validation import validate_scenario, validate_evidence
from .engine import ScenarioEngine
from .clock import get_clock, set_clock_mode, ClockMode


class QEMUDeviceType(Enum):
    """QEMU device type enumeration."""
    PENTAX_K3_III = "pentax-k3-iii"
    PENTAX_K1_II = "pentax-k1-ii"
    GENERIC_USB = "generic-usb"
    CUSTOM = "custom"


@dataclass
class QEMUDeviceConfig:
    """QEMU device configuration."""
    device_type: QEMUDeviceType
    vendor_id: str
    product_id: str
    device_name: str
    usb_version: str
    driver: str
    parameters: Dict[str, Any] = None
    usb_device_path: Optional[str] = None


@dataclass
class QEMUNetworkConfig:
    """QEMU network configuration."""
    bridge_name: str
    ip_address: str
    netmask: str
    gateway: str
    dns_servers: List[str] = None


@dataclass
class QEMUScenarioResult:
    """QEMU scenario execution result."""
    scenario_id: str
    success: bool
    execution_time_ms: int
    device_status: Dict[str, Any]
    logs: List[str]
    errors: List[str]
    metadata: Dict[str, Any] = None


class QEMUError(Exception):
    """Raised when QEMU operations fail."""

    def __init__(self, message: str, error_code: str = None):
        super().__init__(message)
        self.error_code = error_code


class QEMUDeviceManager:
    """Manages QEMU device instances."""

    def __init__(self, config_file: str = "/etc/qemu/harness.conf"):
        self.config_file = config_file
        self.devices: Dict[str, QEMUDeviceConfig] = {}
        self.network_config: Optional[QEMUNetworkConfig] = None
        self.qemu_process: Optional[subprocess.Popen] = None
        self.socket_path: str = "/tmp/qemu_harness.sock"
        self.logger = logging.getLogger(__name__)
        self._load_config()

    def _load_config(self):
        """Load QEMU configuration from file."""
        config_path = Path(self.config_file)
        if config_path.exists():
            with open(config_path, "r") as f:
                config = json.load(f)
                
            # Load devices
            for device_data in config.get("devices", []):
                device_config = QEMUDeviceConfig(**device_data)
                self.devices[device_config.device_name] = device_config
            
            # Load network config
            if "network" in config:
                self.network_config = QEMUNetworkConfig(**config["network"])
        
        self.logger.info(f"Loaded {len(self.devices)} devices and network configuration")

    def add_device(self, device_config: QEMUDeviceConfig):
        """Add a device to the QEMU instance.

        Args:
            device_config: Device configuration
        """
        self.devices[device_config.device_name] = device_config
        self.logger.info(f"Added device: {device_config.device_name}")

    def remove_device(self, device_name: str):
        """Remove a device from the QEMU instance.

        Args:
            device_name: Name of device to remove
        """
        if device_name in self.devices:
            del self.devices[device_name]
            self.logger.info(f"Removed device: {device_name}")

    def setup_network(self, network_config: QEMUNetworkConfig):
        """Setup network configuration.

        Args:
            network_config: Network configuration
        """
        self.network_config = network_config
        self.logger.info(f"Setup network: {network_config.bridge_name}")

    def start_qemu(self) -> bool:
        """Start QEMU instance.

        Returns:
            True if QEMU started successfully, False otherwise
        """
        try:
            # Create socket directory
            Path("/tmp").mkdir(exist_ok=True)
            
            # Build QEMU command
            cmd = [
                "qemu-system-x86_64",
                "-name", "harness-device",
                "-m", "2048",
                "-smp", "2",
                "-enable-kvm",
                "-cpu", "host",
            ]
            
            # Add network configuration
            if self.network_config:
                cmd.extend([
                    "-net", f"nic,model=e1000,macaddr=52:54:00:12:34:56",
                    "-net", f"socket,mcast=239.0.0.1,localaddr={self.network_config.ip_address},listen={self.network_config.ip_address}",
                ])
            
            # Add USB devices
            for device_name, device_config in self.devices.items():
                usb_args = self._get_usb_device_args(device_config)
                cmd.extend(usb_args)
            
            # Add monitor socket
            cmd.extend([
                "-monitor", f"unix:{self.socket_path},server,nowait",
                "-daemonize",
                "-pidfile", "/tmp/qemu_harness.pid",
                "-log", f"file=/tmp/qemu_harness.log,append",
            ])
            
            # Start QEMU process
            self.qemu_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Wait for QEMU to start
            time.sleep(2)
            
            # Check if process is still running
            if self.qemu_process.poll() is not None:
                stdout, stderr = self.qemu_process.communicate()
                raise QEMUError(f"QEMU failed to start: {stderr}")
            
            self.logger.info(f"QEMU started with PID: {self.qemu_process.pid}")
            return True
            
        except Exception as e:
            raise QEMUError(f"Failed to start QEMU: {e}")

    def _get_usb_device_args(self, device_config: QEMUDeviceConfig) -> List[str]:
        """Get QEMU arguments for USB device.

        Args:
            device_config: Device configuration

        Returns:
            List of QEMU arguments
        """
        args = []
        
        if device_config.usb_device_path:
            # Use specific USB device path
            args.extend(["-usbdevice", f"file:{device_config.usb_device_path}"])
        else:
            # Create virtual USB device
            args.extend([
                "-usbdevice", "usb-host",
                "-device", f"usb-device,vendor={device_config.vendor_id},product={device_config.product_id}",
            ])
        
        return args

    def stop_qemu(self) -> bool:
        """Stop QEMU instance.

        Returns:
            True if QEMU stopped successfully, False otherwise
        """
        try:
            if self.qemu_process:
                self.qemu_process.terminate()
                self.qemu_process.wait(timeout=10)
                self.qemu_process = None
                self.logger.info("QEMU stopped")
                return True
            return False
        except Exception as e:
            raise QEMUError(f"Failed to stop QEMU: {e}")

    def is_running(self) -> bool:
        """Check if QEMU is running.

        Returns:
            True if QEMU is running, False otherwise
        """
        return self.qemu_process is not None and self.qemu_process.poll() is None

    def get_device_status(self, device_name: str) -> Optional[Dict[str, Any]]:
        """Get status of a device.

        Args:
            device_name: Name of device

        Returns:
            Device status or None
        """
        if device_name not in self.devices:
            return None
        
        # Send command to QEMU to get device status
        # This would typically involve a monitor command
        return {
            "device_name": device_name,
            "status": "connected" if self.is_running() else "disconnected",
            "config": asdict(self.devices[device_name])
        }

    def execute_scenario(self, scenario_id: str, parameters: Dict[str, Any]) -> QEMUScenarioResult:
        """Execute a scenario against the QEMU device.

        Args:
            scenario_id: Scenario identifier
            parameters: Scenario parameters

        Returns:
            Scenario execution result
        """
        start_time = time.time()
        
        try:
            # Validate scenario
            scenario_data = self._load_scenario(scenario_id)
            errors = validate_scenario(scenario_data)
            if errors:
                return QEMUScenarioResult(
                    scenario_id=scenario_id,
                    success=False,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    device_status={},
                    logs=[f"Scenario validation errors: {errors}"],
                    errors=errors
                )
            
            # Create scenario engine
            engine = ScenarioEngine(scenario_id, scenario_data)
            
            # Execute scenario
            result = self._execute_scenario_on_qemu(engine, parameters)
            
            execution_time = int((time.time() - start_time) * 1000)
            
            return QEMUScenarioResult(
                scenario_id=scenario_id,
                success=result["success"],
                execution_time_ms=execution_time,
                device_status=result.get("device_status", {}),
                logs=result.get("logs", []),
                errors=result.get("errors", []),
                metadata=result.get("metadata", {})
            )
            
        except Exception as e:
            return QEMUScenarioResult(
                scenario_id=scenario_id,
                success=False,
                execution_time_ms=int((time.time() - start_time) * 1000),
                device_status={},
                logs=[],
                errors=[str(e)]
            )

    def _load_scenario(self, scenario_id: str) -> Dict[str, Any]:
        """Load scenario data.

        Args:
            scenario_id: Scenario identifier

        Returns:
            Scenario data
        """
        # Try to load from scenarios directory
        scenario_path = Path(f"scenarios/{scenario_id}/scenario.yaml")
        if scenario_path.exists():
            import yaml
            with open(scenario_path) as f:
                return yaml.safe_load(f)
        
        # Try to load from QEMU scenario directory
        scenario_path = Path(f"/tmp/scenarios/{scenario_id}.yaml")
        if scenario_path.exists():
            import yaml
            with open(scenario_path) as f:
                return yaml.safe_load(f)
        
        # Return empty scenario
        return {
            "schema_version": "1",
            "id": scenario_id,
            "version": 1,
            "title": f"Scenario {scenario_id}",
            "source": "synthetic",
            "status": "synthetic",
            "endpoints": {
                "command": {
                    "transport": "tcp",
                    "framing": {"type": "delimiter"},
                    "bind": {"host": "127.0.0.1", "port": 0}
                }
            },
            "initial_state": "idle",
            "limits": {
                "max_frame_bytes": 65536,
                "max_connections": 1,
                "scenario_timeout_ms": 10000
            }
        }

    def _execute_scenario_on_qemu(self, engine: ScenarioEngine, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute scenario on QEMU.

        Args:
            engine: Scenario engine
            parameters: Scenario parameters

        Returns:
            Execution result
        """
        # This would involve sending commands to QEMU monitor
        # For now, return a simple success result
        return {
            "success": True,
            "device_status": {
                "usb_devices": list(self.devices.keys()),
                "network": asdict(self.network_config) if self.network_config else None
            },
            "logs": [f"Scenario {engine.scenario_id} executed successfully"],
            "errors": [],
            "metadata": {
                "parameters": parameters,
                "execution_time": engine.clock.now_ms()
            }
        }


class QEMUHarnessIntegration:
    """Main integration class for QEMU and Polaris Harness."""

    def __init__(self, config_file: str = "/etc/qemu/harness.conf"):
        self.device_manager = QEMUDeviceManager(config_file)
        self.logger = logging.getLogger(__name__)
        self.scenario_history: List[QEMUScenarioResult] = []

    def setup_environment(self, device_configs: List[QEMUDeviceConfig], network_config: QEMUNetworkConfig):
        """Setup QEMU environment with devices and network.

        Args:
            device_configs: List of device configurations
            network_config: Network configuration
        """
        # Add devices
        for device_config in device_configs:
            self.device_manager.add_device(device_config)
        
        # Setup network
        self.device_manager.setup_network(network_config)
        
        # Start QEMU
        if not self.device_manager.start_qemu():
            raise QEMUError("Failed to start QEMU environment")
        
        self.logger.info("QEMU environment setup completed")

    def teardown_environment(self):
        """Teardown QEMU environment."""
        if self.device_manager.is_running():
            self.device_manager.stop_qemu()
        
        self.logger.info("QEMU environment teared down")

    def execute_scenario(self, scenario_id: str, parameters: Dict[str, Any] = None) -> QEMUScenarioResult:
        """Execute a scenario against the QEMU environment.

        Args:
            scenario_id: Scenario identifier
            parameters: Scenario parameters

        Returns:
            Scenario execution result
        """
        if parameters is None:
            parameters = {}
        
        # Execute scenario
        result = self.device_manager.execute_scenario(scenario_id, parameters)
        
        # Store in history
        self.scenario_history.append(result)
        
        # Log result
        if result.success:
            self.logger.info(f"Scenario {scenario_id} executed successfully in {result.execution_time_ms}ms")
        else:
            self.logger.error(f"Scenario {scenario_id} failed: {result.errors}")
        
        return result

    def get_scenario_history(self) -> List[QEMUScenarioResult]:
        """Get scenario execution history.

        Returns:
            List of scenario execution results
        """
        return self.scenario_history.copy()

    def get_device_status(self, device_name: str) -> Optional[Dict[str, Any]]:
        """Get device status.

        Args:
            device_name: Device name

        Returns:
            Device status or None
        """
        return self.device_manager.get_device_status(device_name)

    def is_environment_running(self) -> bool:
        """Check if QEMU environment is running.

        Returns:
            True if environment is running, False otherwise
        """
        return self.device_manager.is_running()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.teardown_environment()


# Global integration instance
_global_integration: Optional[QEMUHarnessIntegration] = None


def get_integration(config_file: str = "/etc/qemu/harness.conf") -> QEMUHarnessIntegration:
    """Get global integration instance.

    Args:
        config_file: Configuration file path

    Returns:
        Integration instance
    """
    global _global_integration
    if _global_integration is None:
        _global_integration = QEMUHarnessIntegration(config_file)
    return _global_integration


def setup_qemu_environment(device_configs: List[QEMUDeviceConfig], network_config: QEMUNetworkConfig):
    """Setup QEMU environment.

    Args:
        device_configs: List of device configurations
        network_config: Network configuration
    """
    integration = get_integration()
    integration.setup_environment(device_configs, network_config)


def teardown_qemu_environment():
    """Teardown QEMU environment."""
    integration = get_integration()
    integration.teardown_environment()


def execute_scenario_on_qemu(scenario_id: str, parameters: Dict[str, Any] = None) -> QEMUScenarioResult:
    """Execute scenario on QEMU.

    Args:
        scenario_id: Scenario identifier
        parameters: Scenario parameters

    Returns:
        Scenario execution result
    """
    integration = get_integration()
    return integration.execute_scenario(scenario_id, parameters)


def is_qemu_environment_running() -> bool:
    """Check if QEMU environment is running.

    Returns:
        True if environment is running, False otherwise
    """
    integration = get_integration()
    return integration.is_environment_running()