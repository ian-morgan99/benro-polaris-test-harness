"""
Web interface for Benro Polaris Test Harness.

Provides web-based simulation of user interactions and evidence upload capabilities.
"""

import os
import uuid
import hashlib
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from flask import Flask, request, jsonify, render_template_string
from werkzeug.utils import secure_filename

from .validation import (
    validate_scenario,
    validate_evidence,
    validate_scenario_file as validate_scenario_path,
    validate_evidence_file,
)
from .evidence import validate_and_store_evidence, get_evidence, get_all_evidence

# Configuration
UPLOAD_FOLDER = Path("uploads")
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'tiff'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Create upload directory
UPLOAD_FOLDER.mkdir(exist_ok=True)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE
next_capture_images: Dict[str, Path] = {}


def allowed_file(filename: str) -> bool:
    """Check if file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def generate_file_hash(file_path: Path) -> str:
    """Generate SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def create_fake_jpg(output_path: Path, width: int = 640, height: int = 480) -> str:
    """Create a fake JPG file for testing."""
    try:
        import cv2
        import numpy as np
        
        # Create a simple test image (gradient background with text)
        image = np.zeros((height, width, 3), dtype=np.uint8)
        
        # Add gradient background
        for i in range(height):
            for j in range(width):
                image[i, j] = [i % 256, j % 256, (i + j) % 256]
        
        # Add some text (simulating camera metadata)
        font = cv2.FONT_HERSHEY_SIMPLEX
        text = f"Test JPG - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        cv2.putText(image, text, (10, 30), font, 1, (255, 255, 255), 2)
        
        # Save the image
        cv2.imwrite(str(output_path), image)
        return str(output_path)
        
    except ImportError:
        # Fallback: create a simple text file if OpenCV is not available
        with open(output_path, 'w') as f:
            f.write(f"FAKE JPG FILE\n")
            f.write(f"Generated at: {datetime.now()}\n")
            f.write(f"Dimensions: {width}x{height}\n")
            f.write(f"This is a simulated JPG file for testing purposes.\n")
        return str(output_path)


@app.route('/')
def index():
    """Main page with harness interface."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Benro Polaris Test Harness - Web Interface</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .container { max-width: 1200px; margin: 0 auto; }
            .section { margin: 20px 0; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }
            .section h2 { color: #2c3e50; }
            .form-group { margin: 15px 0; }
            label { font-weight: bold; }
            input[type="text"], input[type="file"], textarea, select { width: 100%; padding: 8px; margin: 5px 0; }
            button { background-color: #3498db; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; margin: 5px; }
            button:hover { background-color: #2980b9; }
            .result { margin: 10px 0; padding: 10px; border-radius: 5px; }
            .success { background-color: #d4edda; color: #155724; }
            .error { background-color: #f8d7da; color: #721c24; }
            .scenario-preview { background-color: #f8f9fa; padding: 10px; border-radius: 3px; font-family: monospace; }
            .hardware-config { display: flex; gap: 20px; margin: 15px 0; }
            .hardware-option { flex: 1; padding: 15px; border: 2px solid #ddd; border-radius: 5px; cursor: pointer; }
            .hardware-option.selected { border-color: #3498db; background-color: #e3f2fd; }
            .hardware-option:hover { border-color: #2980b9; }
            .upload-section { border: 2px dashed #ddd; padding: 20px; text-align: center; margin: 15px 0; }
            .upload-section:hover { border-color: #3498db; background-color: #f8f9fa; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Benro Polaris Test Harness - Web Interface</h1>
            
            <div class="section">
                <h2>Hardware Configuration</h2>
                <p>Configure hardware components for simulation.</p>
                
                <div class="hardware-config">
                    <div class="hardware-option" onclick="selectHardware('astro', this)">
                        <h3>ASTRO Module</h3>
                        <p>Include or exclude the ASTRO module in the simulation</p>
                    </div>
                    <div class="hardware-option" onclick="selectHardware('power', this)">
                        <h3>Power Supply</h3>
                        <p>Include or exclude power supply in the simulation</p>
                    </div>
                </div>
                
                <div class="form-group">
                    <label for="camera_id">Camera ID:</label>
                    <input type="text" id="camera_id" value="K-3-III-25fb-0189">
                </div>
                
                <div class="form-group">
                    <label for="scenario_template">Scenario Template:</label>
                    <select id="scenario_template">
                        <option value="power-button">Power Button Test</option>
                        <option value="capture-test">Capture Test</option>
                        <option value="preview-test">Preview Test</option>
                    </select>
                </div>
                
                <button onclick="simulatePowerButton()">Simulate Power Button</button>
                <button onclick="generateFakeJPG()">Generate Fake JPG</button>
            </div>
            
            <div class="section">
                <h2>Platesolving Image Upload</h2>
                <p>Upload real images for platesolving testing (Next capture image simulation).</p>
                
                <div class="upload-section" id="platesolve-upload" onclick="document.getElementById('platesolve-file').click()">
                    <h3>📸 Upload Platesolving Image</h3>
                    <p>Drag and drop or click to upload an image for platesolving testing</p>
                    <input type="file" id="platesolve-file" accept=".jpg,.jpeg,.png,.gif,.bmp,.tiff" style="display: none;" onchange="handlePlatesolveUpload(this)">
                </div>
                
                <div class="form-group">
                    <label for="platesolve-camera">Camera ID for Platesolving:</label>
                    <input type="text" id="platesolve-camera" value="K-3-III-25fb-0189" placeholder="Enter camera ID">
                </div>
                
                <div class="form-group">
                    <label for="platesolve-description">Image Description:</label>
                    <textarea id="platesolve-description" placeholder="Describe the captured image...">Platesolving test image for next capture simulation</textarea>
                </div>
                
                <button onclick="uploadPlatesolveImage()">Upload Platesolving Image</button>
            </div>
            
            <div class="section">
                <h2>Evidence Upload</h2>
                <p>Upload evidence files (JPG, PNG, etc.) for validation.</p>
                
                <div class="upload-section">
                    <h3>📁 Upload Evidence File</h3>
                    <p>Drag and drop or click to upload evidence files</p>
                    <input type="file" id="evidence_file" accept=".jpg,.jpeg,.png,.gif,.bmp,.tiff" style="display: none;" onchange="handleEvidenceUpload(this)">
                </div>
                
                <div class="form-group">
                    <label for="evidence_type">Evidence Type:</label>
                    <select id="evidence_type">
                        <option value="physical">Physical Evidence</option>
                        <option value="synthetic">Synthetic Evidence</option>
                        <option value="derived">Derived Evidence</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <label for="camera_info">Camera Information:</label>
                    <textarea id="camera_info" placeholder="Enter camera details...">Pentax K-3 III, firmware 4.0.0.32, USB MTP, ID 25fb:0189</textarea>
                </div>
                
                <button onclick="uploadEvidence()">Upload Evidence</button>
            </div>
            
            <div class="section">
                <h2>Scenario Validation</h2>
                <p>Validate scenario files (JSON/YAML) uploaded by users.</p>
                
                <div class="form-group">
                    <label for="scenario_file">Upload Scenario File:</label>
                    <input type="file" id="scenario_file" accept=".json,.yaml,.yml">
                </div>
                
                <button onclick="validateScenario()">Validate Scenario</button>
            </div>
            
            <div class="section">
                <h2>Results</h2>
                <div id="results"></div>
            </div>
        </div>
        
        <script>
            let selectedHardware = {
                astro: false,
                power: false
            };
            let platesolveImage = null;
            
            function selectHardware(type, element) {
                selectedHardware[type] = !selectedHardware[type];
                element.classList.toggle('selected', selectedHardware[type]);
            }
            
            function handlePlatesolveUpload(input) {
                if (input.files && input.files[0]) {
                    const file = input.files[0];
                    const reader = new FileReader();
                    
                    reader.onload = function(e) {
                        platesolveImage = e.target.result;
                        document.getElementById('platesolve-upload').innerHTML = `
                            <h3>📸 Platesolving Image Uploaded</h3>
                            <p><strong>File:</strong> ${file.name}</p>
                            <p><strong>Size:</strong> ${(file.size / 1024).toFixed(2)} KB</p>
                            <p><strong>Type:</strong> ${file.type}</p>
                            <button onclick="removePlatesolveImage()" style="background-color: #e74c3c;">Remove Image</button>
                        `;
                    };
                    
                    reader.readAsDataURL(file);
                }
            }
            
            function removePlatesolveImage() {
                platesolveImage = null;
                document.getElementById('platesolve-upload').innerHTML = `
                    <h3>📸 Upload Platesolving Image</h3>
                    <p>Drag and drop or click to upload an image for platesolving testing</p>
                    <input type="file" id="platesolve-file" accept=".jpg,.jpeg,.png,.gif,.bmp,.tiff" style="display: none;" onchange="handlePlatesolveUpload(this)">
                `;
            }
            
            function handleEvidenceUpload(input) {
                if (input.files && input.files[0]) {
                    const file = input.files[0];
                    const reader = new FileReader();
                    
                    reader.onload = function(e) {
                        document.getElementById('evidence_file').files = [file];
                        document.getElementById('evidence_file').style.display = 'none';
                        document.querySelector('.upload-section:nth-child(3)').innerHTML = `
                            <h3>📁 Evidence File Uploaded</h3>
                            <p><strong>File:</strong> ${file.name}</p>
                            <p><strong>Size:</strong> ${(file.size / 1024).toFixed(2)} KB</p>
                            <p><strong>Type:</strong> ${file.type}</p>
                            <button onclick="removeEvidenceImage()" style="background-color: #e74c3c;">Remove File</button>
                        `;
                    };
                    
                    reader.readAsDataURL(file);
                }
            }
            
            function removeEvidenceImage() {
                document.getElementById('evidence_file').style.display = 'block';
                document.querySelector('.upload-section:nth-child(3)').innerHTML = `
                    <h3>📁 Upload Evidence File</h3>
                    <p>Drag and drop or click to upload evidence files</p>
                    <input type="file" id="evidence_file" accept=".jpg,.jpeg,.png,.gif,.bmp,.tiff" style="display: none;" onchange="handleEvidenceUpload(this)">
                `;
            }
            
            function showResult(message, isSuccess) {
                const resultsDiv = document.getElementById('results');
                const resultDiv = document.createElement('div');
                resultDiv.className = `result ${isSuccess ? 'success' : 'error'}`;
                resultDiv.textContent = message;
                resultsDiv.insertBefore(resultDiv, resultsDiv.firstChild);
            }
            
            function simulatePowerButton() {
                const cameraId = document.getElementById('camera_id').value;
                const template = document.getElementById('scenario_template').value;
                
                fetch('/api/simulate-power-button', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        camera_id: cameraId,
                        template: template,
                        hardware_config: selectedHardware
                    })
                })
                .then(response => response.json())
                .then(data => {
                    showResult(`Power button simulation completed: ${data.message}`, true);
                    if (data.scenario) {
                        showResult(`Generated scenario: ${JSON.stringify(data.scenario, null, 2)}`, true);
                    }
                })
                .catch(error => {
                    showResult(`Error: ${error.message}`, false);
                });
            }
            
            function generateFakeJPG() {
                fetch('/api/generate-fake-jpg', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({})
                })
                .then(response => response.json())
                .then(data => {
                    showResult(`Fake JPG generated: ${data.file_path}`, true);
                })
                .catch(error => {
                    showResult(`Error: ${error.message}`, false);
                });
            }
            
            function uploadPlatesolveImage() {
                if (!platesolveImage) {
                    showResult('Please upload a platesolving image first', false);
                    return;
                }
                
                const cameraId = document.getElementById('platesolve-camera').value;
                const description = document.getElementById('platesolve-description').value;
                
                fetch('/api/upload-platesolve-image', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        camera_id: cameraId,
                        description: description,
                        image_data: platesolveImage
                    })
                })
                .then(response => response.json())
                .then(data => {
                    showResult(`Platesolving image uploaded: ${data.evidence_id}`, true);
                    // Reset the upload section
                    platesolveImage = null;
                    document.getElementById('platesolve-upload').innerHTML = `
                        <h3>📸 Upload Platesolving Image</h3>
                        <p>Drag and drop or click to upload an image for platesolving testing</p>
                        <input type="file" id="platesolve-file" accept=".jpg,.jpeg,.png,.gif,.bmp,.tiff" style="display: none;" onchange="handlePlatesolveUpload(this)">
                    `;
                })
                .catch(error => {
                    showResult(`Error: ${error.message}`, false);
                });
            }
            
            function uploadEvidence() {
                const evidenceType = document.getElementById('evidence_type').value;
                const cameraInfo = document.getElementById('camera_info').value;
                
                const formData = new FormData();
                formData.append('evidence_type', evidenceType);
                formData.append('camera_info', cameraInfo);
                
                const fileInput = document.getElementById('evidence_file');
                if (fileInput.files.length > 0) {
                    formData.append('file', fileInput.files[0]);
                }
                
                fetch('/api/upload-evidence', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    showResult(`Evidence uploaded: ${data.evidence_id}`, true);
                })
                .catch(error => {
                    showResult(`Error: ${error.message}`, false);
                });
            }
            
            function validateScenario() {
                const fileInput = document.getElementById('scenario_file');
                
                if (fileInput.files.length === 0) {
                    showResult('Please select a scenario file', false);
                    return;
                }
                
                const formData = new FormData();
                formData.append('file', fileInput.files[0]);
                
                fetch('/api/validate-scenario', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.errors && data.errors.length > 0) {
                        showResult(`Validation errors: ${data.errors.join(', ')}`, false);
                    } else {
                        showResult('Scenario is valid!', true);
                    }
                })
                .catch(error => {
                    showResult(`Error: ${error.message}`, false);
                });
            }
            
            // Initialize hardware selection
            document.addEventListener('DOMContentLoaded', function() {
                // Set initial hardware selection
                selectedHardware.astro = true; // Default: include ASTRO
                selectedHardware.power = true; // Default: include power
                
                // Update visual selection
                const astroOption = document.querySelector('.hardware-option:nth-child(1)');
                const powerOption = document.querySelector('.hardware-option:nth-child(2)');
                astroOption.classList.add('selected');
                powerOption.classList.add('selected');
            });
        </script>
    </body>
    </html>
    """
    return html


@app.route('/api/simulate-power-button', methods=['POST'])
def simulate_power_button():
    """Simulate power button press and generate scenario based on hardware configuration."""
    try:
        data = request.get_json()
        camera_id = data.get('camera_id', 'K-3-III-25fb-0189')
        template = data.get('template', 'power-button')
        hardware_config = data.get('hardware_config', {'astro': True, 'power': True})
        
        # Create a scenario based on the template and hardware configuration
        scenario = {
            "schema_version": "1",
            "id": f"power-button-test-{uuid.uuid4().hex[:8]}",
            "version": 1,
            "title": f"Power Button Test - {camera_id} (ASTRO: {hardware_config.get('astro', True)}, Power: {hardware_config.get('power', True)})",
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
            },
            "states": {
                "idle": {
                    "on_request": [
                        {
                            "id": "power_button_press",
                            "match": {
                                "type": "exact_text",
                                "value": "POWER_BUTTON",
                                "encoding": "utf-8"
                            },
                            "actions": [
                                {
                                    "type": "send",
                                    "endpoint": "command",
                                    "after_ms": 0,
                                    "text": f"POWER_BUTTON_PRESSED (ASTRO: {hardware_config.get('astro', True)}, Power: {hardware_config.get('power', True)})"
                                },
                                {
                                    "type": "transition",
                                    "next_state": "powered_on"
                                }
                            ]
                        }
                    ]
                },
                "powered_on": {
                    "on_request": [
                        {
                            "id": "generate_jpg",
                            "match": {
                                "type": "exact_text",
                                "value": "GENERATE_JPG",
                                "encoding": "utf-8"
                            },
                            "actions": [
                                {
                                    "type": "send",
                                    "endpoint": "command",
                                    "after_ms": 0,
                                    "text": f"JPG_GENERATED (Hardware: ASTRO={hardware_config.get('astro', True)}, Power={hardware_config.get('power', True)})"
                                },
                                {
                                    "type": "transition",
                                    "next_state": "jpg_generated"
                                }
                            ]
                        }
                    ]
                },
                "jpg_generated": {
                    "emit_marker": "jpg_generated"
                }
            }
        }
        
        # Validate the scenario
        errors = validate_scenario(scenario)
        
        return jsonify({
            "success": True,
            "message": "Power button simulation completed successfully",
            "scenario": scenario if not errors else None,
            "errors": errors,
            "hardware_config": hardware_config
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error simulating power button: {str(e)}"
        }), 500


@app.route('/api/generate-fake-jpg', methods=['POST'])
def generate_fake_jpg():
    """Generate a fake JPG file for testing."""
    try:
        camera_id = (request.get_json(silent=True) or {}).get("camera_id")
        queued_image = next_capture_images.pop(camera_id, None) if camera_id else None
        if queued_image:
            return jsonify({
                "success": True,
                "message": "Queued platesolving image returned for simulated capture",
                "file_path": str(queued_image),
                "file_hash": generate_file_hash(queued_image),
                "filename": queued_image.name,
            })

        # Generate a unique filename
        filename = f"fake_jpg_{uuid.uuid4().hex[:8]}.jpg"
        file_path = Path(app.config['UPLOAD_FOLDER']) / filename
        
        # Create the fake JPG
        create_fake_jpg(file_path)
        
        # Generate hash
        file_hash = generate_file_hash(file_path)
        
        return jsonify({
            "success": True,
            "message": "Fake JPG generated successfully",
            "file_path": str(file_path),
            "file_hash": file_hash,
            "filename": filename
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error generating fake JPG: {str(e)}"
        }), 500


@app.route('/api/upload-evidence', methods=['POST'])
def upload_evidence():
    """Upload evidence file and create evidence record."""
    try:
        if 'file' not in request.files:
            return jsonify({
                "success": False,
                "message": "No file uploaded"
            }), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({
                "success": False,
                "message": "No file selected"
            }), 400
        
        if not allowed_file(file.filename):
            return jsonify({
                "success": False,
                "message": "File type not allowed. Allowed types: JPG, PNG, GIF, BMP, TIFF"
            }), 400
        
        # Save the file
        filename = secure_filename(file.filename)
        file_path = Path(app.config['UPLOAD_FOLDER']) / filename
        file.save(str(file_path))
        
        # Generate evidence ID
        evidence_id = f"evidence_{uuid.uuid4().hex[:8]}"
        
        # Get camera info from request
        camera_info = request.form.get('camera_info', 'Test camera')
        
        # Create evidence record
        evidence = {
            "schema_version": "1",
            "evidence_id": evidence_id,
            "source": request.form.get('evidence_type', 'physical'),
            "status": "confirmed",
            "observed_date": datetime.now().strftime('%Y-%m-%d'),
            "observed_layer": "polaris-runtime",
            "source_issue": f"web-upload-{evidence_id}",
            "camera": {
                "manufacturer": "Test",
                "model": "Test Camera",
                "firmware": "1.0.0",
                "usb_mode": "MTP",
                "usb_id": "0000:0000"
            },
            "polaris": {
                "firmware": "1.0.0",
                "patcher_sha": "unknown",
                "fwpkt_sha256": "unknown"
            },
            "libgphoto2": {
                "sha": "unknown"
            },
            "artifacts": [
                {
                    "path": str(file_path),
                    "sha256": generate_file_hash(file_path),
                    "source_provenance": "web-upload",
                    "target_abi": "arm64",
                    "expected_path": str(file_path)
                }
            ]
        }
        
        # Validate and store evidence
        errors = validate_and_store_evidence(evidence)
        
        if errors:
            return jsonify({
                "success": False,
                "message": f"Evidence validation failed: {errors}"
            }), 400
        
        return jsonify({
            "success": True,
            "message": "Evidence uploaded successfully",
            "evidence_id": evidence_id,
            "file_path": str(file_path)
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error uploading evidence: {str(e)}"
        }), 500


@app.route('/api/upload-platesolve-image', methods=['POST'])
def upload_platesolve_image():
    """Store a selected image for the next simulated capture."""
    try:
        data = request.get_json() or {}
        image_data = data.get("image_data", "")
        if not image_data.startswith("data:image/") or "," not in image_data:
            return jsonify({"success": False, "message": "No image data provided"}), 400

        import base64

        filename = f"next-capture-{uuid.uuid4().hex[:8]}.jpg"
        file_path = Path(app.config["UPLOAD_FOLDER"]) / filename
        encoded_image = image_data.split(",", 1)[1]
        file_path.write_bytes(base64.b64decode(encoded_image, validate=True))
        camera_id = data.get("camera_id", "")
        if not camera_id:
            file_path.unlink()
            return jsonify({"success": False, "message": "Camera ID is required"}), 400
        next_capture_images[camera_id] = file_path

        return jsonify({
            "success": True,
            "message": "Image queued for the next simulated capture",
            "camera_id": camera_id,
            "description": data.get("description", ""),
            "file_path": str(file_path),
            "file_hash": generate_file_hash(file_path),
        })
    except (ValueError, base64.binascii.Error) as error:
        return jsonify({"success": False, "message": f"Invalid image data: {error}"}), 400
    except Exception as error:
        return jsonify({"success": False, "message": f"Error uploading platesolving image: {error}"}), 500


@app.route('/api/validate-scenario', methods=['POST'])
def validate_scenario_upload():
    """Validate scenario file."""
    try:
        if 'file' not in request.files:
            return jsonify({
                "success": False,
                "message": "No file uploaded"
            }), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({
                "success": False,
                "message": "No file selected"
            }), 400
        
        # Save the file temporarily
        filename = secure_filename(file.filename)
        file_path = Path(app.config['UPLOAD_FOLDER']) / filename
        file.save(str(file_path))
        
        # Validate the file
        errors = validate_scenario_path(file_path)
        
        return jsonify({
            "success": True,
            "message": "Scenario validation completed",
            "errors": errors,
            "filename": filename
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Error validating scenario: {str(e)}"
        }), 500


if __name__ == '__main__':
    print("Starting Benro Polaris Test Harness Web Interface...")
    print("Visit http://localhost:5000 in your browser")
    app.run(host='127.0.0.1', port=5000, debug=False)