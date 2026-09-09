#!/bin/bash
# setup_qemu_harness.sh - Setup QEMU harness for Polaris Harness testing

set -e

# Configuration
HARNESS_NAME="polaris-harness-qemu"
HARNESS_VERSION="1.0.0"
HARNESS_HOME="/opt/polaris-harness"
QEMU_SOCKET="/tmp/qemu_harness.sock"
QEMU_LOG="/tmp/qemu_harness.log"
QEMU_PID="/tmp/qemu_harness.pid"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1" >&2
}

warn() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] WARN:${NC} $1"
}

# Check if running as root
if [[ $EUID -eq 0 ]]; then
    echo "Warning: Running as root. This is not recommended for security reasons."
fi

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check if a package is installed
package_installed() {
    dpkg -s "$1" >/dev/null 2>&1
}

# Function to install packages
install_packages() {
    local packages=("$@")
    local missing_packages=()
    
    for package in "${packages[@]}"; do
        if ! package_installed "$package"; then
            missing_packages+=("$package")
        fi
    done
    
    if [[ ${#missing_packages[@]} -gt 0 ]]; then
        echo "Installing packages: ${missing_packages[*]}"
        sudo apt-get update
        sudo apt-get install -y "${missing_packages[@]}"
    fi
}

# Function to setup network bridge
setup_network_bridge() {
    local bridge_name="harness-br"
    local ip_address="192.168.100.1"
    local netmask="255.255.255.0"
    
    log "Setting up network bridge: $bridge_name"
    
    # Check if bridge already exists
    if ! sudo brctl show | grep -q "$bridge_name"; then
        # Create bridge
        sudo brctl addbr "$bridge_name"
        sudo ifconfig "$bridge_name" "$ip_address" netmask "$netmask" up
        
        # Add default route
        sudo iptables -t nat -A POSTROUTING -o $(ip route | grep default | awk '{print $5}') -j MASQUERADE
        
        log "Network bridge $bridge_name setup completed"
    else
        warn "Network bridge $bridge_name already exists"
    fi
}

# Function to setup USB device
setup_usb_device() {
    local device_name="$1"
    local vendor_id="$2"
    local product_id="$3"
    local device_path="$4"
    
    log "Setting up USB device: $device_name (VID: $vendor_id, PID: $product_id)"
    
    # Check if device already exists
    if lsusb | grep -q "$vendor_id:$product_id"; then
        warn "USB device $vendor_id:$product_id already exists"
    else
        # Create virtual USB device
        sudo modprobe usbcore
        sudo modprobe usbdevice
        
        # This would typically involve creating a USB device configuration
        # For now, we'll just log the setup
        log "USB device $device_name setup completed (simulated)"
    fi
}

# Function to start QEMU
setup_qemu() {
    local harness_home="$HARNESS_HOME"
    local qemu_socket="$QEMU_SOCKET"
    local qemu_log="$QEMU_LOG"
    local qemu_pid="$QEMU_PID"
    
    log "Starting QEMU harness"
    
    # Create harness home directory
    sudo mkdir -p "$harness_home"
    sudo chown -R $USER:$USER "$harness_home"
    
    # Create socket directory
    sudo mkdir -p "$(dirname "$qemu_socket")"
    sudo chmod 755 "$(dirname "$qemu_socket")"
    
    # Build QEMU command
    local cmd=(
        "qemu-system-x86_64"
        "-name" "harness-device"
        "-m" "2048"
        "-smp" "2"
        "-enable-kvm"
        "-cpu" "host"
        "-net" "nic,model=e1000,macaddr=52:54:00:12:34:56"
        "-net" "socket,mcast=239.0.0.1,localaddr=192.168.100.1,listen=192.168.100.2"
        "-usbdevice" "usb-host"
        "-device" "usb-device,vendor=25fb,product=0189"
        "-monitor" "unix:$qemu_socket,server,nowait"
        "-daemonize"
        "-pidfile" "$qemu_pid"
        "-log" "file=$qemu_log,append"
        "-L" "$harness_home/qemu"
    )
    
    # Add harness binary
    if [[ -f "$harness_home/bin/polaris-harness" ]]; then
        cmd+=("-bios" "$harness_home/bin/polaris-harness")
    fi
    
    # Start QEMU
    log "Starting QEMU with command: ${cmd[*]}"
    sudo "${cmd[@]}"
    
    # Wait for QEMU to start
    sleep 3
    
    # Check if QEMU is running
    if [[ -f "$qemu_pid" ]]; then
        local qemu_pid=$(cat "$qemu_pid")
        if kill -0 "$qemu_pid" 2>/dev/null; then
            log "QEMU started successfully with PID: $qemu_pid"
            return 0
        else
            error "QEMU failed to start (process not running)"
            return 1
        fi
    else
        error "QEMU failed to start (PID file not created)"
        return 1
    fi
}

# Function to stop QEMU
stop_qemu() {
    local qemu_pid="$QEMU_PID"
    
    log "Stopping QEMU harness"
    
    if [[ -f "$qemu_pid" ]]; then
        local pid=$(cat "$qemu_pid")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            sleep 2
            
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid"
                log "QEMU forcefully stopped"
            else
                log "QEMU stopped gracefully"
            fi
        else
            warn "QEMU was not running"
        fi
        
        rm -f "$qemu_pid"
    else
        warn "QEMU PID file not found"
    fi
    
    # Clean up socket
    sudo rm -f "$QEMU_SOCKET"
    
    log "QEMU harness stopped"
}

# Function to setup harness configuration
setup_harness_config() {
    local harness_home="$HARNESS_HOME"
    
    log "Setting up harness configuration"
    
    # Create harness home directory
    sudo mkdir -p "$harness_home"
    sudo chown -R $USER:$USER "$harness_home"
    
    # Create configuration directory
    sudo mkdir -p "$harness_home/config"
    
    # Create harness configuration
    cat > "$harness_home/config/harness.conf" << EOF
{
  "harness_name": "$HARNESS_NAME",
  "harness_version": "$HARNESS_VERSION",
  "harness_home": "$harness_home",
  "qemu_socket": "$QEMU_SOCKET",
  "qemu_log": "$QEMU_LOG",
  "qemu_pid": "$QEMU_PID",
  "devices": [
    {
      "device_type": "pentax-k3-iii",
      "vendor_id": "25fb",
      "product_id": "0189",
      "device_name": "Pentax K-3 III",
      "usb_version": "2.0",
      "driver": "usb-gadget",
      "parameters": {
        "mode": "MTP",
        "interface": "usb-mtp"
      }
    }
  ],
  "network": {
    "bridge_name": "harness-br",
    "ip_address": "192.168.100.1",
    "netmask": "255.255.255.0",
    "gateway": "192.168.100.1",
    "dns_servers": ["8.8.8.8", "8.8.4.4"]
  }
}
EOF
    
    # Create QEMU configuration
    sudo mkdir -p "$harness_home/qemu"
    cat > "$harness_home/qemu/qemu.conf" << EOF
# QEMU configuration for Polaris Harness

# System configuration
-m 2048
-smp 2
-enable-kvm
-cpu host

# Network configuration
-net nic,model=e1000,macaddr=52:54:00:12:34:56
-net socket,mcast=239.0.0.1,localaddr=192.168.100.1,listen=192.168.100.2

# USB configuration
-usbdevice usb-host
-device usb-device,vendor=25fb,product=0189

# Monitor configuration
-monitor unix:$QEMU_SOCKET,server,nowait

# Logging configuration
-log file=$QEMU_LOG,append

# Harness binary
-bios $harness_home/bin/polaris-harness
EOF
    
    log "Harness configuration setup completed"
}

# Function to setup harness binary
setup_harness_binary() {
    local harness_home="$HARNESS_HOME"
    
    log "Setting up harness binary"
    
    # Create bin directory
    sudo mkdir -p "$harness_home/bin"
    
    # Copy harness binary
    if [[ -f "$(pwd)/src/polaris_harness/cli.py" ]]; then
        sudo cp "$(pwd)/src/polaris_harness/cli.py" "$harness_home/bin/polaris-harness"
        sudo chmod +x "$harness_home/bin/polaris-harness"
        
        # Create wrapper script
        cat > "$harness_home/bin/polaris-harness-wrapper.sh" << EOF
#!/bin/bash
# Wrapper script for Polaris Harness in QEMU

HARNESS_HOME="$harness_home"
QEMU_SOCKET="$QEMU_SOCKET"

# Check if QEMU is running
if [[ -f "$QEMU_SOCKET" ]]; then
    echo "QEMU is running. Harness can connect via socket."
    echo "Use 'polaris-harness' command to interact with the harness."
else
    echo "QEMU is not running. Starting QEMU..."
    sudo "$HARNESS_HOME/scripts/setup_qemu_harness.sh" start
fi
EOF
        
        sudo chmod +x "$harness_home/bin/polaris-harness-wrapper.sh"
        
        log "Harness binary setup completed"
    else
        error "Harness binary not found"
        return 1
    fi
}

# Function to setup harness scripts
setup_harness_scripts() {
    local harness_home="$HARNESS_HOME"
    
    log "Setting up harness scripts"
    
    # Create scripts directory
    sudo mkdir -p "$harness_home/scripts"
    
    # Copy setup script
    sudo cp "$(pwd)/scripts/setup_qemu_harness.sh" "$harness_home/scripts/"
    sudo chmod +x "$harness_home/scripts/setup_qemu_harness.sh"
    
    # Create stop script
    cat > "$harness_home/scripts/stop_qemu_harness.sh" << EOF
#!/bin/bash
# Stop QEMU harness

QEMU_PID="/tmp/qemu_harness.pid"
QEMU_SOCKET="/tmp/qemu_harness.sock"

if [[ -f "$QEMU_PID" ]]; then
    local pid=$(cat "$QEMU_PID")
    if kill -0 "$pid" 2>/dev/null; then
        echo "Stopping QEMU harness (PID: $pid)..."
        kill "$pid"
        sleep 2
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid"
            echo "QEMU harness forcefully stopped"
        else
            echo "QEMU harness stopped gracefully"
        fi
    else
        echo "QEMU harness was not running"
    fi
    rm -f "$QEMU_PID"
else
    echo "QEMU harness PID file not found"
fi

# Clean up socket
sudo rm -f "$QEMU_SOCKET"

echo "QEMU harness stopped"
EOF
    
    sudo chmod +x "$harness_home/scripts/stop_qemu_harness.sh"
    
    log "Harness scripts setup completed"
}

# Function to setup harness documentation
setup_harness_documentation() {
    local harness_home="$HARNESS_HOME"
    
    log "Setting up harness documentation"
    
    # Create documentation directory
    sudo mkdir -p "$harness_home/docs"
    
    # Copy documentation
    if [[ -f "$(pwd)/docs/USER-GUIDE.md" ]]; then
        sudo cp "$(pwd)/docs/USER-GUIDE.md" "$harness_home/docs/"
    fi
    
    if [[ -f "$(pwd)/docs/API-REFERENCE.md" ]]; then
        sudo cp "$(pwd)/docs/API-REFERENCE.md" "$harness_home/docs/"
    fi
    
    if [[ -f "$(pwd)/docs/SCENARIO-AUTHORING.md" ]]; then
        sudo cp "$(pwd)/docs/SCENARIO-AUTHORING.md" "$harness_home/docs/"
    fi
    
    log "Harness documentation setup completed"
}

# Function to setup harness tests
setup_harness_tests() {
    local harness_home="$HARNESS_HOME"
    
    log "Setting up harness tests"
    
    # Create tests directory
    sudo mkdir -p "$harness_home/tests"
    
    # Copy tests
    if [[ -f "$(pwd)/tests/test_basic_functionality.py" ]]; then
        sudo cp "$(pwd)/tests/test_basic_functionality.py" "$harness_home/tests/"
    fi
    
    if [[ -f "$(pwd)/tests/test_integration.py" ]]; then
        sudo cp "$(pwd)/tests/test_integration.py" "$harness_home/tests/"
    fi
    
    if [[ -f "$(pwd)/tests/test_smoke.py" ]]; then
        sudo cp "$(pwd)/tests/test_smoke.py" "$harness_home/tests/"
    fi
    
    if [[ -f "$(pwd)/tests/test_evidence_validation.py" ]]; then
        sudo cp "$(pwd)/tests/test_evidence_validation.py" "$harness_home/tests/"
    fi
    
    log "Harness tests setup completed"
}

# Function to setup harness dependencies
setup_harness_dependencies() {
    log "Setting up harness dependencies"
    
    # Install required packages
    install_packages "qemu-system-x86-64" "bridge-utils" "iptables" "usbutils"
    
    # Install Python dependencies
    if [[ -f "$(pwd)/pyproject.toml" ]]; then
        pip install -e .
    fi
    
    log "Harness dependencies setup completed"
}

# Function to setup harness service
setup_harness_service() {
    local harness_home="$HARNESS_HOME"
    
    log "Setting up harness service"
    
    # Create systemd service file
    cat > "/etc/systemd/system/polaris-harness-qemu.service" << EOF
[Unit]
Description=Polaris Harness QEMU Service
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$harness_home
ExecStart=$harness_home/scripts/setup_qemu_harness.sh start
ExecStop=$harness_home/scripts/stop_qemu_harness.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    
    # Reload systemd
    sudo systemctl daemon-reload
    
    # Enable service
    sudo systemctl enable polaris-harness-qemu.service
    
    log "Harness service setup completed"
}

# Function to start harness service
start_harness_service() {
    log "Starting Polaris Harness QEMU service"
    
    sudo systemctl start polaris-harness-qemu.service
    
    # Wait for service to start
    sleep 5
    
    if sudo systemctl is-active --quiet polaris-harness-qemu.service; then
        log "Polaris Harness QEMU service started successfully"
        return 0
    else
        error "Failed to start Polaris Harness QEMU service"
        return 1
    fi
}

# Function to stop harness service
stop_harness_service() {
    log "Stopping Polaris Harness QEMU service"
    
    sudo systemctl stop polaris-harness-qemu.service
    
    if sudo systemctl is-active --quiet polaris-harness-qemu.service; then
        error "Failed to stop Polaris Harness QEMU service"
        return 1
    else
        log "Polaris Harness QEMU service stopped successfully"
        return 0
    fi
}

# Function to show harness status
show_harness_status() {
    log "Polaris Harness QEMU Service Status"
    
    if sudo systemctl is-active --quiet polaris-harness-qemu.service; then
        echo "Status: RUNNING"
        echo "PID: $(sudo systemctl show --property=MainPID --value polaris-harness-qemu.service)"
        echo "Uptime: $(ps -o etimes= -p $(sudo systemctl show --property=MainPID --value polaris-harness-qemu.service) 2>/dev/null | awk '{print $1}') seconds"
    else
        echo "Status: STOPPED"
    fi
}

# Main script logic
case "$1" in
    "setup")
        log "Setting up Polaris Harness QEMU environment"
        
        # Install dependencies
        setup_harness_dependencies
        
        # Setup network
        setup_network_bridge
        
        # Setup harness configuration
        setup_harness_config
        
        # Setup harness binary
        setup_harness_binary
        
        # Setup harness scripts
        setup_harness_scripts
        
        # Setup harness documentation
        setup_harness_documentation
        
        # Setup harness tests
        setup_harness_tests
        
        # Setup harness service
        setup_harness_service
        
        log "Polaris Harness QEMU environment setup completed successfully"
        ;;
    
    "start")
        log "Starting Polaris Harness QEMU environment"
        
        # Start harness service
        start_harness_service
        
        if [[ $? -eq 0 ]]; then
            log "Polaris Harness QEMU environment started successfully"
        else
            error "Failed to start Polaris Harness QEMU environment"
            exit 1
        fi
        ;;
    
    "stop")
        log "Stopping Polaris Harness QEMU environment"
        
        # Stop harness service
        stop_harness_service
        
        if [[ $? -eq 0 ]]; then
            log "Polaris Harness QEMU environment stopped successfully"
        else
            error "Failed to stop Polaris Harness QEMU environment"
            exit 1
        fi
        ;;
    
    "status")
        show_harness_status
        ;;
    
    "restart")
        log "Restarting Polaris Harness QEMU environment"
        
        # Stop harness service
        stop_harness_service
        
        # Start harness service
        start_harness_service
        
        if [[ $? -eq 0 ]]; then
            log "Polaris Harness QEMU environment restarted successfully"
        else
            error "Failed to restart Polaris Harness QEMU environment"
            exit 1
        fi
        ;;
    
    "help")
        echo "Usage: $0 {setup|start|stop|status|restart|help}"
        echo ""
        echo "Commands:"
        echo "  setup    - Setup Polaris Harness QEMU environment"
        echo "  start    - Start Polaris Harness QEMU environment"
        echo "  stop     - Stop Polaris Harness QEMU environment"
        echo "  status   - Show Polaris Harness QEMU service status"
        echo "  restart  - Restart Polaris Harness QEMU environment"
        echo "  help     - Show this help message"
        ;;
    
    *)
        echo "Usage: $0 {setup|start|stop|status|restart|help}"
        echo "Error: Invalid command: $1"
        exit 1
        ;;
esac

# If no arguments provided, show help
if [[ $# -eq 0 ]]; then
    echo "Usage: $0 {setup|start|stop|status|restart|help}"
    echo "Error: No command provided"
    exit 1
fi