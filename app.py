from flask import Flask, render_template, request, send_file, jsonify
import zipfile
import io
import re
import json
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

# The special 'Right-to-Left Embedding' Unicode character
U202B = '\u202b'

def fix_ass_file(content):
    """Applies the specific RTL fix logic for .ASS file format."""
    processed_lines = []
    for line in content.splitlines():
        if line.strip().startswith('Dialogue:'):
            parts = line.split(',', 9)
            if len(parts) == 10:
                text_portion = parts[9]
                temp_text = text_portion.replace(U202B, '')
                temp_text = U202B + temp_text
                temp_text = temp_text.replace('\\N', '\\N' + U202B)
                temp_text = temp_text.replace('\\n', '\\n' + U202B)
                temp_text = temp_text.replace('}', '}' + U202B)
                temp_text = temp_text.replace(U202B + '{', '{')
                parts[9] = temp_text
                processed_lines.append(','.join(parts))
            else:
                processed_lines.append(line)
        else:
            processed_lines.append(line)
    return '\n'.join(processed_lines)

def fix_srt_file(content):
    """Applies the specific RTL fix logic for .SRT file format."""
    processed_lines = []
    # An SRT timestamp line looks like: 00:00:20,000 --> 00:00:24,400
    timestamp_pattern = re.compile(r'\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}')
    
    is_subtitle_text = False
    for line in content.splitlines():
        if timestamp_pattern.search(line):
            # The lines after a timestamp are the subtitle text
            is_subtitle_text = True
            processed_lines.append(line)
        elif line.strip() == '':
            # An empty line marks the end of a subtitle block
            is_subtitle_text = False
            processed_lines.append(line)
        elif is_subtitle_text:
            # This is a line of text that needs to be fixed
            temp_text = line.replace(U202B, '')
            processed_lines.append(U202B + temp_text)
        else:
            # This is a block number or other non-text line
            processed_lines.append(line)
            
    return '\n'.join(processed_lines)

def detect_encoding(content_bytes):
    """Detect and decode file content with proper encoding handling."""
    # Try UTF-8 with BOM first (common in Windows)
    try:
        return content_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        pass

    # Try UTF-16 (common in some subtitle files)
    try:
        decoded = content_bytes.decode('utf-16')
        # Check if it looks like proper text (not just null bytes)
        if len(decoded.strip()) > 0 and not all(ord(c) < 32 for c in decoded[:100]):
            return decoded
    except UnicodeDecodeError:
        pass

    # Try UTF-8 without BOM
    try:
        return content_bytes.decode('utf-8')
    except UnicodeDecodeError:
        pass

    # Try Latin-1 as fallback
    try:
        return content_bytes.decode('latin-1')
    except UnicodeDecodeError:
        pass

    raise UnicodeDecodeError("utf-8", content_bytes, 0, len(content_bytes), "Unable to decode file content")

def validate_srt_content(content):
    """Validate SRT content and check for common issues."""
    issues = []
    lines = content.splitlines()
    timestamp_pattern = re.compile(r'^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$')

    if not lines:
        issues.append("File is empty")
        return issues

    # Check for BOM or unusual characters at start
    if lines[0].startswith('\ufeff'):
        issues.append("File contains BOM marker")

    # Basic SRT structure validation
    subtitle_blocks = 0
    for i, line in enumerate(lines):
        if line.strip().isdigit():
            subtitle_blocks += 1
            # Check if next line is a timestamp
            if i + 1 < len(lines) and timestamp_pattern.match(lines[i + 1]):
                continue
            else:
                issues.append(f"Invalid SRT structure around line {i + 1}")

    if subtitle_blocks == 0:
        issues.append("No valid subtitle blocks found")

    return issues

def preview_srt_content(content, max_lines=10):
    """Extract preview content from SRT file for display."""
    lines = content.splitlines()
    preview_lines = []
    timestamp_pattern = re.compile(r'^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$')

    current_block = []
    for line in lines[:50]:  # Only check first 50 lines for preview
        if line.strip().isdigit() and len(current_block) == 0:
            current_block = [line]
        elif timestamp_pattern.match(line) and len(current_block) == 1:
            current_block.append(line)
        elif line.strip() == '' and len(current_block) == 2:
            current_block.append(line)
        elif len(current_block) == 3 and line.strip():
            # This is subtitle text
            text_line = line.strip()
            if text_line:
                # Check if text contains RTL characters
                has_rtl = any('\u0590' <= c <= '\u05FF' or '\u0600' <= c <= '\u06FF' or '\u0750' <= c <= '\u077F' for c in text_line)
                preview_lines.append({
                    'text': text_line[:100] + ('...' if len(text_line) > 100 else ''),
                    'has_rtl': has_rtl,
                    'is_rtl_fixed': U202B in text_line
                })
                if len(preview_lines) >= max_lines:
                    break
            current_block = []

    return preview_lines

@app.route("/preview", methods=["POST"])
def preview():
    """Preview subtitle file content and detect RTL issues."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    try:
        content_bytes = file.read()
        content = detect_encoding(content_bytes)

        filename_lower = file.filename.lower()
        preview_data = {
            'filename': file.filename,
            'size': len(content_bytes),
            'encoding': 'utf-8-sig' if content_bytes.startswith(b'\xef\xbb\xbf') else 'utf-8',
            'issues': []
        }

        if filename_lower.endswith('.srt'):
            issues = validate_srt_content(content)
            preview_data['issues'] = issues
            preview_data['preview'] = preview_srt_content(content)
            preview_data['type'] = 'srt'
        elif filename_lower.endswith('.ass'):
            preview_data['preview'] = content.splitlines()[:10]  # First 10 lines for ASS
            preview_data['type'] = 'ass'
        else:
            return jsonify({'error': 'Unsupported file type'}), 400

        return jsonify(preview_data)

    except Exception as e:
        return jsonify({'error': f'Error processing file: {str(e)}'}), 500

@app.route("/process", methods=["POST"])
def process():
    """Process subtitle files with improved encoding handling."""
    files = request.files.getlist("files")
    if not files:
        return jsonify({'error': 'No files uploaded'}), 400

    memory_file = io.BytesIO()
    processed_files = []

    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            try:
                content_bytes = f.read()
                content = detect_encoding(content_bytes)

                filename_lower = f.filename.lower()
                fixed_content = content

                # Check file type and apply the correct fix
                if filename_lower.endswith('.ass'):
                    fixed_content = fix_ass_file(content)
                elif filename_lower.endswith('.srt'):
                    fixed_content = fix_srt_file(content)

                # Ensure UTF-8 without BOM for better compatibility
                if filename_lower.endswith('.srt'):
                    # Validate SRT content before encoding
                    issues = validate_srt_content(fixed_content)
                    if issues:
                        processed_files.append({
                            'filename': f.filename,
                            'status': 'warning',
                            'issues': issues
                        })

                # Write without BOM for better Android compatibility
                zf.writestr(f.filename, fixed_content.encode('utf-8'))
                processed_files.append({
                    'filename': f.filename,
                    'status': 'success'
                })

            except Exception as e:
                processed_files.append({
                    'filename': f.filename,
                    'status': 'error',
                    'error': str(e)
                })

    memory_file.seek(0)

    # Return both the file and processing status
    response = send_file(
        memory_file,
        download_name="fixed_files.zip",
        as_attachment=True,
        mimetype='application/zip'
    )

    # Store processing results in session for status display
    # Note: In production, you'd want to use proper session storage
    return response

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        return process()
    return render_template("index.html")
