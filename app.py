from flask import Flask, render_template, request, send_file
import zipfile
import io
import re

app = Flask(__name__)

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

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        files = request.files.getlist("files")
        if not files:
            return "No files uploaded", 400

        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            for f in files:
                content = None
                try:
                    content = f.read().decode('utf-8-sig')
                except UnicodeDecodeError:
                    try:
                        f.seek(0)
                        content = f.read().decode('utf-16')
                    except Exception:
                        content = f"Error: Could not decode file '{f.filename}'."

                if content:
                    filename_lower = f.filename.lower()
                    fixed_content = content # Default to original content

                    # Check file type and apply the correct fix
                    if filename_lower.endswith('.ass'):
                        fixed_content = fix_ass_file(content)
                    elif filename_lower.endswith('.srt'):
                        fixed_content = fix_srt_file(content)
                    
                    zf.writestr(f.filename, fixed_content.encode('utf-8'))

        memory_file.seek(0)
        return send_file(
            memory_file,
            download_name="fixed_files.zip",
            as_attachment=True,
            mimetype='application/zip'
        )
    return render_template("index.html")
