from flask import Flask, render_template, request, send_file
import zipfile
import io
import re
import os # Import the os module for filename manipulation

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
    timestamp_pattern = re.compile(r'\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}')
    is_subtitle_text = False
    for line in content.splitlines():
        if timestamp_pattern.search(line):
            is_subtitle_text = True
            processed_lines.append(line)
        elif line.strip() == '':
            is_subtitle_text = False
            processed_lines.append(line)
        elif is_subtitle_text:
            temp_text = line.replace(U202B, '')
            processed_lines.append(U202B + temp_text)
        else:
            processed_lines.append(line)
    return '\n'.join(processed_lines)

def process_file_content(filename, content):
    """Determines file type and applies the correct fix."""
    filename_lower = filename.lower()
    if filename_lower.endswith('.ass'):
        return fix_ass_file(content)
    elif filename_lower.endswith('.srt'):
        return fix_srt_file(content)
    return content

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        files = request.files.getlist("files")
        if not files:
            return "No files uploaded", 400

        # --- NEW LOGIC: Handle single vs. multiple files ---

        if len(files) == 1:
            # --- SINGLE FILE LOGIC ---
            f = files[0]
            try:
                content = f.read().decode('utf-8-sig')
            except UnicodeDecodeError:
                f.seek(0)
                content = f.read().decode('utf-16')
            
            fixed_content = process_file_content(f.filename, content)
            
            # Create the new filename with _fixed suffix
            basename, extension = os.path.splitext(f.filename)
            new_filename = f"{basename}_fixed{extension}"

            memory_file = io.BytesIO(fixed_content.encode('utf-8'))
            memory_file.seek(0)

            return send_file(
                memory_file,
                download_name=new_filename,
                as_attachment=True
            )
        else:
            # --- MULTIPLE FILES LOGIC (ZIP) ---
            memory_file = io.BytesIO()
            with zipfile.ZipFile(memory_file, 'w') as zf:
                for f in files:
                    try:
                        content = f.read().decode('utf-8-sig')
                    except UnicodeDecodeError:
                        f.seek(0)
                        content = f.read().decode('utf-16')
                    
                    fixed_content = process_file_content(f.filename, content)
                    zf.writestr(f.filename, fixed_content.encode('utf-8'))
            
            memory_file.seek(0)
            return send_file(
                memory_file,
                download_name="fixed_files.zip",
                as_attachment=True,
                mimetype='application/zip'
            )

    return render_template("index.html")
